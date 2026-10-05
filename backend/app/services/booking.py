"""Phase 3 — the authoritative cinema booking engine.

This module is the *single* place that defines what a booking/seat state
transition means. Outside of tests and ``seed.py`` (which only creates rows
in their initial state), no other module may assign ``Booking.status``,
``ShowSeat.status`` or ``Booking.payment_status`` directly.

State machine
-------------

::

    ShowSeat                     Booking                     Payment

    AVAILABLE                    HELD                        PENDING
       |                          |  |                      /      \\
       v                          |  +--> CANCELLED        v        v
     HELD                         +----> CONFIRMED        PAID     FAILED
       |                                                   (retry ->
       +--> AVAILABLE  (hold expiry only)                  PENDING/PAID)
       v
     BOOKED   (terminal)

Terminal states (``BOOKED``, ``CONFIRMED``, ``CANCELLED``, ``PAID``) never
transition again in this phase. There is deliberately **no** cancellation
workflow for confirmed bookings yet; the transition tables below make any
such attempt raise :class:`InvalidTransition` instead of silently mutating
rows.

Transaction & concurrency model
-------------------------------

Every mutating operation runs inside :func:`booking_transaction`, which:

1. opens the database transaction,
2. issues a **write-intent statement first** — critical on SQLite, whose
   Python driver defers the real ``BEGIN`` until the first write statement.
   Without this, availability checks run in autocommit reads *before* the
   transaction exists and two concurrent holds can both observe
   ``AVAILABLE`` and both succeed (verified empirically during Phase 3
   development; see README). The write-intent statement grabs SQLite's
   single-writer lock before any state is read, making
   check -> decide -> write atomic against other writers,
3. runs the operation body,
4. commits atomically, or rolls back on *any* exception (no partial holds,
   no orphaned BookingSeat rows, no half-updated ShowSeat rows),
5. retries only on transient lock contention (SQLite "database is locked",
   PostgreSQL deadlock/serialization failure) — never on domain errors.

Row locking & lock ordering (PostgreSQL)
----------------------------------------

Seat acquisition uses ``SELECT ... FOR UPDATE`` with a deterministic
``ORDER BY`` inside the same transaction that mutates the seats:

* seat rows are always locked in ``ORDER BY show_seats.id`` order, so two
  users holding ``[A1, A2]`` and ``[A2, A1]`` acquire locks in the same
  sequence and cannot deadlock;
* the global lock hierarchy is ``bookings`` rows first, then ``show_seats``
  rows (confirm and cleanup both follow it; hold only locks seat rows);
* SQLite compiles ``FOR UPDATE`` away — there the single-writer lock from
  step 2 provides mutual exclusion. The two engines reach the same logical
  outcome through different mechanisms; this is intentional and documented
  rather than papered over.

Hold semantics
--------------

* Hold duration: 10 minutes (:data:`HOLD_TTL`), unchanged from Phase 1/2.
* Pricing is server-authoritative: totals are computed from ``Seat.price``
  rows read inside the transaction; client-submitted amounts are ignored.
* Expired holds are freed atomically: cleanup runs inside the hold
  transaction (before seat locks), so a previous holder's booking is
  cancelled and its seats released in the same transaction that re-holds
  them.
* Duplicate-submission idempotency: re-posting an identical hold for seats
  already held by *your own* active hold returns that same booking instead
  of creating a second one; conflicts against someone else's hold are 409.

Payment boundary (Phase 3)
--------------------------

The payment call happens inside the confirmation transaction while the
booking and seat rows are locked. With the mock gateway this is safe and
guarantees atomicity: if ``charge`` raises, everything rolls back and the
booking stays ``HELD``. Architectural limitation, deferred to Phase 4: with
a real external gateway you must not hold database row locks across network
I/O — the eventual flow is Booking Order -> Payment Order -> external
checkout -> webhook -> verified payment -> confirmation, with idempotent
webhook handling. No real gateway, webhooks or signature verification are
implemented here.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, func, text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session

from ..models import (
    Booking,
    BookingSeat,
    BookingStatus,
    PaymentStatus,
    Seat,
    Show,
    ShowSeat,
    ShowSeatStatus,
    User,
)
from ..schemas import BookingCreateRequest, UserCreate
from ..security import generate_booking_reference, hash_password, verify_password
from . import payment as payment_service

# Hold duration — unchanged from Phase 1/2 (10 minutes).
HOLD_TTL = timedelta(minutes=10)

# ---------------------------------------------------------------------------
# Clock seam — tests inject a controllable clock instead of sleeping.
# ---------------------------------------------------------------------------

_clock_fn = datetime.utcnow


def utcnow() -> datetime:
    """Current UTC time. Tests replace this via :func:`set_clock`."""
    return _clock_fn()


def set_clock(fn) -> None:
    """Install an injectable clock (test-only seam, mirrors ratelimit.py)."""
    global _clock_fn
    _clock_fn = fn


def reset_clock() -> None:
    """Restore the real wall clock (test teardown)."""
    global _clock_fn
    _clock_fn = datetime.utcnow


# ---------------------------------------------------------------------------
# State machine — the one authoritative definition of legal transitions.
# ---------------------------------------------------------------------------

SHOW_SEAT_TRANSITIONS: dict[str, frozenset[str]] = {
    ShowSeatStatus.AVAILABLE.value: frozenset({ShowSeatStatus.HELD.value}),
    ShowSeatStatus.HELD.value: frozenset(
        {ShowSeatStatus.BOOKED.value, ShowSeatStatus.AVAILABLE.value}
    ),
    ShowSeatStatus.BOOKED.value: frozenset(),  # terminal
}

BOOKING_TRANSITIONS: dict[str, frozenset[str]] = {
    BookingStatus.HELD.value: frozenset(
        {BookingStatus.CONFIRMED.value, BookingStatus.CANCELLED.value}
    ),
    BookingStatus.CONFIRMED.value: frozenset(),  # terminal: never silently back
    BookingStatus.CANCELLED.value: frozenset(),  # terminal
}

PAYMENT_TRANSITIONS: dict[str, frozenset[str]] = {
    PaymentStatus.PENDING.value: frozenset(
        {PaymentStatus.PAID.value, PaymentStatus.FAILED.value}
    ),
    PaymentStatus.FAILED.value: frozenset(
        {PaymentStatus.PENDING.value, PaymentStatus.PAID.value}  # retryable
    ),
    PaymentStatus.PAID.value: frozenset(),  # terminal
}

_TRANSITION_TABLES = {
    "show_seat": SHOW_SEAT_TRANSITIONS,
    "booking": BOOKING_TRANSITIONS,
    "payment": PAYMENT_TRANSITIONS,
}


class InvalidTransition(Exception):
    """A state change the machine forbids — always a bug or an attack."""


def can_transition(kind: str, current: str, target: str) -> bool:
    """Whether ``current -> target`` is legal for ``kind`` (no-op allowed)."""
    if target == current:
        return True  # assigning the same status (e.g. re-holding) is not a transition
    return target in _TRANSITION_TABLES[kind].get(current, frozenset())


def _assert_transition(kind: str, current: str, target: str) -> None:
    if not can_transition(kind, current, target):
        raise InvalidTransition(f"Illegal {kind} transition {current!r} -> {target!r}")


def transition_seat(
    seat: ShowSeat,
    target: str,
    *,
    hold_expires_at=...,
    booking_id=...,
) -> None:
    """Move a ShowSeat to ``target`` (guarded) and set its hold fields."""
    _assert_transition("show_seat", seat.status, target)
    seat.status = target
    if hold_expires_at is not ...:
        seat.hold_expires_at = hold_expires_at
    if booking_id is not ...:
        seat.booking_id = booking_id


def transition_booking(
    booking: Booking, target: str, *, hold_expires_at=...
) -> None:
    """Move a Booking to ``target`` (guarded)."""
    _assert_transition("booking", booking.status, target)
    booking.status = target
    if hold_expires_at is not ...:
        booking.hold_expires_at = hold_expires_at


def transition_payment(booking: Booking, target: str) -> None:
    """Move a Booking's payment status to ``target`` (guarded)."""
    _assert_transition("payment", booking.payment_status, target)
    booking.payment_status = target


