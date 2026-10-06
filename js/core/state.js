// Session/auth state — the only cross-feature state in the app.
//
// Booking flow state (city/show/seats/hold/payment) stays module-scoped
// inside features/booking.js + features/payments.js where it belongs; this
// store only holds what multiple features genuinely share: who is signed in.
//
// Storage decision (documented in README, Phase 5): the backend issues plain
// HS256 bearer tokens with no refresh/cookie mechanism, so the token lives in
// localStorage under the same keys the app has always used
// (`dhurandhar_token` / `dhurandhar_user`). Risks: an XSS could read it —
// mitigated by CSP `script-src 'self'`, zero inline scripts/handlers, and
// escaping all dynamic DOM. Benefits: survives reloads (a lost token would
// silently sign users out mid-booking). Migrating to httpOnly cookies would
// require a backend change (out of scope for Phase 5).

import { readString, readJSON, writeString, writeJSON, remove } from './storage.js';

const TOKEN_KEY = 'dhurandhar_token';
const USER_KEY = 'dhurandhar_user';

function normalizeUser(raw) {
  if (!raw || typeof raw !== 'object') return null;
  if (typeof raw.full_name !== 'string' || typeof raw.email !== 'string') return null;
  return raw;
}

function loadInitial() {
  const token = readString(TOKEN_KEY);
  const user = normalizeUser(readJSON(USER_KEY));
  if (token && user) return { token, user };
  // Half-written state (user without token or vice versa, corrupted JSON) is
  // treated as signed-out so protected calls never fire with a broken pair.
  remove(TOKEN_KEY);
  remove(USER_KEY);
  return { token: null, user: null };
}

let session = loadInitial();

const listeners = new Set();

function emit() {
  for (const fn of listeners) {
    try { fn(session); } catch { /* a broken subscriber must not break others */ }
  }
}

export function getSession() { return session; }

export function isAuthenticated() { return Boolean(session.token); }

export function setSession(token, user) {
  session = { token, user: normalizeUser(user) || null };
  if (session.token) writeString(TOKEN_KEY, session.token); else remove(TOKEN_KEY);
  if (session.user) writeJSON(USER_KEY, session.user); else remove(USER_KEY);
  emit();
}

export function clearSession() {
  if (!session.token && !session.user) return; // idempotent: no repeat work on double-401
  session = { token: null, user: null };
  remove(TOKEN_KEY);
  remove(USER_KEY);
  emit();
}

/** Subscribe to session changes. Returns an unsubscribe function. */
export function onSessionChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
