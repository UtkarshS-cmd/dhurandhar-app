// Booking feature — the ticket wizard: details → theater/show → seats →
// confirm. Responsibilities are separated:
//   seat state machine ......... ui/seats.js
//   payment machine + reconcile  features/payments.js
//   network .................... core/api-client.js (via js/api.js)
//   double-submit guards ........ ui/loading.js
//   this module: orchestration + wizard rendering only.
//
// The server price is authoritative: the browser only *displays* an estimate
// while selecting; totals shown at confirm come from the hold response.

import {
  fetchBooking, fetchCities, fetchDates, fetchShows, fetchSeats, fetchTheaters, createHold,
} from '../api.js';
import { friendlyMessage } from '../core/errors.js';
import { $, el, option } from '../ui/dom.js';
import { registerActions } from '../ui/actions.js';
import { isBusy, runExclusive } from '../ui/loading.js';
import { closeModal, isModalOpen, openModal } from '../ui/modal.js';
import { toast } from '../ui/notifications.js';
import {
  MAX_SEATS_PER_BOOKING, SEAT_STATE, capSelection, classify, reconcileSelection, seatLabel,
  stateDescription,
} from '../ui/seats.js';
import {
  PAYMENT_STATE, checkPaymentStatus, getPaymentState, isCheckoutActive, resetPaymentState, runCheckout,
} from './payments.js';

const PENDING_REF_KEY = 'dhurandhar_pending_booking';

const state = {
  step: 1,
  cityId: null,
  theaterId: null,
  showId: null,
  selectedTime: '',
  seats: [],
  seatsShowId: null, // seats fetched for which show (stale-show guard)
  selectedSeatIds: new Set(),
  ticketCount: 2,
  category: 'Silver',
  hold: null,
  lastFocused: null,
};

let initDone = false;

// -- request sequencing: newest request wins, obsolete ones are aborted ----
const seq = { step2: 0, theaters: 0, shows: 0, seats: 0 };
const ctrl = { step2: null, theaters: null, shows: null, seats: null };

function begin(key) {
  ctrl[key]?.abort();
  const controller = new AbortController();
  ctrl[key] = controller;
  seq[key] += 1;
  return { signal: controller.signal, id: seq[key] };
}

function stale(key, id) { return seq[key] !== id; }

function abortAllRequests() {
  for (const key of Object.keys(ctrl)) ctrl[key]?.abort();
}

const isAbort = (error) => error?.name === 'AbortError';

// -- small UI helpers -------------------------------------------------------
function setStatus(message = '', type = '') {
  let box = $('booking-status');
  if (!box) {
    box = document.createElement('div');
    box.id = 'booking-status';
    box.setAttribute('role', 'status');
    $('modalBox')?.prepend(box);
  }
  box.className = `booking-status ${type}`.trim();
  box.textContent = message; // textContent: backend copy never parsed as HTML
}

function asyncState(text, isError = false) {
  const box = document.createElement('div');
  box.className = isError ? 'async-state error' : 'async-state';
  box.textContent = text;
  return box;
}

function goStep(step) {
  state.step = step;
  for (let i = 1; i <= 5; i++) $(`mpanel${i}`)?.classList.toggle('active', i === step);
  for (let i = 1; i <= 4; i++) {
    const tab = $(`stab${i}`);
    tab?.classList.toggle('active', i === step);
    tab?.classList.toggle('done', i < step);
  }
  $('modalBox').scrollTop = 0;
}

function setErr(rowId, inputId, ok, message) {
  const row = $(rowId);
  const input = $(inputId);
  if (!row || !input) return;
  row.classList.toggle('has-error', !ok);
  input.classList.toggle('error', !ok);
  input.classList.toggle('valid', ok);
  const msg = row.querySelector('.err-msg');
  if (msg && message) msg.textContent = message;
}