# ---------------------------------------------------------------------------
# Domain errors — the API layer translates these into HTTP status codes.
# ---------------------------------------------------------------------------

class BookingEngineError(Exception):
    """Base class; carries HTTP status + response detail for the API layer."""

    status_code = 500

    def __init__(self, detail):
        self.detail = detail
        super().__init__(detail if isinstance(detail, str) else str(detail))


class ShowNotFound(BookingEngineError):
    status_code = 404


class BookingNotFound(BookingEngineError):
    status_code = 404


class InvalidSeatRequest(BookingEngineError):
    status_code = 422


class SeatConflict(BookingEngineError):
    status_code = 409


class BookingConflict(BookingEngineError):
    status_code = 409


class BookingExpired(BookingEngineError):
    status_code = 409


class AccountConflict(BookingEngineError):
    status_code = 409


class PaymentFailed(BookingEngineError):
    status_code = 402


class PaymentUnavailable(BookingEngineError):
    status_code = 502


# ---------------------------------------------------------------------------
# Transaction helper — atomicity, SQLite write-intent, bounded retry.
# ---------------------------------------------------------------------------

# Opening the real SQLite transaction with a no-op UPDATE *before* any state
# is read: the Python sqlite3 driver defers BEGIN until the first write
# statement, so availability checks executed before it would run in
# autocommit snapshots (empirically verified during Phase 3: check-first
# pattern lets two threads both observe AVAILABLE and both hold). On
# PostgreSQL this statement matches zero rows, so it takes no row locks and
# does not disturb the bookings -> show_seats lock hierarchy.
_WRITE_INTENT = text("UPDATE show_seats SET status = status WHERE 1 = 0")

