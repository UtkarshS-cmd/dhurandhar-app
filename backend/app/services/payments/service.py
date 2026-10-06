"""PaymentService — the cinema backend's payment owner (Phase 4)."""
from __future__ import annotations

import time

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...config import Settings
from ...models import (
    Booking,
    BookingStatus,
    PaymentAttempt,
    PaymentAttemptStatus,
    PaymentStatus,
    PaymentWebhookEvent,
    ShowSeatStatus,
    WebhookEventStatus,
)
from ..booking import (
    BookingConflict,
    BookingExpired,
    BookingNotFound,
    PaymentUnavailable,
    _cleanup_expired_holds,
    _lock_booking,
    _lock_booking_seats,
    booking_transaction,
    transition_booking,
    transition_payment,
    transition_seat,
    utcnow,
)
from .base import PaymentOrder, PaymentProvider, ProviderError, WebhookEvent
from .mock import MockProvider
from .razorpay import RazorpayProvider

CURRENCY = "INR"

PAYMENT_ATTEMPT_TRANSITIONS: dict[str, frozenset[str]] = {
    PaymentAttemptStatus.CREATED.value: frozenset({
        PaymentAttemptStatus.PENDING.value,
        PaymentAttemptStatus.AUTHORIZED.value,
        PaymentAttemptStatus.PAID.value,
        PaymentAttemptStatus.FAILED.value,
        PaymentAttemptStatus.CANCELLED.value,
    }),
    PaymentAttemptStatus.PENDING.value: frozenset({
        PaymentAttemptStatus.AUTHORIZED.value,
        PaymentAttemptStatus.PAID.value,
        PaymentAttemptStatus.FAILED.value,
        PaymentAttemptStatus.CANCELLED.value,
    }),
    PaymentAttemptStatus.AUTHORIZED.value: frozenset({
        PaymentAttemptStatus.PAID.value,
        PaymentAttemptStatus.FAILED.value,
        PaymentAttemptStatus.CANCELLED.value,
    }),
    PaymentAttemptStatus.FAILED.value: frozenset({
        PaymentAttemptStatus.PENDING.value,
    }),
    PaymentAttemptStatus.PAID.value: frozenset({PaymentAttemptStatus.REFUNDED.value}),
    PaymentAttemptStatus.CANCELLED.value: frozenset(),
    PaymentAttemptStatus.REFUNDED.value: frozenset(),
}

ACTIVE_ATTEMPT_STATUSES = frozenset({
    PaymentAttemptStatus.CREATED.value,
    PaymentAttemptStatus.PENDING.value,
    PaymentAttemptStatus.AUTHORIZED.value,
})


def transition_attempt(attempt: PaymentAttempt, target: str) -> None:
    if target == attempt.status:
        return
    allowed = PAYMENT_ATTEMPT_TRANSITIONS.get(attempt.status, frozenset())
    if target not in allowed:
        from ..booking import InvalidTransition
        raise InvalidTransition(f"Illegal payment attempt transition {attempt.status!r} -> {target!r}")
    attempt.status = target


def get_provider(settings: Settings | None = None) -> PaymentProvider:
    from ...config import settings as global_settings
    cfg = settings or global_settings
    name = (getattr(cfg, "payment_provider", "") or "").strip().lower() or "mock"
    if name == "mock":
        return MockProvider()
    if name == "razorpay":
        key_id = (getattr(cfg, "razorpay_key_id", "") or "").strip()
        key_secret = (getattr(cfg, "razorpay_key_secret", "") or "").strip()
        webhook_secret = (getattr(cfg, "razorpay_webhook_secret", "") or "").strip()
        if not key_id or not key_secret or not webhook_secret:
            raise ProviderError(
                "PAYMENT_PROVIDER=razorpay requires RAZORPAY_KEY_ID, "
                "RAZORPAY_KEY_SECRET and RAZORPAY_WEBHOOK_SECRET.")
        return RazorpayProvider(key_id=key_id, key_secret=key_secret, webhook_secret=webhook_secret)
    raise ProviderError(f"Unknown payment provider {name!r}.")


