// The single door every frontend API request goes through.
//
// Responsibilities:
//   - base URL (/api) + JSON serialization/parsing
//   - request timeout via AbortController (long-running callers can override)
//   - caller-supplied signal composition (race-prone loaders cancel each other)
//   - non-2xx + network-failure + timeout normalization into ApiError
//     { status, code, message, rawMessage, details, retryAfter }
//   - optional auth header (Bearer token from the session store)
//   - one-time 401 handling via an `onUnauthorized` hook (registered by
//     features/auth.js) so expired JWTs clear local state and protected
//     endpoints are not hammered after the session dies
//
// Errors are never swallowed: everything throws ApiError. Callers translate
// with friendlyMessage() from core/errors.js for display.

import { friendlyMessage } from './errors.js';
import { getSession } from './state.js';

const API_BASE = '/api';
const DEFAULT_TIMEOUT_MS = 15000;

export class ApiError extends Error {
  constructor({ status = 0, code = '', details = null, retryAfter = null, rawMessage = '' }) {
    super(rawMessage || 'Request failed');
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;
    this.retryAfter = retryAfter;
    this.rawMessage = rawMessage;
  }
}

let unauthorizedHandler = null;
/** Registered once by features/auth.js; called at most once per dead session. */
export function onUnauthorized(fn) { unauthorizedHandler = fn; }

function extractMessage(data, status) {
  const detail = data && typeof data === 'object' ? data.detail : undefined;
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object') {
    if (typeof detail.message === 'string') return detail.message;
    // Pydantic validation errors: keep it short and generic; never echo values.
    if (Array.isArray(detail)) return '';
  }
  if (data && typeof data === 'object' && typeof data.message === 'string') return data.message;
  return status ? `Request failed (${status})` : 'Request failed';
}

function extractDetails(data) {
  const detail = data && typeof data === 'object' ? data.detail : undefined;
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) return detail;
  return null;
}

function parseRetryAfter(response) {
  const header = response.headers.get('Retry-After');
  if (!header) return null;
  const seconds = Number(header);
  if (Number.isFinite(seconds)) return seconds;
  const at = Date.parse(header);
  return Number.isNaN(at) ? null : Math.max(0, (at - Date.now()) / 1000);
}

function buildError(status, data, retryAfter, code) {
  const rawMessage = extractMessage(data, status);
  const error = new ApiError({
    status, code: code || String(status), details: extractDetails(data), retryAfter, rawMessage,
  });
  error.message = friendlyMessage(error);
  return error;
}

/**
 * Perform an API request.
 * @param {string} path        — path under /api (e.g. '/bookings/hold')
 * @param {object} options
 *   method, body (object → JSON), headers, token (override auth header),
 *   auth (false → never attach Authorization),
 *   timeout (ms, 0 → no client timeout), signal (caller cancellation)
 */
export async function request(path, options = {}) {
  const {
    method = 'GET',
    body,
    headers = {},
    timeout = DEFAULT_TIMEOUT_MS,
    signal: outerSignal,
    auth = true,
    token,
  } = options;

  if (typeof navigator !== 'undefined' && navigator.onLine === false) {
    const offline = buildError(0, null, null, 'OFFLINE');
    throw offline;
  }

  const controller = new AbortController();
  let timedOut = false;
  let timer = null;
  if (timeout > 0) {
    timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeout);
  }
  const onOuterAbort = () => controller.abort();
  if (outerSignal) {
    if (outerSignal.aborted) controller.abort();
    else outerSignal.addEventListener('abort', onOuterAbort, { once: true });
  }

  const finalHeaders = { 'Content-Type': 'application/json', ...headers };
  const effectiveToken = token !== undefined ? token : (auth ? getSession().token : null);
  if (auth && effectiveToken) finalHeaders.Authorization = `Bearer ${effectiveToken}`;

  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      headers: finalHeaders,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: controller.signal,
      credentials: 'same-origin',
    });
  } catch (err) {
    if (timedOut) throw buildError(0, null, null, 'TIMEOUT');
    if (outerSignal?.aborted) {
      // Caller cancelled deliberately (stale request) — propagate the abort so
      // callers can distinguish "obsolete" from "failed".
      throw err;
    }
    throw buildError(0, null, null, 'NETWORK');
  } finally {
    if (timer) clearTimeout(timer);
    if (outerSignal) outerSignal.removeEventListener('abort', onOuterAbort);
  }

  // Safe response parsing: an empty/non-JSON body is not an error for 204s.
  let data = null;
  const text = await response.text().catch(() => '');
  if (text) {
    try { data = JSON.parse(text); } catch {
      if (response.ok) throw buildError(response.status, null, null, 'PARSE');
      data = null;
    }
  }

  if (!response.ok) {
    const error = buildError(response.status, data, parseRetryAfter(response));
    // Only a request that carried a credential can mean "session expired" —
    // a 401 from /auth/login (no token attached) never triggers the hook.
    if (response.status === 401 && finalHeaders.Authorization && unauthorizedHandler) {
      unauthorizedHandler(error);
    }
    throw error;
  }

  return data;
}

export const get = (path, options) => request(path, { ...options, method: 'GET' });
export const post = (path, body, options) => request(path, { ...options, method: 'POST', body });