_MAX_TRANSACTION_ATTEMPTS = 4
_RETRY_BACKOFF_SECONDS = 0.01


def _is_lock_contention(exc: Exception) -> bool:
    """Transient contention worth retrying — never domain/logic errors."""
    if isinstance(exc, OperationalError):
        msg = str(exc).lower()
        return "locked" in msg or "busy" in msg
    if isinstance(exc, DBAPIError):
        # PostgreSQL: 40001 serialization_failure, 40P01 deadlock_detected.
        orig = getattr(exc, "orig", None)
        return getattr(orig, "pgcode", None) in ("40001", "40P01")
    return False


def booking_transaction(db: Session, operation):
    """Run ``operation()`` as one atomic booking transaction.

    * write-intent first (SQLite single-writer reservation before checks),
    * commit only if ``operation`` returns normally,
    * full rollback on *any* exception — payment failures, conflicts and
      simulated crashes can never leave partial state,
    * bounded retry only for transient lock contention.

    Returns whatever ``operation`` returns.
    """
    for attempt in range(1, _MAX_TRANSACTION_ATTEMPTS + 1):
        try:
            db.execute(_WRITE_INTENT)
            result = operation()
            db.commit()
            return result
        except BookingEngineError:
            db.rollback()
            raise
        except Exception as exc:
            db.rollback()
            if attempt >= _MAX_TRANSACTION_ATTEMPTS or not _is_lock_contention(exc):
                raise
            time.sleep(_RETRY_BACKOFF_SECONDS * attempt)
    raise AssertionError("unreachable")  # pragma: no cover


# ---------------------------------------------------------------------------
# Locking helpers
# ---------------------------------------------------------------------------

def locked_seat_statement(show_id: int, seat_ids: list[int]):
    """The canonical seat-lock query used by hold.

    Deterministic acquisition order (``ORDER BY show_seats.id``) so that two
    transactions requesting the same seats in different *request* order lock
    rows in the same *database* order. On PostgreSQL ``FOR UPDATE`` takes
    row locks inside the caller's transaction; on SQLite it compiles to
    nothing and the write-intent lock provides mutual exclusion instead.
    """
    return (
        select(ShowSeat)
        .where(ShowSeat.show_id == show_id, ShowSeat.seat_id.in_(seat_ids))
        .order_by(ShowSeat.id)
        .with_for_update()
    )