def _active_attempt(db: Session, booking_id: int) -> PaymentAttempt | None:
    return db.scalar(
        select(PaymentAttempt)
        .where(PaymentAttempt.booking_id == booking_id,
               PaymentAttempt.status.in_(ACTIVE_ATTEMPT_STATUSES))
        .order_by(PaymentAttempt.attempt_no.desc()))

class PaymentService:
    def __init__(self, provider=None):
        self._provider = provider

    @property
    def provider(self):
        if self._provider is None:
            self._provider = get_provider()
        return self._provider

    def _checkout_hints(self, attempt):
        if attempt.provider == "razorpay":
            p = self.provider
            kid = getattr(p, "public_key_id", "") if p.name == "razorpay" else ""
            return {"key_id": kid, "name": "Dhurandhar Cinema"}
        from .mock import _mock_payment_id
        if attempt.provider_order_id:
            return {"mock_payment_id": _mock_payment_id(attempt.provider_order_id)}
        return {}

    def create_order_for_booking(self, db, booking_reference):
        def _op():
            booking = _lock_booking(db, booking_reference)
            if booking is None or not booking_reference.startswith("DHR-"):
                raise BookingNotFound("Booking hold not found")
            _cleanup_expired_holds(db)
            db.flush()
            booking = _lock_booking(db, booking_reference)
            if booking is None:
                raise BookingNotFound("Booking hold not found")
            if booking.status != BookingStatus.HELD.value:
                raise BookingConflict("Booking is no longer available for payment")
            if not booking.hold_expires_at or booking.hold_expires_at < utcnow():
                raise BookingExpired("Seat hold expired; please choose seats again")
            existing = _active_attempt(db, booking.id)
            if existing is not None:
                return existing.id, float(existing.amount), existing.currency
            attempt_no = (db.scalar(select(func.max(PaymentAttempt.attempt_no)).where(
                PaymentAttempt.booking_id == booking.id)) or 0) + 1
            attempt = PaymentAttempt(
                booking_id=booking.id, provider=self.provider.name,
                amount=float(booking.total_amount), currency=CURRENCY,
                status=PaymentAttemptStatus.CREATED.value, attempt_no=attempt_no)
            db.add(attempt)
            db.flush()
            return attempt.id, float(attempt.amount), attempt.currency
        attempt_id, amount, currency = booking_transaction(db, _op)
        return self._attach_or_reuse(db, booking_reference, attempt_id, amount, currency)

    def retry_payment(self, db, booking_reference):
        def _op():
            booking = _lock_booking(db, booking_reference)
            if booking is None:
                raise BookingNotFound("Booking hold not found")
            _cleanup_expired_holds(db)
            db.flush()
            booking = _lock_booking(db, booking_reference)
            if booking is None:
                raise BookingNotFound("Booking hold not found")
            if booking.status != BookingStatus.HELD.value:
                raise BookingConflict("Booking is no longer available for payment")
            if not booking.hold_expires_at or booking.hold_expires_at < utcnow():
                raise BookingExpired("Seat hold expired; please choose seats again")
            active = _active_attempt(db, booking.id)
            if active is not None:
                return active.id, float(active.amount), active.currency
            failed = db.scalars(select(PaymentAttempt).where(
                PaymentAttempt.booking_id == booking.id,
                PaymentAttempt.status == PaymentAttemptStatus.FAILED.value
                ).order_by(PaymentAttempt.attempt_no.desc())).first()
            if failed is None and booking.payment_status != PaymentStatus.FAILED.value:
                raise BookingConflict("Nothing to retry: no failed payment attempt")
            attempt_no = (db.scalar(select(func.max(PaymentAttempt.attempt_no)).where(
                PaymentAttempt.booking_id == booking.id)) or 0) + 1
            attempt = PaymentAttempt(
                booking_id=booking.id, provider=self.provider.name,
                amount=float(booking.total_amount), currency=CURRENCY,
                status=PaymentAttemptStatus.CREATED.value, attempt_no=attempt_no)
            if booking.payment_status == PaymentStatus.FAILED.value:
                transition_payment(booking, PaymentStatus.PENDING.value)
            db.add(attempt)
            db.flush()
            return attempt.id, float(attempt.amount), attempt.currency
        attempt_id, amount, currency = booking_transaction(db, _op)
        return self._attach_or_reuse(db, booking_reference, attempt_id, amount, currency)

    def _attach_or_reuse(self, db, booking_reference, attempt_id, amount, currency):
        attempt = db.get(PaymentAttempt, attempt_id)
        assert attempt is not None
        if attempt.provider_order_id:
            return attempt, PaymentOrder(
                provider=attempt.provider, provider_order_id=attempt.provider_order_id,
                amount=float(attempt.amount), currency=attempt.currency,
                booking_reference=booking_reference, checkout=self._checkout_hints(attempt))
        try:
            order = self.provider.create_order(
                amount=amount, currency=currency,
                booking_reference=booking_reference, attempt_id=attempt.id)
        except Exception as exc:
            def _fail():
                row = db.get(PaymentAttempt, attempt_id)
                if row is not None and row.status == PaymentAttemptStatus.CREATED.value:
                    transition_attempt(row, PaymentAttemptStatus.FAILED.value)
                    row.failure_code = "PROVIDER_ERROR"
                    row.failure_reason = str(exc)[:500]
            booking_transaction(db, _fail)
            raise PaymentUnavailable(str(exc)) from exc
        def _attach():
            row = db.get(PaymentAttempt, attempt_id)
            if row is not None and not row.provider_order_id:
                row.provider_order_id = order.provider_order_id
                if row.status == PaymentAttemptStatus.CREATED.value:
                    transition_attempt(row, PaymentAttemptStatus.PENDING.value)
        booking_transaction(db, _attach)
        db.expire_all()
        attempt = db.get(PaymentAttempt, attempt_id)
        assert attempt is not None
        return attempt, PaymentOrder(
            provider=attempt.provider,
            provider_order_id=attempt.provider_order_id or order.provider_order_id,
            amount=float(attempt.amount), currency=attempt.currency,
            booking_reference=booking_reference, checkout=self._checkout_hints(attempt))

    def process_webhook_event(self, db, event):
        if not event.event_id or not event.provider_order_id:
            return {"outcome": "ignored", "reason": "missing identifiers"}
        # Crash-safe idempotency: claim + settlement commit atomically.
        # A row is only PROCESSED together with its settlement, so a crash
        # before commit leaves either no row or a retryable row — never a
        # committed RECEIVED that incorrectly reads as "already processed".
        # IntegrityError here means a concurrent transaction won the insert
        # race; retry in a fresh transaction to observe its committed state.
        last_exc = None
        for _retry in range(5):
            try:
                return booking_transaction(db, lambda: self._settle_atomic(db, event))
            except IntegrityError as exc:
                last_exc = exc
                try:
                    db.rollback()
                except Exception:
                    pass
                time.sleep(0.01 * (_retry + 1))
                continue
            except PaymentUnavailable:
                raise
            except Exception as exc:
                raise PaymentUnavailable(str(exc)) from exc
        # Retries exhausted on insert races: observe committed state instead
        # of poisoning the event. Only a durable PROCESSED counts.
        try:
            committed = db.scalar(select(PaymentWebhookEvent).where(
                PaymentWebhookEvent.provider == event.provider,
                PaymentWebhookEvent.event_id == event.event_id))
        except Exception:
            committed = None
        if committed is not None and committed.status == WebhookEventStatus.PROCESSED.value:
            return {"outcome": "already_processed"}
        if last_exc is not None:
            raise PaymentUnavailable(f"concurrent webhook delivery: {last_exc}") from last_exc
        raise PaymentUnavailable("concurrent webhook delivery")  # pragma: no cover

    def _settle_atomic(self, db, event):
        """Single-transaction claim + validate + settle.

        Lock order: PaymentAttempt -> webhook row -> Booking -> ShowSeats
        (seats in deterministic id order via existing helpers). The attempt
        row is the real settlement mutex: two different event_ids for the
        same provider order serialize on it so only one can flip PENDING to
        PAID. No external provider calls inside this transaction.
        """
        attempt = db.scalar(select(PaymentAttempt).where(
            PaymentAttempt.provider == event.provider,
            PaymentAttempt.provider_order_id == event.provider_order_id
            ).with_for_update())
        webhook_row = db.scalar(select(PaymentWebhookEvent).where(
            PaymentWebhookEvent.provider == event.provider,
            PaymentWebhookEvent.event_id == event.event_id).with_for_update())
        if webhook_row is None:
            webhook_row = PaymentWebhookEvent(
                provider=event.provider, event_id=event.event_id,
                event_type=event.event_type, status=WebhookEventStatus.RECEIVED.value)
            db.add(webhook_row)
            db.flush()
        elif webhook_row.status == WebhookEventStatus.PROCESSED.value:
            return {"outcome": "already_processed"}
        # RECEIVED / FAILED / IGNORED rows are retryable: fall through and
        # reprocess safely inside this same transaction. The unique
        # (provider, event_id) constraint is never weakened and the row is
        # never deleted; PROCESSED is only written together with settlement.
        return self._settle(db, event, webhook_row=webhook_row, attempt=attempt)

    def _settle(self, db, event, webhook_row=None, attempt=None):
        # Webhook row + attempt are already claimed + row-locked by
        # _settle_atomic and passed in; only look them up when _settle is
        # invoked directly.
        if webhook_row is None:
            webhook_row = db.scalar(select(PaymentWebhookEvent).where(
                PaymentWebhookEvent.provider == event.provider,
                PaymentWebhookEvent.event_id == event.event_id).with_for_update())
        if attempt is None:
            attempt = db.scalar(select(PaymentAttempt).where(
                PaymentAttempt.provider == event.provider,
                PaymentAttempt.provider_order_id == event.provider_order_id
                ).with_for_update())
        if attempt is None:
            if webhook_row is not None:
                webhook_row.status = WebhookEventStatus.IGNORED.value
                webhook_row.processed_at = utcnow()
            return {"outcome": "ignored", "reason": "unknown provider order"}
        if webhook_row is not None:
            webhook_row.payment_attempt_id = attempt.id
        bad = self._check_invariants(db, event, attempt, webhook_row)
        if bad is not None:
            return bad
        return self._settle_paid(db, event, attempt, webhook_row)

    def _check_invariants(self, db, event, attempt, webhook_row):
        if event.amount is not None and abs(float(event.amount) - float(attempt.amount)) > 0.001:
            self._fail_attempt(attempt, "AMOUNT_MISMATCH",
                f"Webhook amount {event.amount} != recorded {float(attempt.amount)}")
            self._mark_row(webhook_row, WebhookEventStatus.FAILED.value)
            return {"outcome": "ignored", "reason": "amount mismatch"}
        if event.currency and event.currency != attempt.currency:
            self._fail_attempt(attempt, "CURRENCY_MISMATCH",
                f"Webhook currency {event.currency} != {attempt.currency}")
            self._mark_row(webhook_row, WebhookEventStatus.FAILED.value)
            return {"outcome": "ignored", "reason": "currency mismatch"}
        booking = db.scalar(select(Booking).where(
            Booking.id == attempt.booking_id).with_for_update())
        if booking is None:
            self._mark_row(webhook_row, WebhookEventStatus.FAILED.value)
            return {"outcome": "ignored", "reason": "booking missing"}
        if event.outcome == "failed":
            self._fail_attempt(attempt, "PROVIDER_DECLINED", f"{event.event_type}"[:500])
            try:
                transition_payment(booking, PaymentStatus.FAILED.value)
            except Exception:
                pass
            self._mark_row(webhook_row, WebhookEventStatus.PROCESSED.value)
            return {"outcome": "failed_recorded"}
        if event.outcome == "authorized":
            if attempt.status in (PaymentAttemptStatus.CREATED.value,
                                  PaymentAttemptStatus.PENDING.value):
                transition_attempt(attempt, PaymentAttemptStatus.AUTHORIZED.value)
                if event.provider_payment_id and not attempt.provider_payment_id:
                    attempt.provider_payment_id = event.provider_payment_id
            self._mark_row(webhook_row, WebhookEventStatus.PROCESSED.value)
            return {"outcome": "failed_recorded", "authorized": True}
        if event.outcome != "paid":
            self._mark_row(webhook_row, WebhookEventStatus.IGNORED.value)
            return {"outcome": "ignored", "reason": f"unrecognized event {event.event_type}"}
        return None

    def _fail_attempt(self, attempt, code, reason):
        if attempt.status in (PaymentAttemptStatus.CREATED.value,
                              PaymentAttemptStatus.PENDING.value,
                              PaymentAttemptStatus.AUTHORIZED.value):
            transition_attempt(attempt, PaymentAttemptStatus.FAILED.value)
            attempt.failure_code = code
            attempt.failure_reason = reason[:500]

    def _mark_row(self, row, status):
        if row is not None:
            row.status = status
            row.processed_at = utcnow()

    def _settle_paid(self, db, event, attempt, webhook_row):
        _cleanup_expired_holds(db)
        db.flush()
        booking = db.scalar(select(Booking).where(
            Booking.id == attempt.booking_id).with_for_update())
        if booking is None:
            self._mark_row(webhook_row, WebhookEventStatus.FAILED.value)
            return {"outcome": "ignored", "reason": "booking missing"}
        if attempt.status == PaymentAttemptStatus.PAID.value:
            self._mark_row(webhook_row, WebhookEventStatus.PROCESSED.value)
            return {"outcome": "already_processed"}
        if attempt.status in (PaymentAttemptStatus.CANCELLED.value,
                              PaymentAttemptStatus.REFUNDED.value):
            self._mark_row(webhook_row, WebhookEventStatus.PROCESSED.value)
            return {"outcome": "terminal_ignored"}
        if booking.status != BookingStatus.HELD.value:
            if event.provider_payment_id and not attempt.provider_payment_id:
                attempt.provider_payment_id = event.provider_payment_id
            self._fail_attempt(attempt, "BOOKING_TERMINAL",
                f"Payment arrived for {booking.status} booking "
                f"{booking.booking_reference}; manual reconciliation required")
            self._mark_row(webhook_row, WebhookEventStatus.PROCESSED.value)
            return {"outcome": "terminal_ignored"}
        if not booking.hold_expires_at or booking.hold_expires_at < utcnow():
            transition_booking(booking, BookingStatus.CANCELLED.value)
            for ss in _lock_booking_seats(db, booking.id):
                if ss.status == ShowSeatStatus.HELD.value:
                    transition_seat(ss, ShowSeatStatus.AVAILABLE.value,
                                    hold_expires_at=None, booking_id=None)
            if event.provider_payment_id and not attempt.provider_payment_id:
                attempt.provider_payment_id = event.provider_payment_id
            self._fail_attempt(attempt, "HOLD_EXPIRED", "Payment arrived after hold expiry")
            self._mark_row(webhook_row, WebhookEventStatus.PROCESSED.value)
            return {"outcome": "terminal_ignored"}
        seat_rows = _lock_booking_seats(db, booking.id)
        if len(seat_rows) == 0 or any(
                ss.status != ShowSeatStatus.HELD.value for ss in seat_rows):
            self._mark_row(webhook_row, WebhookEventStatus.FAILED.value)
            return {"outcome": "ignored", "reason": "seats not held"}
        if event.provider_payment_id and not attempt.provider_payment_id:
            attempt.provider_payment_id = event.provider_payment_id
        transition_attempt(attempt, PaymentAttemptStatus.PAID.value)
        transition_payment(booking, PaymentStatus.PAID.value)
        transition_booking(booking, BookingStatus.CONFIRMED.value, hold_expires_at=None)
        for ss in seat_rows:
            transition_seat(ss, ShowSeatStatus.BOOKED.value,
                            hold_expires_at=None, booking_id=ss.booking_id)
        self._mark_row(webhook_row, WebhookEventStatus.PROCESSED.value)
        return {"outcome": "confirmed", "booking_reference": booking.booking_reference}


def get_payment_service(provider=None):
    return PaymentService(provider=provider)
