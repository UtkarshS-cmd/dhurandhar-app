"""Phase 4 — payment architecture: provider abstraction, orders, webhooks."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest

from app.config import Settings
from app.models import (
    Booking, BookingStatus, PaymentAttempt, PaymentAttemptStatus,
    PaymentStatus, PaymentWebhookEvent, ShowSeat, ShowSeatStatus)
from app.services.booking import InvalidTransition
from app.services.payments.base import ProviderError
from app.services.payments.mock import (
    MockProvider, build_mock_failure_event, build_mock_success_event)
from app.services.payments.razorpay import (
    RazorpayProvider, compute_payment_signature, compute_webhook_signature)
from app.services.payments.service import (
    PAYMENT_ATTEMPT_TRANSITIONS, PaymentService, get_provider, transition_attempt)
from conftest import TestingSessionLocal, make_user


def _hold_via_api(client, prefix="pay4"):
    show_id = client.show_id
    seats = client.get(f"/api/shows/{show_id}/seats").json()
    avail = [s for s in seats if s["status"] == "AVAILABLE" and s["category"] == "Silver"][:2]
    assert len(avail) == 2
    ids = [s["id"] for s in avail]
    resp = client.post("/api/bookings/hold", json={
        "user": make_user(prefix), "show_id": show_id,
        "seat_ids": ids, "payment_method": "PENDING"})
    assert resp.status_code == 201
    return resp.json()


def _order(client, reference):
    resp = client.post("/api/payments/orders", json={"booking_reference": reference})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _webhook(client, provider, body, headers):
    return client.post(f"/api/payments/webhook/{provider}", content=body, headers=headers)


def test_provider_selection_mock_default():
    assert get_provider(Settings()).name == "mock"
    assert get_provider().name == "mock"


def test_missing_razorpay_config_fails_safely():
    import dataclasses
    cfg = dataclasses.replace(
        Settings(), payment_provider="razorpay",
        razorpay_key_id="", razorpay_key_secret="", razorpay_webhook_secret="")
    with pytest.raises(ProviderError):
        get_provider(cfg)
    with pytest.raises(ProviderError):
        RazorpayProvider(key_id="", key_secret="s", webhook_secret="w")


def test_razorpay_adapter_ok_with_credentials():
    provider = RazorpayProvider(key_id="rzp_test_pub", key_secret="sec", webhook_secret="whsec")
    order = provider.create_order(amount=700.0, currency="INR",
                                  booking_reference="DHR-x", attempt_id=1)
    assert order.provider_order_id.startswith("order_RZP_")
    assert order.checkout["key_id"] == "rzp_test_pub"
    assert "sec" not in json.dumps(order.checkout)


def test_mock_order_requires_no_credentials(client):
    hold = _hold_via_api(client)
    order = _order(client, hold["booking_reference"])
    assert order["provider"] == "mock"
    assert order["provider_order_id"].startswith("order_MOCK_")
    assert order["amount"] == pytest.approx(float(hold["total_amount"]))


def test_order_amount_comes_from_db(client):
    hold = _hold_via_api(client)
    order = _order(client, hold["booking_reference"])
    assert order["amount"] == pytest.approx(700.0)
    assert order["currency"] == "INR"
    with TestingSessionLocal() as db:
        attempt = db.query(PaymentAttempt).filter_by(
            provider_order_id=order["provider_order_id"]).one()
        assert float(attempt.amount) == pytest.approx(700.0)


def test_successful_webhook_confirms_booking(client):
    hold = _hold_via_api(client)
    order = _order(client, hold["booking_reference"])
    eid, headers, body = build_mock_success_event(
        order_id=order["provider_order_id"], amount=order["amount"])
    resp = _webhook(client, "mock", body, headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ok"
    with TestingSessionLocal() as db:
        booking = db.query(Booking).filter_by(
            booking_reference=hold["booking_reference"]).one()
        assert booking.status == BookingStatus.CONFIRMED.value
        assert booking.payment_status == PaymentStatus.PAID.value
        assert booking.hold_expires_at is None
        attempt = db.query(PaymentAttempt).filter_by(
            provider_order_id=order["provider_order_id"]).one()
        assert attempt.status == PaymentAttemptStatus.PAID.value
        assert attempt.provider_payment_id is not None
        seats = db.query(ShowSeat).filter_by(booking_id=booking.id).all()
        assert seats and all(s.status == ShowSeatStatus.BOOKED.value for s in seats)
        evt = db.query(PaymentWebhookEvent).filter_by(event_id=eid).one()
        assert evt.status == "PROCESSED"


def test_duplicate_webhook_is_idempotent(client):
    hold = _hold_via_api(client, "dupwh")
    order = _order(client, hold["booking_reference"])
    eid, headers, body = build_mock_success_event(
        order_id=order["provider_order_id"], amount=order["amount"])
    first = _webhook(client, "mock", body, headers)
    second = _webhook(client, "mock", body, headers)
    assert first.status_code == 200 and second.status_code == 200
    assert second.json()["status"] == "already_processed"
    with TestingSessionLocal() as db:
        assert db.query(PaymentAttempt).filter_by(
            provider_order_id=order["provider_order_id"]).count() == 1
        assert db.query(PaymentWebhookEvent).filter_by(event_id=eid).count() == 1

def test_invalid_webhook_signature_rejected(client):
    hold = _hold_via_api(client, "badsig")
    order = _order(client, hold["booking_reference"])
    _e, _h, body = build_mock_success_event(
        order_id=order["provider_order_id"], amount=order["amount"])
    resp = _webhook(client, "mock", body, {"x-mock-signature": "deadbeef"})
    assert resp.status_code == 401
    with TestingSessionLocal() as db:
        booking = db.query(Booking).filter_by(
            booking_reference=hold["booking_reference"]).one()
        assert booking.status == BookingStatus.HELD.value
        assert db.query(PaymentWebhookEvent).count() == 0


def test_wrong_provider_order_rejected(client):
    hold = _hold_via_api(client, "badord")
    _order(client, hold["booking_reference"])
    _e, headers, body = build_mock_success_event(order_id="order_MOCK_nope", amount=700.0)
    resp = _webhook(client, "mock", body, headers)
    assert resp.json()["status"] == "ignored"
    with TestingSessionLocal() as db:
        booking = db.query(Booking).filter_by(
            booking_reference=hold["booking_reference"]).one()
        assert booking.status == BookingStatus.HELD.value


def test_wrong_amount_rejected(client):
    hold = _hold_via_api(client, "badamt")
    order = _order(client, hold["booking_reference"])
    _e, headers, body = build_mock_success_event(
        order_id=order["provider_order_id"], amount=1.0)
    assert _webhook(client, "mock", body, headers).json()["status"] == "ignored"
    with TestingSessionLocal() as db:
        booking = db.query(Booking).filter_by(
            booking_reference=hold["booking_reference"]).one()
        assert booking.status == BookingStatus.HELD.value
        attempt = db.query(PaymentAttempt).filter_by(
            provider_order_id=order["provider_order_id"]).one()
        assert attempt.status == PaymentAttemptStatus.FAILED.value
        assert attempt.failure_code == "AMOUNT_MISMATCH"


def test_wrong_currency_rejected(client):
    hold = _hold_via_api(client, "badcur")
    order = _order(client, hold["booking_reference"])
    _e, headers, body = build_mock_success_event(
        order_id=order["provider_order_id"], amount=order["amount"], currency="USD")
    assert _webhook(client, "mock", body, headers).json()["status"] == "ignored"
    with TestingSessionLocal() as db:
        attempt = db.query(PaymentAttempt).filter_by(
            provider_order_id=order["provider_order_id"]).one()
        assert attempt.status == PaymentAttemptStatus.FAILED.value


def test_webhook_for_cancelled_booking_does_not_resurrect(client):
    hold = _hold_via_api(client, "cancel")
    order = _order(client, hold["booking_reference"])
    with TestingSessionLocal() as db:
        booking = db.query(Booking).filter_by(
            booking_reference=hold["booking_reference"]).one()
        past = datetime.utcnow() - timedelta(minutes=1)
        booking.hold_expires_at = past
        for ss in db.query(ShowSeat).filter_by(booking_id=booking.id):
            ss.hold_expires_at = past
        db.commit()
    client.get(f"/api/bookings/{hold['booking_reference']}")
    _e, headers, body = build_mock_success_event(
        order_id=order["provider_order_id"], amount=order["amount"])
    resp = _webhook(client, "mock", body, headers)
    assert resp.json()["status"] == "already_processed"
    with TestingSessionLocal() as db:
        booking = db.query(Booking).filter_by(
            booking_reference=hold["booking_reference"]).one()
        assert booking.status == BookingStatus.CANCELLED.value
        assert booking.payment_status != PaymentStatus.PAID.value


def test_payment_failure_does_not_confirm(client):
    hold = _hold_via_api(client, "fail")
    order = _order(client, hold["booking_reference"])
    _e, headers, body = build_mock_failure_event(
        order_id=order["provider_order_id"], amount=order["amount"])
    assert _webhook(client, "mock", body, headers).json()["status"] == "ok"
    with TestingSessionLocal() as db:
        booking = db.query(Booking).filter_by(
            booking_reference=hold["booking_reference"]).one()
        assert booking.status == BookingStatus.HELD.value
        assert booking.payment_status == PaymentStatus.FAILED.value



def test_provider_outage_does_not_corrupt_booking(client, monkeypatch):
    hold = _hold_via_api(client, "outage")
    from app.services.payments import mock as mock_mod

    def boom(self, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(mock_mod.MockProvider, "create_order", boom)
    resp = client.post("/api/payments/orders",
                       json={"booking_reference": hold["booking_reference"]})
    assert resp.status_code == 502
    with TestingSessionLocal() as db:
        booking = db.query(Booking).filter_by(
            booking_reference=hold["booking_reference"]).one()
        assert booking.status == BookingStatus.HELD.value
        assert booking.payment_status == PaymentStatus.PENDING.value


def test_retry_creates_new_attempt_safely(client):
    hold = _hold_via_api(client, "retry")
    first = _order(client, hold["booking_reference"])
    _e, headers, body = build_mock_failure_event(
        order_id=first["provider_order_id"], amount=first["amount"])
    _webhook(client, "mock", body, headers)
    resp = client.post("/api/payments/retry",
                       json={"booking_reference": hold["booking_reference"]})
    assert resp.status_code == 200
    second = resp.json()
    assert second["provider_order_id"] != first["provider_order_id"]
    with TestingSessionLocal() as db:
        booking = db.query(Booking).filter_by(
            booking_reference=hold["booking_reference"]).one()
        attempts = db.query(PaymentAttempt).filter_by(
            booking_id=booking.id).order_by(PaymentAttempt.attempt_no).all()
        assert [a.status for a in attempts] == [
            PaymentAttemptStatus.FAILED.value, PaymentAttemptStatus.PENDING.value]
    _e2, h2, b2 = build_mock_success_event(
        order_id=second["provider_order_id"], amount=second["amount"])
    assert _webhook(client, "mock", b2, h2).json()["status"] == "ok"
    with TestingSessionLocal() as db:
        booking = db.query(Booking).filter_by(
            booking_reference=hold["booking_reference"]).one()
        assert booking.status == BookingStatus.CONFIRMED.value


def test_duplicate_checkout_request_reuses_attempt(client):
    hold = _hold_via_api(client, "dupord")
    first = _order(client, hold["booking_reference"])
    second = _order(client, hold["booking_reference"])
    assert first["provider_order_id"] == second["provider_order_id"]
    with TestingSessionLocal() as db:
        booking = db.query(Booking).filter_by(
            booking_reference=hold["booking_reference"]).one()
        assert db.query(PaymentAttempt).filter_by(booking_id=booking.id).count() == 1


def test_success_updates_atomically(client):
    hold = _hold_via_api(client, "atomic")
    order = _order(client, hold["booking_reference"])
    _e, headers, body = build_mock_success_event(
        order_id=order["provider_order_id"], amount=order["amount"])
    assert _webhook(client, "mock", body, headers).status_code == 200
    with TestingSessionLocal() as db:
        booking = db.query(Booking).filter_by(
            booking_reference=hold["booking_reference"]).one()
        attempt = db.query(PaymentAttempt).filter_by(
            provider_order_id=order["provider_order_id"]).one()
        assert (attempt.status, booking.status, booking.payment_status) == (
            PaymentAttemptStatus.PAID.value, BookingStatus.CONFIRMED.value,
            PaymentStatus.PAID.value)
        assert all(s.status == ShowSeatStatus.BOOKED.value
                   for s in db.query(ShowSeat).filter_by(booking_id=booking.id))


def test_forced_db_failure_rolls_back(client, monkeypatch):
    hold = _hold_via_api(client, "rollback")
    order = _order(client, hold["booking_reference"])
    _e, headers, body = build_mock_success_event(
        order_id=order["provider_order_id"], amount=order["amount"])

    def boom(*args, **kwargs):
        raise RuntimeError("forced db failure")

    from app.services.payments import service as payment_service_mod
    monkeypatch.setattr(payment_service_mod, "transition_booking", boom)
    assert _webhook(client, "mock", body, headers).status_code == 502
    with TestingSessionLocal() as db:
        booking = db.query(Booking).filter_by(
            booking_reference=hold["booking_reference"]).one()
        assert booking.status == BookingStatus.HELD.value
        assert booking.payment_status == PaymentStatus.PENDING.value
        attempt = db.query(PaymentAttempt).filter_by(
            provider_order_id=order["provider_order_id"]).one()
        assert attempt.status != PaymentAttemptStatus.PAID.value


def test_already_paid_cannot_be_paid_again(client):
    import hashlib
    hold = _hold_via_api(client, "repay")
    order = _order(client, hold["booking_reference"])
    _e, headers, body = build_mock_success_event(
        order_id=order["provider_order_id"], amount=order["amount"])
    assert _webhook(client, "mock", body, headers).json()["status"] == "ok"
    eid2 = "evt_MOCK_replay_" + hashlib.sha256(
        order["provider_order_id"].encode()).hexdigest()[:8]
    _e2, h2, b2 = build_mock_success_event(
        order_id=order["provider_order_id"], amount=order["amount"], event_id=eid2)
    assert _webhook(client, "mock", b2, h2).json()["status"] == "already_processed"
    with TestingSessionLocal() as db:
        booking = db.query(Booking).filter_by(
            booking_reference=hold["booking_reference"]).one()
        assert booking.status == BookingStatus.CONFIRMED.value


def test_terminal_booking_cannot_be_resurrected(client):
    hold = _hold_via_api(client, "term")
    order = _order(client, hold["booking_reference"])
    _e, headers, body = build_mock_success_event(
        order_id=order["provider_order_id"], amount=order["amount"])
    assert _webhook(client, "mock", body, headers).json()["status"] == "ok"
    resp = client.post(f"/api/bookings/{hold['booking_reference']}/confirm",
                       json={"payment_method": "PENDING"})
    assert resp.status_code == 409


def test_payment_attempt_state_machine_guards():
    assert "PAID" not in PAYMENT_ATTEMPT_TRANSITIONS["FAILED"]
    assert "PENDING" not in PAYMENT_ATTEMPT_TRANSITIONS["PAID"]
    fake = PaymentAttempt(booking_id=1, provider="mock", amount=1.0,
                          currency="INR", status=PaymentAttemptStatus.PAID.value,
                          attempt_no=1)
    with pytest.raises(InvalidTransition):
        transition_attempt(fake, PaymentAttemptStatus.FAILED.value)
    with pytest.raises(InvalidTransition):
        transition_attempt(fake, PaymentAttemptStatus.PENDING.value)
    cancelled = PaymentAttempt(booking_id=1, provider="mock", amount=1.0,
                               currency="INR",
                               status=PaymentAttemptStatus.CANCELLED.value, attempt_no=2)
    with pytest.raises(InvalidTransition):
        transition_attempt(cancelled, PaymentAttemptStatus.PAID.value)


def test_webhook_idempotent_after_restart(client):
    hold = _hold_via_api(client, "restart")
    order = _order(client, hold["booking_reference"])
    eid, headers, body = build_mock_success_event(
        order_id=order["provider_order_id"], amount=order["amount"])
    assert _webhook(client, "mock", body, headers).status_code == 200
    from app.db import SessionLocal
    fresh = SessionLocal()
    try:
        evt = fresh.query(PaymentWebhookEvent).filter_by(event_id=eid).one_or_none()
    finally:
        fresh.close()
    assert evt is None or evt.status in ("PROCESSED", "RECEIVED")
    assert _webhook(client, "mock", body, headers).json()["status"] == "already_processed"


def test_concurrent_webhook_delivery_single_confirmation(client):
    hold = _hold_via_api(client, "conc")
    order = _order(client, hold["booking_reference"])
    _e, _h, body = build_mock_success_event(
        order_id=order["provider_order_id"], amount=order["amount"])

    def deliver(payload_bytes):
        with TestingSessionLocal() as db:
            svc = PaymentService(provider=MockProvider())
            event = svc.provider.parse_webhook(raw_body=payload_bytes)
            return svc.process_webhook_event(db, event)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(deliver, [body] * 4))
    outcomes = sorted(r["outcome"] for r in results)
    assert outcomes.count("confirmed") == 1
    assert outcomes.count("already_processed") == 3
    with TestingSessionLocal() as db:
        booking = db.query(Booking).filter_by(
            booking_reference=hold["booking_reference"]).one()
        assert booking.status == BookingStatus.CONFIRMED.value


def test_razorpay_webhook_signature_and_payment_checks():
    provider = RazorpayProvider(key_id="kid", key_secret="ksec", webhook_secret="whsec")
    order = provider.create_order(amount=700.0, currency="INR",
                                  booking_reference="DHR-t", attempt_id=3)
    payload = {"id": "evt_rzp_1", "event": "payment.captured",
               "payload": {"payment": {"entity": {
                   "order_id": order.provider_order_id, "id": "pay_123",
                   "amount": 70000, "currency": "INR"}}}}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    sig = compute_webhook_signature(raw, "whsec")
    assert provider.verify_webhook(raw_body=raw, headers={"X-Razorpay-Signature": sig})
    assert not provider.verify_webhook(raw_body=raw, headers={"X-Razorpay-Signature": "bad"})
    event = provider.parse_webhook(raw_body=raw)
    assert event.outcome == "paid" and event.amount == pytest.approx(700.0)
    good = compute_payment_signature(
        order_id=order.provider_order_id, payment_id="pay_123", key_secret="ksec")
    assert provider.verify_payment({
        "razorpay_order_id": order.provider_order_id,
        "razorpay_payment_id": "pay_123", "razorpay_signature": good}).ok
    assert not provider.verify_payment({
        "razorpay_order_id": order.provider_order_id,
        "razorpay_payment_id": "pay_123", "razorpay_signature": "bad"}).ok


def test_client_cannot_override_payment_amount(client):
    """A spoofed `amount` field in the order request is ignored — the schema
    has no amount field and the amount always comes from the booking row."""
    hold = _hold_via_api(client, "spoof")
    resp = client.post("/api/payments/orders", json={
        "booking_reference": hold["booking_reference"], "amount": 0.01,
        "currency": "USD", "status": "PAID", "provider_order_id": "evil"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["amount"] == pytest.approx(700.0)   # server-priced, not 0.01
    assert body["currency"] == "INR"
    assert body["provider_order_id"] != "evil"
    with TestingSessionLocal() as db:
        attempt = db.query(PaymentAttempt).filter_by(
            provider_order_id=body["provider_order_id"]).one()
        assert float(attempt.amount) == pytest.approx(700.0)
        assert attempt.status == PaymentAttemptStatus.PENDING.value
        booking = db.query(Booking).filter_by(
            booking_reference=hold["booking_reference"]).one()
        assert booking.status == BookingStatus.HELD.value  # spoofed PAID ignored
        assert booking.payment_status == PaymentStatus.PENDING.value
