// Payment state machine (Phase 4 contract).
//
//   PAYMENT_IDLE
//     → PAYMENT_CREATING_ORDER   POST /payments/orders (server-priced only)
//     → PAYMENT_READY            order exists; settlement request next
//     → PAYMENT_PROCESSING       POST /bookings/{ref}/confirm (mock settles
//                                synchronously; Razorpay returns 502 and
//                                settles via webhook instead)
//     → PAYMENT_RECONCILING      GET /payments/status/{ref} polled with
//                                backoff — the ONLY source of truth
//     → PAYMENT_SUCCESS | PAYMENT_FAILED | PAYMENT_EXPIRED
//
// CRITICAL: PAYMENT_SUCCESS is only ever entered because the *backend*
// reported booking CONFIRMED. Browser callbacks, provider redirects or local
// heuristics never confirm a booking. Network failure during payment moves
// the state to RECONCILING, never to FAILED.

import {
  confirmBooking, createPaymentOrder, fetchBooking, fetchPaymentStatus,
} from '../api.js';
import { friendlyMessage } from '../core/errors.js';

export const PAYMENT_STATE = Object.freeze({
  IDLE: 'PAYMENT_IDLE',
  CREATING_ORDER: 'PAYMENT_CREATING_ORDER',
  READY: 'PAYMENT_READY',
  PROCESSING: 'PAYMENT_PROCESSING',
  RECONCILING: 'PAYMENT_RECONCILING',
  SUCCESS: 'PAYMENT_SUCCESS',
  FAILED: 'PAYMENT_FAILED',
  EXPIRED: 'PAYMENT_EXPIRED',
});

const STATUS_MESSAGES = {
  [PAYMENT_STATE.IDLE]: '',
  [PAYMENT_STATE.CREATING_ORDER]: 'Preparing your payment order…',
  [PAYMENT_STATE.READY]: 'Payment order ready. Confirming with the cinema server…',
  [PAYMENT_STATE.PROCESSING]: 'Payment received. Confirming your booking…',
  [PAYMENT_STATE.RECONCILING]: 'Connection lost. Checking booking status…',
  [PAYMENT_STATE.SUCCESS]: 'Booking confirmed.',
  [PAYMENT_STATE.FAILED]: 'Payment could not be completed.',
  [PAYMENT_STATE.EXPIRED]: 'Your seat hold expired.',
};

let current = PAYMENT_STATE.IDLE;
const listeners = new Set();

export function getPaymentState() { return current; }

export function onPaymentStateChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function setState(next, note = '') {
  if (current === next && !note) return;
  current = next;
  const message = note || STATUS_MESSAGES[next] || '';
  for (const fn of listeners) {
    try { fn(next, message); } catch { /* subscriber errors stay isolated */ }
  }
}

export function resetPaymentState() { setState(PAYMENT_STATE.IDLE); }

export function isCheckoutActive() {
  return current === PAYMENT_STATE.CREATING_ORDER
    || current === PAYMENT_STATE.PROCESSING
    || current === PAYMENT_STATE.RECONCILING;
}

// Reconciliation schedule after an ambiguous outcome (ms). Bounded — after
// the last attempt the caller can press "Check status" to run one more pass.
const RECONCILE_DELAYS = [0, 1000, 2000, 4000, 7000, 11000];

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Ask the backend where a reference stands — one pass.
 * Returns an outcome; never guesses.
 */