// -- step 2: cities + dates -------------------------------------------------
async function loadStep2Data() {
  const city = $('f-city');
  const date = $('f-date');
  if (!city || !date) return;
  const { signal, id } = begin('step2');
  city.replaceChildren(option('', 'Loading cities…'));
  date.replaceChildren(option('', 'Loading dates…'));
  try {
    const [cities, dates] = await Promise.all([fetchCities({ signal }), fetchDates({ signal })]);
    if (stale('step2', id)) return;
    city.replaceChildren(option('', '— Choose your city —'), ...cities.map((c) => option(c.id, c.name)));
    date.replaceChildren(option('', '— Select date —'), ...dates.map((d) => option(d.value, d.label)));
  } catch (error) {
    if (isAbort(error) || stale('step2', id)) return;
    city.replaceChildren(option('', 'Backend unavailable'));
    date.replaceChildren(option('', 'Backend unavailable'));
    setStatus(friendlyMessage(error), 'error');
  }
}

// Is the catalog actually loaded (vs loading/unavailable placeholder)?
function catalogLoaded() {
  return ($('f-city')?.options.length || 0) > 1 && ($('f-date')?.options.length || 0) > 1;
}

// -- step 2: theaters + shows ----------------------------------------------
async function updateTheaters() {
  state.cityId = Number($('f-city')?.value) || null;
  state.theaterId = null;
  state.showId = null;
  state.selectedTime = '';
  state.seats = [];
  state.seatsShowId = null;
  state.selectedSeatIds.clear();
  const grid = $('theaterGrid');
  if (!grid) return;
  const { signal, id } = begin('theaters');
  begin('shows'); // any in-flight show loads belong to the old city now
  grid.replaceChildren(asyncState('Loading theaters…'));
  if (!state.cityId) {
    grid.replaceChildren(asyncState('Select a city to see theaters.'));
    return;
  }
  try {
    const theaters = await fetchTheaters(state.cityId, { signal });
    if (stale('theaters', id)) return;
    if (!theaters.length) {
      grid.replaceChildren(asyncState('No theaters are available in this city.'));
      return;
    }
    grid.replaceChildren(...theaters.map((theater) => el('div', {
      class: 'theater-card', tabindex: '0', 'data-id': theater.id,
    }, [
      el('span', { class: 'theater-radio', 'aria-hidden': 'true' }),
      el('div', {}, [
        el('div', { class: 'theater-name', text: theater.name }),
        el('div', { class: 'theater-addr', text: theater.address }),
      ]),
      el('div', { class: 'theater-shows', 'data-show-list': '' }),
    ])));
    const cards = [...grid.querySelectorAll('.theater-card')];
    const showsSignal = ctrl.shows?.signal;
    await Promise.all(theaters.map((theater, i) => loadShowsIntoCard(theater, cards[i], showsSignal)));
  } catch (error) {
    if (isAbort(error) || stale('theaters', id)) return;
    grid.replaceChildren(asyncState(friendlyMessage(error), true));
  }
}

async function loadShowsIntoCard(theater, card, signal) {
  const list = card?.querySelector('[data-show-list]');
  if (!list) return;
  const date = $('f-date')?.value;
  if (!date) { list.textContent = 'Select a date'; return; }
  try {
    const shows = await fetchShows(theater.id, date, { signal });
    if (signal?.aborted) return;
    if (!shows.length) { list.textContent = 'No shows'; return; }
    list.replaceChildren(...shows.map((show) => el('button', {
      type: 'button',
      class: 'show-time',
      'data-show-id': show.id,
      'aria-pressed': 'false',
      text: show.time.slice(0, 5),
    })));
  } catch (error) {
    if (isAbort(error)) return;
    list.textContent = 'Unable to load shows';
  }
}

function refreshTheatersForDate() {
  if (state.cityId) updateTheaters();
}

function selectTheater(card) {
  document.querySelectorAll('.theater-card').forEach((c) => {
    c.classList.remove('selected');
    c.setAttribute('aria-pressed', 'false');
  });
  card.classList.add('selected');
  card.setAttribute('aria-pressed', 'true');
  state.theaterId = Number(card.dataset.id) || null;
  state.showId = null;
  state.selectedTime = '';
  document.querySelectorAll('.show-time').forEach((b) => {
    b.classList.remove('selected-time');
    b.setAttribute('aria-pressed', 'false');
  });
}

function selectShow(button) {
  const card = button.closest('.theater-card');
  if (!card) return;
  selectTheater(card);
  state.showId = Number(button.dataset.showId) || null;
  state.selectedTime = button.textContent.trim();
  button.classList.add('selected-time');
  button.setAttribute('aria-pressed', 'true');
}