def _lock_booking(db: Session, reference: str) -> Booking | None:
    """Lock a booking row by reference (bookings come first in lock order)."""
    return db.scalar(
        select(Booking)
        .where(Booking.booking_reference == reference)
        .with_for_update()
    )


def _lock_booking_seats(db: Session, booking_id: int) -> list[ShowSeat]:
    """Lock the ShowSeat rows owned by a booking, in deterministic order."""
    return list(
        db.scalars(
            select(ShowSeat)
            .where(ShowSeat.booking_id == booking_id)
            .order_by(ShowSeat.id)
            .with_for_update()
        )
    )


# ---------------------------------------------------------------------------
# Expiration / cleanup
# ---------------------------------------------------------------------------

def cleanup_expired_holds(db: Session) -> None:
    """Cancel expired holds and release their seats — atomically.

    Request-triggered (no background worker in this phase). Safe properties:

    * one transaction: a booking and its seats are released together;
    * lock order ``bookings`` -> ``show_seats``, rows in id order;
    * guarded transitions: only ``HELD -> CANCELLED`` and
      ``HELD -> AVAILABLE`` are performed, and only while the row *still*
      verifies the expiry predicate under the row lock — so a concurrent
      confirmation (which flips rows to ``CONFIRMED``/``BOOKED`` and clears
      ``hold_expires_at``) is never overwritten: on PostgreSQL
      ``SELECT ... FOR UPDATE`` re-evaluates the WHERE clause against the
      committed row version, on SQLite the single-writer lock serializes
      the two transactions entirely;
    * never touches ``CONFIRMED`` bookings or ``BOOKED`` seats;
    * idempotent — running it twice changes nothing the second time.
    """
    booking_transaction(db, lambda: _cleanup_expired_holds(db))


def _cleanup_expired_holds(db: Session) -> None:
    now = utcnow()
    expired_bookings = db.scalars(
        select(Booking)
        .where(
            Booking.status == BookingStatus.HELD.value,
            Booking.hold_expires_at.isnot(None),
            Booking.hold_expires_at < now,
        )
        .order_by(Booking.id)
        .with_for_update()
    ).all()
    for booking in expired_bookings:
        transition_booking(booking, BookingStatus.CANCELLED.value)

    expired_seats = db.scalars(
        select(ShowSeat)
        .where(
            ShowSeat.status == ShowSeatStatus.HELD.value,
            ShowSeat.hold_expires_at.isnot(None),
            ShowSeat.hold_expires_at < now,
        )
        .order_by(ShowSeat.id)
        .with_for_update()
    ).all()
    for seat in expired_seats:
        if seat.booking_id is not None:
            owner = db.get(Booking, seat.booking_id)
            # Defensive: a HELD seat whose owner booking is already CONFIRMED
            # would be a broken state — never release it blindly.
            if owner is not None and owner.status == BookingStatus.CONFIRMED.value:
                continue
        transition_seat(
            seat,
            ShowSeatStatus.AVAILABLE.value,
            hold_expires_at=None,
            booking_id=None,
        )


def effective_seat_status(seat: ShowSeat) -> str:
    """Display status: expired holds read as AVAILABLE even before cleanup."""
    if (
        seat.status == ShowSeatStatus.HELD.value
        and seat.hold_expires_at is not None
        and seat.hold_expires_at < utcnow()
    ):
        return ShowSeatStatus.AVAILABLE.value
    return seat.status


