"""Mock payment provider (Phase 4).

Deterministic, credential-free, network-free. Used for local development and
all tests. Produces stable order/payment ids derived from (booking reference,
attempt) so retries and redeliveries are reproducible.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from .base import PaymentOrder, PaymentProvider, PaymentVerification, WebhookEvent

MOCK_SIGNING_KEY = b"dhurandhar-mock-dev-only"


def _mock_order_id(booking_reference: str, attempt_id: int) -> str:
    digest = hashlib.sha256(f"{booking_reference}:{attempt_id}".encode()).hexdigest()[:16]
    return f"order_MOCK_{digest}"


def _mock_payment_id(order_id: str) -> str:
    digest = hashlib.sha256(f"pay:{order_id}".encode()).hexdigest()[:16]
    return f"pay_MOCK_{digest}"


def sign_mock_payload(payload: dict[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(MOCK_SIGNING_KEY, body, hashlib.sha256).hexdigest()


def build_mock_success_event(*, order_id: str, amount: float, currency: str = "INR",
                             event_id: str | None = None) -> tuple[str, dict[str, str], bytes]:
    """Build a signed mock ``payment.captured`` webhook (tests/dev only)."""
    eid = event_id or f"evt_MOCK_{hashlib.sha256(order_id.encode()).hexdigest()[:12]}"
    payload = {
        "event_id": eid,
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "order_id": order_id,
                    "id": _mock_payment_id(order_id),
                    "amount": int(round(amount * 100)),
                    "currency": currency,
                }
            }
        },
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    return eid, {"x-mock-signature": sign_mock_payload(payload)}, body


def build_mock_failure_event(*, order_id: str, amount: float, currency: str = "INR",
                             event_id: str | None = None,
                             code: str = "PAYMENT_FAILED") -> tuple[str, dict[str, str], bytes]:
    eid = event_id or f"evt_MOCK_{hashlib.sha256(('fail:' + order_id).encode()).hexdigest()[:12]}"
    payload = {
        "event_id": eid,
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "order_id": order_id,
                    "id": _mock_payment_id(order_id),
                    "amount": int(round(amount * 100)),
                    "currency": currency,
                    "error_code": code,
                    "error_description": "Mock payment declined",
                }
            }
        },
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    return eid, {"x-mock-signature": sign_mock_payload(payload)}, body


class MockProvider(PaymentProvider):
    name = "mock"

    def create_order(self, *, amount: float, currency: str,
                     booking_reference: str, attempt_id: int) -> PaymentOrder:
        order_id = _mock_order_id(booking_reference, attempt_id)
        return PaymentOrder(
            provider="mock",
            provider_order_id=order_id,
            amount=amount,
            currency=currency,
            booking_reference=booking_reference,
            checkout={"mock_payment_id": _mock_payment_id(order_id)},
        )

    def verify_payment(self, payload: dict[str, Any]) -> PaymentVerification:
        order_id = payload.get("provider_order_id") or payload.get("order_id")
        payment_id = payload.get("provider_payment_id") or payload.get("payment_id")
        signature = payload.get("signature") or ""
        if not order_id or not payment_id or not signature:
            return PaymentVerification(ok=False, provider="mock", failure_reason="Missing payment fields")
        expected = hmac.new(
            MOCK_SIGNING_KEY, f"{order_id}|{payment_id}".encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, str(signature)):
            return PaymentVerification(ok=False, provider="mock", failure_reason="Invalid mock signature")
        return PaymentVerification(ok=True, provider="mock",
                                   provider_order_id=order_id, provider_payment_id=payment_id)

    def verify_webhook(self, *, raw_body: bytes, headers: dict[str, str]) -> bool:
        lowered = {k.lower(): v for k, v in headers.items()}
        signature = lowered.get("x-mock-signature", "")
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except Exception:
            return False
        return hmac.compare_digest(sign_mock_payload(payload), str(signature))

    def parse_webhook(self, *, raw_body: bytes) -> WebhookEvent:
        payload = json.loads(raw_body.decode("utf-8"))
        event = str(payload.get("event", ""))
        entity = ((payload.get("payload") or {}).get("payment") or {}).get("entity") or {}
        paise = entity.get("amount")
        outcome = "paid" if event == "payment.captured" else ("failed" if event == "payment.failed" else "unknown")
        return WebhookEvent(
            provider="mock",
            event_id=str(payload.get("event_id", "")),
            event_type=event,
            provider_order_id=entity.get("order_id"),
            provider_payment_id=entity.get("id"),
            amount=(paise / 100.0) if isinstance(paise, (int, float)) else None,
            currency=entity.get("currency"),
            outcome=outcome,
        )