// -- step 1: guest details --------------------------------------------------
function validateStep1() {
  const name = $('f-name').value.trim();
  const email = $('f-email').value.trim();
  const phone = $('f-phone').value.trim();
  const pwd = $('f-pwd').value;
  const cpwd = $('f-cpwd').value;
  const validPwd = /^(?=.*[A-Za-z])(?=.*\d)(?=.*[^A-Za-z\d]).{8,}$/;
  const validName = name.length >= 3;
  const validEmail = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
  const validPhone = /^[6-9]\d{9}$/.test(phone.replace(/\D/g, ''));
  const validPwdOk = validPwd.test(pwd);
  setErr('fr-name', 'f-name', validName, 'Please enter your full name (min 3 characters)');
  setErr('fr-email', 'f-email', validEmail, 'Please enter a valid email address');
  setErr('fr-phone', 'f-phone', validPhone, 'Please enter a valid 10-digit mobile number');
  setErr('fr-pwd', 'f-pwd', validPwdOk, 'Use 8+ characters with letters, numbers and a special character');
  setErr('fr-cpwd', 'f-cpwd', pwd === cpwd && pwd.length > 0, 'Passwords do not match');
  if (!(validName && validEmail && validPhone && validPwdOk && pwd === cpwd)) return;
  goStep(2);
}

// -- step 2 validation ------------------------------------------------------
function validateStep2() {
  const city = $('f-city').value;
  const date = $('f-date').value;
  setErr('fr-city', 'f-city', !!city, 'Please select a city');
  setErr('fr-date', 'f-date', !!date, 'Please select a date');
  const err = $('theater-err');
  if (!city || !date || !state.theaterId || !state.showId) {
    err.style.display = 'block';
    err.textContent = 'Please select a theater and show time';
    return;
  }
  err.style.display = 'none';
  state.selectedSeatIds.clear();
  goStep(3);
  loadSeats();
}

// -- step 3: seats ----------------------------------------------------------
async function loadSeats() {
  if (!state.showId) return;
  const showId = state.showId;
  const { signal, id } = begin('seats');
  const groups = ['gold-seats', 'silver-seats', 'bronze-seats'];
  groups.forEach((gid) => $(gid)?.replaceChildren(asyncState('Loading seats…')));
  try {
    const seats = await fetchSeats(showId, { signal });
    // Stale-response guards: a newer request owns the UI, or the user has
    // moved to a different show while this response was in flight.
    if (stale('seats', id) || state.showId !== showId) return;
    // Reconcile local selection against server truth BEFORE rendering:
    // seats that became unavailable are dropped and the user is told.
    const { selected, removed } = reconcileSelection(seats, state.selectedSeatIds);
    state.selectedSeatIds = selected;
    state.seats = seats;
    state.seatsShowId = showId;
    renderSeats();
    updatePrice();
    if (removed.length) {
      setStatus(
        `${removed.length} selected seat${removed.length === 1 ? ' is' : 's are'} no longer available: `
        + `${removed.map(seatLabel).join(', ')}. Please choose again.`,
        'error',
      );
    }
  } catch (error) {
    if (isAbort(error) || stale('seats', id)) return;
    groups.forEach((gid) => $(gid)?.replaceChildren(asyncState(friendlyMessage(error), true)));
  }
}

function renderSeats() {
  const groups = { Gold: $('gold-seats'), Silver: $('silver-seats'), Bronze: $('bronze-seats') };
  Object.values(groups).forEach((g) => g?.replaceChildren());
  // Never render seats fetched for a different show.
  if (state.seatsShowId !== state.showId) return;
  for (const seat of state.seats) {
    const seatState = classify(seat, state.selectedSeatIds);
    const unavailable = seatState !== SEAT_STATE.AVAILABLE && seatState !== SEAT_STATE.SELECTED;
    const wrongCategory = seat.category !== state.category;
    const selected = seatState === SEAT_STATE.SELECTED;
    const button = el('button', {
      type: 'button',
      class: ['seat', unavailable ? 'taken' : '', selected ? 'selected-seat' : '', wrongCategory ? 'not-category' : '']
        .filter(Boolean).join(' '),
      'data-id': seat.id,
      // Non-color state signalling for assistive tech (also mirrored by the
      // .taken strikethrough style for sighted users).
      'aria-pressed': selected ? 'true' : 'false',
      'aria-label': `${seat.row}${seat.number}, ${stateDescription(seatState, state.selectedSeatIds, seat)}`
        + (wrongCategory && !unavailable ? ', different category' : ''),
      text: `${seat.row}${seat.number}`,
    });
    button.disabled = unavailable || wrongCategory;
    groups[seat.category]?.appendChild(button);
  }
}

