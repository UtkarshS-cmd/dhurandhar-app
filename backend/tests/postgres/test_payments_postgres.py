"""PostgreSQL payment concurrency — duplicate webhooks, expiry races, atomicity."""
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.models import (
    Booking,
    BookingStatus,
    PaymentAttempt,
    PaymentAttemptStatus,
    PaymentStatus,
    PaymentWebhookEvent,
    Show,
    ShowSeat,
    ShowSeatStatus,
    User,
)
from app.security import hash_password
from app.services.booking import create_hold_for_user, utcnow
from app.services.payments.mock import MockProvider, build_mock_success_event
from app.services.payments.service import PaymentService


def _prepare_hold(pg_migrated):
    factory = sessionmaker(bind=pg_migrated, autoflush=False, expire_on_commit=False, future=True)
    db = factory()
    try:
        user = User(
            full_name="PG Pay User",
            email=f"pg-pay-{uuid.uuid4().hex[:8]}@example.com",
            phone="9876543210",
            password_hash=hash_password("Test@1234"),
        )
        db.add(user)
        db.flush()
        show = db.scalars(select(Show)).first()
        seat_ids = (
            db.execute(
                select(ShowSeat.seat_id)
                .where(ShowSeat.show_id == show.id, ShowSeat.status == ShowSeatStatus.AVAILABLE.value)
                .order_by(ShowSeat.id)
                .limit(2)
            )
            .scalars()
            .all()
        )
        booking = create_hold_for_user(db, user, show.id, list(seat_ids), "PENDING")
        service = PaymentService(provider=MockProvider())
        order = service.create_order(db, booking.booking_reference)
        db.commit()
        return booking.booking_reference, order.provider_order_id, order.amount
    finally:
        db.close()


def test_pg_concurrent_duplicate_webhook_settles_once(pg_session, pg_migrated):
    reference, order_id, amount = _prepare_hold(pg_migrated)
    event_id, headers, body = build_mock_success_event(order_id=order_id, amount=amount)
    service = PaymentService(provider=MockProvider())
    barrier = threading.Barrier(2)
    outcomes = []

    def worker():
        factory = sessionmaker(bind=pg_migrated, autoflush=False, expire_on_commit=False, future=True)
        db = factory()
        try:
            barrier.wait()
            outcomes.append(service.handle_webhook(db, "mock", headers, body))
        except Exception as exc:  # noqa: BLE001 — asserted below
            outcomes.append({"outcome": f"error:{type(exc).__name__}"})
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: worker(), range(2)))

    factory = sessionmaker(bind=pg_migrated, future=True)
    db = factory()
    try:
        booking = db.scalar(select(Booking).where(Booking.booking_reference == reference))
        assert booking.status == BookingStatus.CONFIRMED.value
        assert booking.payment_status == PaymentStatus.PAID.value
        events = db.scalars(select(PaymentWebhookEvent).where(PaymentWebhookEvent.event_id == event_id)).all()
        assert len(events) == 1
        paid = db.scalars(
            select(PaymentAttempt).where(PaymentAttempt.provider_order_id == order_id)
        ).all()
        assert sum(1 for a in paid if a.status == PaymentAttemptStatus.PAID.value) == 1
        booked = db.scalars(select(ShowSeat).where(ShowSeat.booking_id == booking.id)).all()
        assert booked and all(s.status == ShowSeatStatus.BOOKED.value for s in booked)
    finally:
        db.close()


def test_pg_expired_hold_never_confirms_via_webhook(pg_session, pg_migrated):
    reference, order_id, amount = _prepare_hold(pg_migrated)
    factory = sessionmaker(bind=pg_migrated, future=True)
    db = factory()
    try:
        booking = db.scalar(select(Booking).where(Booking.booking_reference == reference))
        past = utcnow() - timedelta(minutes=1)
        booking.hold_expires_at = past
        for ss in db.scalars(select(ShowSeat).where(ShowSeat.booking_id == booking.id)).all():
            ss.hold_expires_at = past
        db.commit()
    finally:
        db.close()

    event_id, headers, body = build_mock_success_event(order_id=order_id, amount=amount)
    worker_factory = sessionmaker(bind=pg_migrated, autoflush=False, expire_on_commit=False, future=True)
    db = worker_factory()
    try:
        result = PaymentService(provider=MockProvider()).handle_webhook(db, "mock", headers, body)
    finally:
        db.close()
    assert result["outcome"] in {"terminal_ignored", "ignored", "already_processed"}

    check = worker_factory()
    try:
        booking = check.scalar(select(Booking).where(Booking.booking_reference == reference))
        assert booking.status != BookingStatus.CONFIRMED.value
    finally:
        check.close()


def test_pg_settlement_failure_rolls_back_then_retry_succeeds(pg_session, pg_migrated):
    reference, order_id, amount = _prepare_hold(pg_migrated)
    event_id, headers, body = build_mock_success_event(order_id=order_id, amount=amount)

    factory = sessionmaker(bind=pg_migrated, autoflush=False, expire_on_commit=False, future=True)
    db = factory()
    try:
        attempt = db.scalar(select(PaymentAttempt).where(PaymentAttempt.provider_order_id == order_id))
        from app.services.booking import booking_transaction

        def _op():
            attempt2 = db.scalar(select(PaymentAttempt).where(PaymentAttempt.id == attempt.id))
            attempt2.provider_payment_id = "pay_injected"
            db.flush()
            raise RuntimeError("injected settlement failure")

        try:
            booking_transaction(db, _op)
        except RuntimeError:
            pass
    finally:
        db.close()

    db = factory()
    try:
        result = PaymentService(provider=MockProvider()).handle_webhook(db, "mock", headers, body)
        assert result["outcome"] in {"confirmed", "already_processed"}
    finally:
        db.close()

    check = sessionmaker(bind=pg_migrated, future=True)()
    try:
        booking = check.scalar(select(Booking).where(Booking.booking_reference == reference))
        assert booking.status == BookingStatus.CONFIRMED.value
        events = check.scalars(select(PaymentWebhookEvent).where(PaymentWebhookEvent.event_id == event_id)).all()
        assert len(events) == 1
    finally:
        check.close()

