// Shared E2E helpers. Everything runs against the real FastAPI server started
// by playwright.config.mjs, which uses an isolated SQLite database and MOCK
// payments. No real provider, no real charge, no production data.

import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { expect, request as pwRequest } from '@playwright/test';

// NOTE: never use `import.meta` here. package.json has no "type": "module", so
// Playwright transpiles this file to CJS; an `import.meta` left in the output
// makes Node's syntax detection re-run the file as an ES module, where the
// transpiled require/exports then throw "exports is not defined in ES module
// scope". The CJS pipeline always provides __dirname for ./. files here.
const REPO_ROOT = path.resolve(__dirname, '..');
const BACKEND_DIR = path.join(REPO_ROOT, 'backend');

// The webServer (e2e/e2e-server.cjs) starts uvicorn with cwd=backend, so a
// relative sqlite:///./ URL in the config resolves there.
function e2eDatabaseUrl() {
  return process.env.E2E_DATABASE_URL || 'sqlite:///./dhurandhar_e2e_test.db';
}

function e2eDatabasePath() {
  const match = /^sqlite:\/\/\/(.*)$/.exec(e2eDatabaseUrl());
  if (!match) throw new Error(`E2E helpers only support sqlite URLs, got: ${e2eDatabaseUrl()}`);
  const raw = match[1];
  return path.isAbsolute(raw) ? raw : path.join(BACKEND_DIR, raw);
}

function backendPython() {
  // CI sets E2E_PYTHON to the runner's interpreter (no venv on disk there).
  if (process.env.E2E_PYTHON) return process.env.E2E_PYTHON;
  const candidates = process.platform === 'win32'
    ? [path.join(BACKEND_DIR, '.venv', 'Scripts', 'python.exe'), 'python']
    : [path.join(BACKEND_DIR, '.venv', 'bin', 'python3'), 'python3', 'python'];
  for (const candidate of candidates) {
    if (candidate === 'python' || candidate === 'python3' || existsSync(candidate)) return candidate;
  }
  return 'python';
}

/**
 * Populate the isolated E2E database with the reference catalog (cities,
 * theaters, screens, seats, 7 days of shows). Idempotent — safe to call from
 * every spec file's beforeAll. Runs backend/seed.py against the same
 * DATABASE_URL the webServer uses; never touches development/production data.
 */
export function seedCatalog() {
  execFileSync(backendPython(), ['seed.py'], {
    cwd: BACKEND_DIR,
    stdio: 'pipe',
    env: {
      ...process.env,
      DATABASE_URL: e2eDatabaseUrl(),
      JWT_SECRET: process.env.E2E_JWT_SECRET || 'e2e-only-secret-not-used-in-production-000000',
      APP_ENV: 'development',
      PAYMENT_MODE: 'mock',
    },
  });
}

/**
 * Flip a registered account to role=ADMIN directly in the isolated SQLite DB.
 * Roles are DB-authoritative (the JWT never carries role), which is exactly
 * how backend/admin_bootstrap.py promotes operators — this is the E2E twin of
 * that script, scoped to the test database only.
 */
export function promoteToAdmin(email) {
  const db = new DatabaseSync(e2eDatabasePath());
  try {
    db.prepare('UPDATE users SET role = ? WHERE email = ?').run('ADMIN', String(email).toLowerCase());
  } finally {
    db.close();
  }
}

/**
 * Register → promote → login, so the returned user object carries role=ADMIN
 * exactly as a real operator's session would.
 */
export async function createAdminSession(baseURL, prefix = 'e2e-admin') {
  const registered = await registerUser(baseURL, prefix);
  promoteToAdmin(registered.user.email);
  const api = await pwRequest.newContext({ baseURL });
  const login = await api.post('/api/auth/login', {
    data: { email: registered.user.email, password: registered.user.password },
  });
  expect(login.status(), await login.text()).toBe(200);
  const body = await login.json();
  expect(body.user.role, 'login response must carry the promoted role').toBe('ADMIN');
  return { token: body.access_token, account: body.user, api };
}

let seq = 0;
/** Unique, schema-valid user payload for a fresh test account. */
export function makeUser(prefix = 'e2e') {
  seq += 1;
  const suffix = `${Date.now().toString(36)}${seq}${Math.random().toString(36).slice(2, 7)}`;
  return {
    full_name: `${prefix} E2E User`,
    email: `${prefix}-${suffix}@example.com`,
    phone: '9876543210',
    password: 'Test@1234',
  };
}

/** Register a user through the API and return { user, token, api }. */
export async function registerUser(baseURL, prefix = 'e2e') {
  const api = await pwRequest.newContext({ baseURL });
  const user = makeUser(prefix);
  const res = await api.post('/api/auth/register', { data: user });
  expect(res.status(), await res.text()).toBe(201);
  const body = await res.json();
  return { user, token: body.access_token, account: body.user, api };
}

/** Log in via the API and return the token. */
export async function loginUser(api, user) {
  const res = await api.post('/api/auth/login', {
    data: { email: user.email, password: user.password },
  });
  expect(res.status(), await res.text()).toBe(200);
  return (await res.json()).access_token;
}

/** Seed an authenticated browser session (localStorage) and load the page. */
export async function loginInBrowser(page, { token, account }) {
  // Session is loaded once at JS boot (core/state.js), so seed storage first,
  // then load — a goto()+evaluate()+reload() can land before modules execute
  // and wipe the half-written pair via loadInitial's signed-out cleanup.
  await page.addInitScript(
    ({ token, account }) => {
      localStorage.setItem('dhurandhar_token', token);
      localStorage.setItem('dhurandhar_user', JSON.stringify(account));
    },
    { token, account },
  );
  await page.goto('/');
}

/** Open the booking modal from the header. */
export async function openBooking(page) {
  await page.getByRole('button', { name: 'Book Tickets' }).click();
  await expect(page.locator('#bookingModal')).toBeVisible();
}

/** Assert there were no uncaught console errors during the test. */
export function watchConsoleErrors(page) {
  const errors = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') errors.push(msg.text());
  });
  page.on('pageerror', (err) => errors.push(String(err)));
  return errors;
}