function toggleSeat(seatId) {
  const seat = state.seats.find((s) => s.id === seatId);
  if (!seat) return;
  const selected = state.selectedSeatIds.has(seatId);
  if (!selected) {
    // Server truth only: booked/held seats can never enter the selection.
    if (seat.status !== 'AVAILABLE' || seat.category !== state.category) return;
    if (state.selectedSeatIds.size >= Math.min(state.ticketCount, MAX_SEATS_PER_BOOKING)) {
      setStatus(`Select no more than ${state.ticketCount} ticket${state.ticketCount === 1 ? '' : 's'}.`, 'error');
      return;
    }
    state.selectedSeatIds.add(seatId);
  } else {
    state.selectedSeatIds.delete(seatId);
  }
  setStatus('');
  renderSeats();
  updatePrice();
}

function updateSeats() {
  state.ticketCount = Number($('f-tickets')?.value) || 1;
  const { selected, dropped } = capSelection(state.selectedSeatIds, state.ticketCount);
  state.selectedSeatIds = selected;
  if (dropped.length) {
    setStatus(`${dropped.length} seat${dropped.length === 1 ? '' : 's'} removed to match your ticket count.`, '');
  }
  renderSeats();
  updatePrice();
}

function onCategoryChange() {
  state.category = $('f-category')?.value || state.category;
  const before = state.selectedSeatIds.size;
  state.selectedSeatIds = new Set(
    [...state.selectedSeatIds].filter((id) => state.seats.find((s) => s.id === id)?.category === state.category),
  );
  const removed = before - state.selectedSeatIds.size;
  if (removed) {
    setStatus(`${removed} seat${removed === 1 ? '' : 's'} cleared — they belong to another category.`, '');
  }
  renderSeats();
  updatePrice();
}

/** Display estimate only — the hold response total is authoritative. */
function updatePrice() {
  const total = state.seats
    .filter((s) => state.selectedSeatIds.has(s.id))
    .reduce((sum, s) => sum + Number(s.price), 0);
  const label = $('live-price');
  if (label) label.textContent = `₹${total.toFixed(2)}`;
  return total;
}

// -- step 3 → hold ----------------------------------------------------------
async function validateStep3() {
  const err = $('seat-err');
  if (state.selectedSeatIds.size !== state.ticketCount) {
    err.style.display = 'block';
    err.textContent = `Please select exactly ${state.ticketCount} seat${state.ticketCount === 1 ? '' : 's'}.`;
    return;
  }
  if (state.selectedSeatIds.size > MAX_SEATS_PER_BOOKING) {
    err.style.display = 'block';
    err.textContent = `You can book at most ${MAX_SEATS_PER_BOOKING} seats.`;
    return;
  }
  err.style.display = 'none';
  const button = document.querySelector('[data-action="booking-step3"]');
  await runExclusive(button, 'Holding seats…', async () => {
    setStatus('Temporarily holding your selected seats…', 'loading');
    try {
      const hold = await createHold({
        user: {
          full_name: $('f-name').value.trim(),
          email: $('f-email').value.trim(),
          phone: $('f-phone').value.replace(/\D/g, ''),
          password: $('f-pwd').value,
        },
        show_id: state.showId,
        seat_ids: [...state.selectedSeatIds],
        payment_method: 'PENDING',
      });
      state.hold = hold;
      savePendingReference(hold.booking_reference);
      buildSummary(hold);
      setStatus('');
      goStep(4);
    } catch (error) {
      setStatus(friendlyMessage(error), 'error');
      if (error.status === 409 || error.status === 422) {
        // Conflict/validation: reconcile against fresh server state.
        const conflicted = error.details?.seat_ids;
        if (Array.isArray(conflicted)) {
          const conflictedSet = new Set(conflicted.map(Number));
          state.selectedSeatIds = new Set(
            [...state.selectedSeatIds].filter((id) => !conflictedSet.has(id)),
          );
        }
        loadSeats();
      }
    }
  });
}

