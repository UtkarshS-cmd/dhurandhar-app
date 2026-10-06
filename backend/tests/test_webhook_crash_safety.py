"""Phase 4 hotfix — crash-safe webhook idempotency.

Covers the failure window where a webhook claim committed separately from
settlement: a crash between the two left a RECEIVED row that later retries
mistook for "already processed", so the payment was never settled.
"""
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from app.models import (
    Booking, BookingStatus, PaymentAttempt, PaymentAttemptStatus,
    PaymentStatus, PaymentWebhookEvent, ShowSeat, ShowSeatStatus,
    WebhookEventStatus,
)
from app.services.payments.mock import (
    MockProvider, build_mock_success_event)
from app.services.payments.service import PaymentService
from conftest import TestingSessionLocal
from test_payments_phase4 import _hold_via_api, _order, _webhook


def _event_row(event_id):
    with TestingSessionLocal() as db:
        row = db.scalar(select(PaymentWebhookEvent).where(
            PaymentWebhookEvent.event_id == event_id))
        if row is None:
            return None
        return row.status


def test_new_webhook_settles_and_marks_processed(client):
    hold = _hold_via_api(client, "crashnew")
    order = _order(client, hold["booking_reference"])
    eid, headers, body = build_mock_success_event(
        order_id=order["provider_order_id"], amount=order["amount"])
    resp = _webhook(client, "mock", body, headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ok"
    assert _event_row(eid) == WebhookEventStatus.PROCESSED.value
    with TestingSessionLocal() as db:
        booking = db.query(Booking).filter_by(
            booking_reference=hold["booking_reference"]).one()
        assert booking.status == BookingStatus.CONFIRMED.value
        assert booking.payment_status == PaymentStatus.PAID.value
        attempt = db.query(PaymentAttempt).filter_by(
            provider_order_id=order["provider_order_id"]).one()
        assert attempt.status == PaymentAttemptStatus.PAID.value

def test_duplicate_processed_webhook_is_idempotent(client):
    hold = _hold_via_api(client, "crashdup")
    order = _order(client, hold["booking_reference"])
    eid, headers, body = build_mock_success_event(
        order_id=order["provider_order_id"], amount=order["amount"])
    assert _webhook(client, "mock", body, headers).json()["status"] == "ok"
    second = _webhook(client, "mock", body, headers)
    assert second.json()["status"] == "already_processed"
    assert _event_row(eid) == WebhookEventStatus.PROCESSED.value
    with TestingSessionLocal() as db:
        assert db.query(PaymentWebhookEvent).filter_by(event_id=eid).count() == 1
        assert db.query(PaymentAttempt).filter_by(
            provider_order_id=order["provider_order_id"]).count() == 1
        booking = db.query(Booking).filter_by(
            booking_reference=hold["booking_reference"]).one()
        assert booking.status == BookingStatus.CONFIRMED.value


def test_crash_before_settlement_leaves_event_retryable(client, monkeypatch):
    """Failure injection stands in for a crash between claim and settlement:
    the error is raised *inside* the single transaction so it must roll back
    atomically — no committed RECEIVED row may remain."""
    hold = _hold_via_api(client, "crashsim")
    order = _order(client, hold["booking_reference"])
    eid, headers, body = build_mock_success_event(
        order_id=order["provider_order_id"], amount=order["amount"])

    from app.services.payments import service as payment_service_mod

    def boom(*args, **kwargs):
        raise RuntimeError("simulated crash before settlement commit")

    monkeypatch.setattr(payment_service_mod, "transition_booking", boom)
    assert _webhook(client, "mock", body, headers).status_code == 502
    # Atomic rollback: either no row at all, or a non-PROCESSED row.
    # Either way the event must NOT read as already processed.
    assert _event_row(eid) in (None, WebhookEventStatus.RECEIVED.value,
                               WebhookEventStatus.FAILED.value)
    with TestingSessionLocal() as db:
        booking = db.query(Booking).filter_by(
            booking_reference=hold["booking_reference"]).one()
        assert booking.status == BookingStatus.HELD.value
        attempt = db.query(PaymentAttempt).filter_by(
            provider_order_id=order["provider_order_id"]).one()
        assert attempt.status != PaymentAttemptStatus.PAID.value

    # Provider retry of the SAME event must now settle the payment.
    monkeypatch.undo()
    retry = _webhook(client, "mock", body, headers)
    assert retry.json()["status"] == "ok", retry.text
    assert _event_row(eid) == WebhookEventStatus.PROCESSED.value
    with TestingSessionLocal() as db:
        booking = db.query(Booking).filter_by(
            booking_reference=hold["booking_reference"]).one()
        assert booking.status == BookingStatus.CONFIRMED.value
        assert booking.payment_status == PaymentStatus.PAID.value


def test_stale_received_row_is_reprocessed(client):
    """A committed RECEIVED row (left by the pre-hotfix two-transaction claim
    or any crash between receipt and settlement) must be reprocessed — mere
    row existence is not proof of processing."""
    hold = _hold_via_api(client, "stalerow")
    order = _order(client, hold["booking_reference"])
    eid, headers, body = build_mock_success_event(
        order_id=order["provider_order_id"], amount=order["amount"])
    with TestingSessionLocal() as db:
        db.add(PaymentWebhookEvent(
            provider="mock", event_id=eid, event_type="payment.captured",
            status=WebhookEventStatus.RECEIVED.value))
        db.commit()
    resp = _webhook(client, "mock", body, headers)
    assert resp.json()["status"] == "ok", resp.text
    assert _event_row(eid) == WebhookEventStatus.PROCESSED.value
    with TestingSessionLocal() as db:
        booking = db.query(Booking).filter_by(
            booking_reference=hold["booking_reference"]).one()
        assert booking.status == BookingStatus.CONFIRMED.value

def test_stale_failed_row_is_reprocessed(client):
    """A FAILED row from an earlier transient failure must stay retryable."""
    hold = _hold_via_api(client, "failrow")
    order = _order(client, hold["booking_reference"])
    eid, headers, body = build_mock_success_event(
        order_id=order["provider_order_id"], amount=order["amount"])
    with TestingSessionLocal() as db:
        db.add(PaymentWebhookEvent(
            provider="mock", event_id=eid, event_type="payment.captured",
            status=WebhookEventStatus.FAILED.value))
        db.commit()
    resp = _webhook(client, "mock", body, headers)
    assert resp.json()["status"] == "ok", resp.text
    assert _event_row(eid) == WebhookEventStatus.PROCESSED.value
    with TestingSessionLocal() as db:
        booking = db.query(Booking).filter_by(
            booking_reference=hold["booking_reference"]).one()
        assert booking.status == BookingStatus.CONFIRMED.value


def test_transient_failure_does_not_poison_event(client, monkeypatch):
    """First delivery fails transiently (502); retry of the same event
    settles normally — the failure must not permanently poison the event."""
    hold = _hold_via_api(client, "poison")
    order = _order(client, hold["booking_reference"])
    eid, headers, body = build_mock_success_event(
        order_id=order["provider_order_id"], amount=order["amount"])

    from app.services.payments import service as payment_service_mod
    calls = {"n": 0}
    real_transition = payment_service_mod.transition_booking

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient settlement failure")
        return real_transition(*args, **kwargs)

    monkeypatch.setattr(payment_service_mod, "transition_booking", flaky)
    assert _webhook(client, "mock", body, headers).status_code == 502
    retry = _webhook(client, "mock", body, headers)
    assert retry.json()["status"] == "ok", retry.text
    assert _event_row(eid) == WebhookEventStatus.PROCESSED.value
    with TestingSessionLocal() as db:
        booking = db.query(Booking).filter_by(
            booking_reference=hold["booking_reference"]).one()
        assert booking.status == BookingStatus.CONFIRMED.value


def test_concurrent_duplicate_webhooks_single_settlement(client):
    """Simultaneous redeliveries of the same event: exactly one settles,
    the rest observe the durable PROCESSED state. DB is the mutex."""
    hold = _hold_via_api(client, "crashconc")
    order = _order(client, hold["booking_reference"])
    eid, _h, body = build_mock_success_event(
        order_id=order["provider_order_id"], amount=order["amount"])

    def deliver(payload_bytes):
        with TestingSessionLocal() as db:
            svc = PaymentService(provider=MockProvider())
            event = svc.provider.parse_webhook(raw_body=payload_bytes)
            return svc.process_webhook_event(db, event)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(deliver, [body] * 4))
    outcomes = sorted(r["outcome"] for r in results)
    assert outcomes.count("confirmed") == 1, outcomes
    assert outcomes.count("already_processed") == 3, outcomes
    assert _event_row(eid) == WebhookEventStatus.PROCESSED.value
    with TestingSessionLocal() as db:
        booking = db.query(Booking).filter_by(
            booking_reference=hold["booking_reference"]).one()
        assert booking.status == BookingStatus.CONFIRMED.value
        assert booking.payment_status == PaymentStatus.PAID.value
        assert db.query(ShowSeat).filter_by(
            booking_id=booking.id,
            status=ShowSeatStatus.BOOKED.value).count() == 2
        assert db.query(PaymentWebhookEvent).filter_by(event_id=eid).count() == 1

        assert db.query(PaymentWebhookEvent).filter_by(event_id=eid).count() == 1

        seats = db.query(ShowSeat).filter_by(booking_id=booking.id).all()
        assert seats and all(s.status == ShowSeatStatus.BOOKED.value for s in seats)