export async function checkPaymentStatus(reference) {
  let status;
  try {
    status = await fetchPaymentStatus(reference, { timeout: 10000 });
  } catch (error) {
    const networkish = error?.code === 'TIMEOUT' || error?.code === 'NETWORK'
      || error?.code === 'OFFLINE' || error?.status === 0;
    if (networkish) {
      return outcome(PAYMENT_STATE.RECONCILING, {
        unresolved: true, message: 'Still unable to reach the server. Your booking reference is safe.',
      });
    }
    if (error.status === 404 || error.status === 409) {
      return outcome(PAYMENT_STATE.EXPIRED, { message: friendlyMessage(error) });
    }
    if (error.status === 429) {
      return outcome(PAYMENT_STATE.RECONCILING, {
        unresolved: true, retryAfter: error.retryAfter, message: friendlyMessage(error),
      });
    }
    return outcome(PAYMENT_STATE.FAILED, { message: friendlyMessage(error), retryable: true });
  }

  const bookingStatus = status.booking_status;
  const paymentStatus = status.payment_status;
  if (bookingStatus === 'CONFIRMED') {
    // Fetch the full booking for the success screen (seats/total/reference).
    let booking = null;
    try { booking = await fetchBooking(reference, { timeout: 10000 }); } catch { /* status already proves it */ }
    return outcome(PAYMENT_STATE.SUCCESS, { booking, status });
  }
  if (bookingStatus === 'CANCELLED') {
    return outcome(PAYMENT_STATE.EXPIRED, { status, message: 'This booking was cancelled. Please choose your seats again.' });
  }
  if (paymentStatus === 'FAILED') {
    return outcome(PAYMENT_STATE.FAILED, {
      status, retryable: true,
      message: 'Payment was declined. Your seats are still held — please try again.',
    });
  }
  // PENDING/HELD — money not settled yet (or webhook mid-flight): keep checking.
  return outcome(PAYMENT_STATE.RECONCILING, { unresolved: true, status });
}

async function reconcile(reference) {
  for (const delay of RECONCILE_DELAYS) {
    if (delay) await sleep(delay);
    setState(PAYMENT_STATE.RECONCILING);
    const result = await checkPaymentStatus(reference);
    if (!result.unresolved) return result;
  }
  return outcome(PAYMENT_STATE.RECONCILING, {
    unresolved: true,
    message: 'Still verifying your payment. Your booking reference is safe — use “Check status” to try again.',
  });
}

/**
 * Full checkout: order → settlement → (reconcile on ambiguity).
 * Returns an outcome object; the caller renders UI from it.
 */
export async function runCheckout(reference, paymentMethod) {
  // 1. Create (or reuse) the server-priced order. The client never sends an
  //    amount, currency, provider id or status.
  setState(PAYMENT_STATE.CREATING_ORDER);
  try {
    await createPaymentOrder(reference);
  } catch (error) {
    const networkish = error.code === 'TIMEOUT' || error.code === 'NETWORK'
      || error.code === 'OFFLINE' || error.status === 0;
    if (networkish || error.status >= 500) {
      // Order may exist server-side — reconcile instead of failing.
      return reconcile(reference);
    }
    if (error.status === 409) {
      setState(PAYMENT_STATE.EXPIRED);
      return outcome(PAYMENT_STATE.EXPIRED, { message: friendlyMessage(error) });
    }
    setState(PAYMENT_STATE.FAILED);
    return outcome(PAYMENT_STATE.FAILED, { retryable: error.status === 429, message: friendlyMessage(error) });
  }

  // 2. Settlement. Mock settles synchronously; Razorpay answers 502
  //    PaymentUnavailable (webhook settles later) — both reconcile below.
  setState(PAYMENT_STATE.PROCESSING);
  try {
    const booking = await confirmBooking(reference, paymentMethod);
    if (booking && booking.status === 'CONFIRMED') {
      setState(PAYMENT_STATE.SUCCESS);
      return outcome(PAYMENT_STATE.SUCCESS, { booking });
    }
    // Defensive: a non-CONFIRMED response is not success — check the truth.
    return reconcile(reference);
  } catch (error) {
    if (error.status === 402) {
      setState(PAYMENT_STATE.FAILED);
      return outcome(PAYMENT_STATE.FAILED, { retryable: true, message: friendlyMessage(error) });
    }
    if (error.status === 409 && /expired/i.test(error.rawMessage || '')) {
      setState(PAYMENT_STATE.EXPIRED);
      return outcome(PAYMENT_STATE.EXPIRED, { message: friendlyMessage(error) });
    }
    if (error.status === 409 && /email already registered/i.test(error.rawMessage || '')) {
      setState(PAYMENT_STATE.FAILED);
      return outcome(PAYMENT_STATE.FAILED, { retryable: false, message: friendlyMessage(error) });
    }
    // Network drop, 429, other 409s, 5xx/502: the outcome is genuinely
    // unknown — NEVER assume failure. Reconcile against the backend.
    return reconcile(reference);
  }
}


function outcome(state, extra = {}) {
  return { state, message: STATUS_MESSAGES[state] || '', ...extra };
}