// -- summary (step 4) -------------------------------------------------------
function buildSummary(hold) {
  const box = $('summaryBox');
  if (!box) return;
  const row = (label, value) => el('div', { class: 'summary-row' }, [
    el('span', { class: 's-label', text: label }),
    el('span', { class: 's-val', text: value }),
  ]);
  const recovered = hold.show_id !== state.showId; // resumed from a saved reference
  const selectedSeats = state.seats.filter((s) => state.selectedSeatIds.has(s.id));
  const seatsText = selectedSeats.length
    ? selectedSeats.map(seatLabel).join(', ')
    : (Array.isArray(hold.seats) ? hold.seats.join(', ') : '');
  const ticketCount = recovered
    ? (Array.isArray(hold.seats) ? hold.seats.length : state.ticketCount)
    : state.ticketCount;
  const parts = [];
  if (!recovered) {
    const city = $('f-city')?.selectedOptions[0]?.textContent || '';
    const theater = document.querySelector('.theater-card.selected .theater-name')?.textContent || '';
    const date = $('f-date')?.selectedOptions[0]?.textContent || '';
    if (city) parts.push(row('CITY', city));
    if (theater) parts.push(row('THEATER', theater));
    if (date) parts.push(row('DATE', date));
    if (state.selectedTime) parts.push(row('TIME', state.selectedTime));
  }
  parts.push(row('SEATS', seatsText));
  parts.push(row('TICKETS', String(ticketCount)));
  parts.push(el('div', { class: 'summary-row total' }, [
    el('span', { class: 's-label', text: 'SERVER TOTAL' }),
    el('span', { class: 's-val', text: `₹${Number(hold.total_amount).toFixed(2)}` }),
  ]));
  parts.push(el('div', {
    class: 'summary-note',
    text: 'Seats are held temporarily. Final confirmation happens only after the backend confirms the booking.',
  }));
  box.replaceChildren(...parts);
}

// -- pending-reference recovery (browser lost connection mid-payment) -------
function savePendingReference(reference) {
  try { sessionStorage.setItem(PENDING_REF_KEY, reference); } catch { /* private mode */ }
}

function readPendingReference() {
  try { return sessionStorage.getItem(PENDING_REF_KEY); } catch { return null; }
}

function clearPendingReference() {
  try { sessionStorage.removeItem(PENDING_REF_KEY); } catch { /* ignore */ }
}

function removeRecoveryNotice() {
  $('booking-recovery')?.remove();
}

function renderRecoveryNotice() {
  const reference = readPendingReference();
  if (!reference || state.hold?.booking_reference === reference) return;
  if ($('booking-recovery')) return;
  const box = el('div', { id: 'booking-recovery', class: 'booking-status loading', role: 'status' }, [
    el('span', { text: `You have an unfinished booking (${reference}). ` }),
    el('button', { type: 'button', class: 'recovery-btn', 'data-action': 'recover-booking', text: 'Check status' }),
    el('button', { type: 'button', class: 'recovery-btn', 'data-action': 'dismiss-recovery', text: 'Dismiss' }),
  ]);
  $('modalBox')?.prepend(box);
}

async function recoverBooking(event, target) {
  const reference = readPendingReference();
  if (!reference) { removeRecoveryNotice(); return; }
  await runExclusive(target, 'Checking…', async () => {
    try {
      const booking = await fetchBooking(reference);
      removeRecoveryNotice();
      clearPendingReference();
      if (booking.status === 'CONFIRMED') {
        showSuccess(booking);
        toast('Your previous booking is confirmed.', 'success');
      } else if (booking.status === 'HELD' && booking.hold_expires_at) {
        // Resume exactly where the user left off: summary + payment step.
        state.hold = booking;
        buildSummary(booking);
        resetPaymentState();
        goStep(4);
        setStatus('Resuming your held booking — complete payment to confirm.', 'loading');
      } else {
        setStatus('Your previous booking is no longer active. Please choose your seats again.', 'error');
      }
    } catch (error) {
      if (error.status === 404) {
        removeRecoveryNotice();
        clearPendingReference();
        setStatus(friendlyMessage(error), 'error');
      } else {
        setStatus(friendlyMessage(error), 'error');
      }
    }
  });
}

