// Shared E2E helpers. Everything runs against the real FastAPI server started
// by playwright.config.mjs, which uses an isolated SQLite database and MOCK
// payments. No real provider, no real charge, no production data.

import { expect, request as pwRequest } from '@playwright/test';

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