def validate_show_and_seats(show_id: int, seat_ids: list[int], db: Session):
    """Validate a requested hold against the active show and seat list."""
    if not isinstance(show_id, int) or show_id < 1:
        raise InvalidSeatRequest("Show ID must be positive")
    if not seat_ids or len(seat_ids) > 6:
        raise InvalidSeatRequest("Select between 1 and 6 seats")
    if any(seat_id < 1 for seat_id in seat_ids):
        raise InvalidSeatRequest("Seat IDs must be positive")
    if len(set(seat_ids)) != len(seat_ids):
        raise InvalidSeatRequest("Duplicate seats are not allowed")

    show = db.get(Show, show_id)
    if show is None or show.status != "ACTIVE":
        raise ShowNotFound("Show not found or inactive")

    rows = db.execute(
        select(ShowSeat, Seat)
        .join(Seat, ShowSeat.seat_id == Seat.id)
        .where(ShowSeat.show_id == show_id, Seat.id.in_(seat_ids))
        .order_by(ShowSeat.id)
    ).all()
    if len(rows) != len(seat_ids):
        raise InvalidSeatRequest("One or more seats do not belong to this show")
    return show, rows


def create_hold_for_user(db: Session, user: User, show_id: int, seat_ids: list[int], payment_method: str) -> Booking:
    """Create a booking hold atomically and return the created booking row."""

    def _op() -> Booking:
        show, rows = validate_show_and_seats(show_id, seat_ids, db)
        lock_rows = db.scalars(locked_seat_statement(show.id, seat_ids)).all()
        now = utcnow()
        conflicts: list[int] = []
        for ss in lock_rows:
            if ss.status == ShowSeatStatus.BOOKED.value:
                conflicts.append(ss.seat_id)
            elif (
                ss.status == ShowSeatStatus.HELD.value
                and ss.hold_expires_at is not None
                and ss.hold_expires_at > now
            ):
                conflicts.append(ss.seat_id)

        if conflicts:
            raise SeatConflict(
                {"message": "One or more selected seats are unavailable", "seat_ids": sorted(set(conflicts))}
            )

        expires = now + HOLD_TTL
        total = sum(Decimal(str(seat.price)) for _, seat in rows)
        booking = Booking(
            booking_reference=generate_booking_reference(),
            user_id=user.id,
            show_id=show.id,
            status=BookingStatus.HELD.value,
            total_amount=float(total),
            payment_method=payment_method,
            payment_status=PaymentStatus.PENDING.value,
            hold_expires_at=expires,
        )
        db.add(booking)
        db.flush()

        for ss in lock_rows:
            seat = next(seat for _, seat in rows if seat.id == ss.seat_id)
            transition_seat(
                ss,
                ShowSeatStatus.HELD.value,
                hold_expires_at=expires,
                booking_id=booking.id,
            )
            db.add(
                BookingSeat(
                    booking_id=booking.id,
                    show_id=show.id,
                    seat_id=seat.id,
                    price=seat.price,
                )
            )
        return booking

    return booking_transaction(db, _op)


def confirm_booking_reference(db: Session, reference: str, payment_method: str) -> Booking:
    """Confirm a held booking if still valid and chargeable."""

    def _op() -> Booking:
        if not reference or not reference.startswith("DHR-"):
            raise BookingNotFound("Booking hold not found")
        booking = _lock_booking(db, reference)
        if booking is None:
            raise BookingNotFound("Booking hold not found")
        if booking.status != BookingStatus.HELD.value:
            raise BookingConflict("Booking is no longer available for confirmation")
        if not booking.hold_expires_at or booking.hold_expires_at < utcnow():
            transition_booking(booking, BookingStatus.CANCELLED.value)
            raise BookingExpired("Seat hold expired; please choose seats again")

        seat_rows = _lock_booking_seats(db, booking.id)
        if len(seat_rows) == 0 or any(ss.status != ShowSeatStatus.HELD.value for ss in seat_rows):
            raise BookingConflict("Selected seats are no longer held")

        try:
            result = payment_service.PaymentGateway().charge(float(booking.total_amount), payment_method)
        except RuntimeError as exc:
            raise PaymentUnavailable(str(exc)) from exc

        booking.payment_method = payment_method
        transition_payment(booking, result.status)
        transition_booking(booking, BookingStatus.CONFIRMED.value, hold_expires_at=None)
        for ss in seat_rows:
            transition_seat(ss, ShowSeatStatus.BOOKED.value, hold_expires_at=None, booking_id=ss.booking_id)
        return booking

    return booking_transaction(db, _op)