// -- payment (step 4) -------------------------------------------------------
function showSuccess(booking) {
  const reference = booking.booking_reference;
  $('bookingRef').textContent = reference;
  const sub = document.querySelector('#mpanel5 .success-sub');
  const seats = Array.isArray(booking.seats) ? booking.seats.join(', ') : '';
  if (sub) {
    sub.textContent = `Your seats ${seats} are confirmed. Booking total: ₹${Number(booking.total_amount).toFixed(2)}.`;
  }
  setStatus('');
  goStep(5);
}

function showCheckStatusControl(reference) {
  let box = $('payment-check');
  if (!box) {
    box = el('div', { id: 'payment-check', class: 'booking-status loading', role: 'status' }, [
      el('span', { id: 'payment-check-text', text: '' }),
      el('button', { type: 'button', class: 'recovery-btn', 'data-action': 'check-payment-status', text: 'Check status' }),
    ]);
    $('modalBox')?.prepend(box);
  }
  const text = $('payment-check-text');
  if (text) text.textContent = `Booking ${reference} is still being verified. `;
}

function removeCheckStatusControl() {
  $('payment-check')?.remove();
}

function forgetHold() {
  state.hold = null;
  clearPendingReference();
  resetPaymentState();
}

async function renderOutcome(outcome, reference) {
  switch (outcome.state) {
    case PAYMENT_STATE.SUCCESS: {
      clearPendingReference();
      removeCheckStatusControl();
      removeRecoveryNotice();
      showSuccess(outcome.booking || state.hold || { booking_reference: reference, seats: [], total_amount: 0 });
      break;
    }
    case PAYMENT_STATE.FAILED: {
      // The backend keeps the hold on a decline — the user can retry.
      removeCheckStatusControl();
      setStatus(outcome.message || 'Payment could not be completed. Your seats are still held — please try again.', 'error');
      break;
    }
    case PAYMENT_STATE.EXPIRED: {
      removeCheckStatusControl();
      forgetHold();
      setStatus(`${outcome.message} Please choose your seats again.`, 'error');
      toast('Your seat hold expired.', 'error');
      goStep(3);
      loadSeats();
      break;
    }
    default: {
      // PAYMENT_RECONCILING (unresolved): never claim failure, never claim
      // success — keep the reference visible and offer manual re-check.
      setStatus(outcome.message || 'Connection lost. Checking booking status…', 'loading');
      showCheckStatusControl(reference);
    }
  }
}

async function confirm() {
  if (!$('f-agree')?.checked) {
    $('agree-err').style.display = 'block';
    return;
  }
  $('agree-err').style.display = 'none';
  const reference = state.hold?.booking_reference;
  if (!reference) return;
  const button = document.querySelector('[data-action="confirm-booking"]');
  let outcome = null;
  await runExclusive(button, 'Confirming…', async () => {
    removeCheckStatusControl();
    setStatus('Payment received. Confirming your booking…', 'loading');
    outcome = await runCheckout(reference, $('f-payment').value);
    await renderOutcome(outcome, reference);
  });
  // Post-run button affordances (runExclusive restored the label already).
  if (outcome?.state === PAYMENT_STATE.FAILED && outcome.retryable) {
    button.textContent = 'Retry Payment';
  }
}

async function checkPaymentNow(event, target) {
  const reference = state.hold?.booking_reference || readPendingReference();
  if (!reference) { removeCheckStatusControl(); return; }
  await runExclusive(target, 'Checking…', async () => {
    const outcome = await checkPaymentStatus(reference);
    await renderOutcome(outcome, reference);
  });
}

