// Seat selection state machine.
//
// Server truth (GET /shows/{id}/seats) has three statuses: AVAILABLE, HELD,
// BOOKED. The UI derives five states by combining server truth with the
// local selection:
//
//   AVAILABLE  — server AVAILABLE, not selected
//   SELECTED   — server AVAILABLE, locally selected
//   HELD       — server HELD (someone else's active hold)
//   BOOKED     — server BOOKED (confirmed)
//   UNAVAILABLE— any other/future server status (treated as unbookable)
//
// Selection is only ever legal on AVAILABLE seats; reconciliation happens on
// every server refresh so a stale selection can never be submitted.

export const SEAT_STATE = Object.freeze({
  AVAILABLE: 'AVAILABLE',
  SELECTED: 'SELECTED',
  HELD: 'HELD',
  BOOKED: 'BOOKED',
  UNAVAILABLE: 'UNAVAILABLE',
});

export const MAX_SEATS_PER_BOOKING = 6; // matches backend schema (1..6)

/** Classify one server seat row against the current local selection. */
export function classify(seat, selectedIds) {
  if (!seat) return SEAT_STATE.UNAVAILABLE;
  if (seat.status === 'AVAILABLE') {
    return selectedIds.has(seat.id) ? SEAT_STATE.SELECTED : SEAT_STATE.AVAILABLE;
  }
  if (seat.status === 'HELD') return SEAT_STATE.HELD;
  if (seat.status === 'BOOKED') return SEAT_STATE.BOOKED;
  return SEAT_STATE.UNAVAILABLE;
}

export function isSelectable(state) {
  return state === SEAT_STATE.AVAILABLE;
}

/**
 * Reconcile local selection against freshly fetched server seats.
 * Returns { seats: filteredSelection, removed: [dropped seat rows] }.
 * Never mutates the caller's Set.
 */
export function reconcileSelection(seats, selectedIds) {
  const byId = new Map(seats.map((s) => [s.id, s]));
  const next = [];
  const removed = [];
  for (const id of selectedIds) {
    const seat = byId.get(id);
    if (seat && seat.status === 'AVAILABLE') next.push(id);
    else removed.push(seat || { id, row: '?', number: '?', category: '', status: 'MISSING' });
  }
  return { selected: new Set(next), removed };
}

/** Enforce ticket-count cap (and the global 6-seat backend cap). */
export function capSelection(selectedIds, ticketCount) {
  const limit = Math.min(Math.max(1, ticketCount), MAX_SEATS_PER_BOOKING);
  const kept = [];
  const dropped = [];
  for (const id of selectedIds) {
    if (kept.length < limit) kept.push(id);
    else dropped.push(id);
  }
  return { selected: new Set(kept), dropped };
}

/** Human-readable seat label like "C7" (falls back to the id). */
export function seatLabel(seat) {
  if (!seat) return '?';
  if (seat.row && seat.number) return `${seat.row}${seat.number}`;
  return String(seat.id);
}

/** Accessible state description — not conveyed by color alone. */
export function stateDescription(state, selectedIds, seat) {
  switch (state) {
    case SEAT_STATE.SELECTED: return 'selected';
    case SEAT_STATE.HELD: return 'held by another booking';
    case SEAT_STATE.BOOKED: return 'booked';
    case SEAT_STATE.UNAVAILABLE: return 'unavailable';
    default: return `available, ₹${Number(seat?.price ?? 0)}`;
  }
}