// -- open / close / reset ---------------------------------------------------
function reset() {
  state.step = 1;
  state.cityId = null;
  state.theaterId = null;
  state.showId = null;
  state.selectedTime = '';
  state.seats = [];
  state.seatsShowId = null;
  state.selectedSeatIds = new Set();
  state.ticketCount = 2;
  state.category = 'Silver';
  state.hold = null;
  resetPaymentState();
  setStatus('');
  removeCheckStatusControl();
  ['f-name', 'f-email', 'f-phone', 'f-pwd', 'f-cpwd'].forEach((id) => { if ($(id)) $(id).value = ''; });
  if ($('f-city')) $('f-city').value = '';
  if ($('f-date')) $('f-date').value = '';
  if ($('f-tickets')) $('f-tickets').value = '2';
  if ($('f-category')) $('f-category').value = 'Silver';
  if ($('f-agree')) $('f-agree').checked = false;
  document.querySelectorAll('.has-error').forEach((row) => row.classList.remove('has-error'));
  document.querySelectorAll('.form-row .error').forEach((input) => input.classList.remove('error', 'valid'));
  goStep(1);
}

function openBooking() {
  state.lastFocused = document.activeElement;
  reset();
  openModal($('bookingModal'), { focus: $('f-name') });
  // Refresh the catalog if the initial load never succeeded (backend was
  // down at boot) — otherwise reuse what we already fetched.
  if (!catalogLoaded()) loadStep2Data();
  renderRecoveryNotice();
}

function close() {
  closeModal($('bookingModal'));
  abortAllRequests(); // no stale responses can render after close
  reset();
}

function togglePassword(targetId, button) {
  const input = $(targetId);
  if (!input || !button) return;
  input.type = input.type === 'password' ? 'text' : 'password';
  button.textContent = input.type === 'password' ? '👁' : '🙈';
}

// -- init -------------------------------------------------------------------
export function initBooking() {
  const modal = $('bookingModal');
  if (!modal || initDone) return;
  initDone = true;

  loadStep2Data();

  registerActions({
    'open-booking': openBooking,
    'close-booking': close,
    'booking-step1': validateStep1,
    'booking-step2': validateStep2,
    'booking-step3': validateStep3,
    'confirm-booking': confirm,
    'check-payment-status': checkPaymentNow,
    'recover-booking': recoverBooking,
    'dismiss-recovery': (event, target) => { target.closest('#booking-recovery')?.remove(); },
    'toggle-password': (event, target) => togglePassword(target.dataset.target, target),
  });

  // Back-step navigation — blocked while any critical request is in flight so
  // a user can't jump wizard steps mid-payment or mid-hold.
  document.addEventListener('click', (event) => {
    const step = event.target.closest?.('[data-step]')?.dataset.step;
    if (!step) return;
    if (document.querySelector('#bookingModal [data-busy]') || isCheckoutActive()) return;
    goStep(Number(step));
  });

  // Delegated seat clicks: one listener per container, immune to re-renders.
  ['gold-seats', 'silver-seats', 'bronze-seats'].forEach((id) => {
    $(id)?.addEventListener('click', (event) => {
      const seatButton = event.target.closest('.seat');
      if (seatButton) toggleSeat(Number(seatButton.dataset.id));
    });
  });

  // Delegated theater grid: cards and show-time buttons survive re-renders.
  const grid = $('theaterGrid');
  grid?.addEventListener('click', (event) => {
    const showButton = event.target.closest('.show-time');
    if (showButton) { event.stopPropagation(); selectShow(showButton); return; }
    const card = event.target.closest('.theater-card');
    if (card) selectTheater(card);
  });
  grid?.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    const card = event.target.closest('.theater-card');
    if (card && event.target === card) { event.preventDefault(); selectTheater(card); }
  });

  // Change events are the reliable signal for selects (the old click-based
  // wiring fired before the value actually changed).
  $('f-city')?.addEventListener('change', updateTheaters);
  $('f-date')?.addEventListener('change', refreshTheatersForDate);
  $('f-tickets')?.addEventListener('change', updateSeats);
  $('f-category')?.addEventListener('change', onCategoryChange);
  $('f-payment')?.addEventListener('change', (event) => {
    if ($('fr-upiid')) $('fr-upiid').style.display = event.target.value.startsWith('UPI') ? 'block' : 'none';
  });

  modal.addEventListener('click', (event) => { if (event.target === modal) close(); });
}
