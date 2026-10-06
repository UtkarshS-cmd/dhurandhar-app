"""Razorpay adapter (Phase 4 — Razorpay-ready, credential-gated).

All Razorpay-specific code lives in this module. Nothing else in the backend
imports Razorpay SDK types. Live network calls are intentionally NOT made in
this phase: order creation synthesizes a deterministic ``order_RZP_…`` id from
the server-side (reference, attempt) WITHOUT contacting Razorpay, and
signature verification uses HMAC-SHA256 exactly as Razorpay documents.

Production selection (``PAYMENT_PROVIDER=razorpay``) fails fast unless
``RAZORPAY_KEY_ID``, ``RAZORPAY_KEY_SECRET`` and ``RAZORPAY_WEBHOOK_SECRET``
are all configured. The real ``razorpay`` SDK / HTTPS order creation is the
Phase 5 insertion point (replace ``_synthesized_order_id`` with the SDK call
— the boundary and webhook verification stay unchanged).
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from .base import PaymentOrder, PaymentProvider, PaymentVerification, ProviderError, WebhookEvent


def _synthesized_order_id(booking_reference: str, attempt_id: int) -> str:
    digest = hashlib.sha256(f"rzp:{booking_reference}:{attempt_id}".encode()).hexdigest()[:16]
    return f"order_RZP_{digest}"


def compute_webhook_signature(raw_body: bytes, webhook_secret: str) -> str:
    """Razorpay webhook signature: hex HMAC-SHA256 of the raw request body."""
    return hmac.new(webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest()


def compute_payment_signature(*, order_id: str, payment_id: str, key_secret: str) -> str:
    """Razorpay checkout signature: HMAC-SHA256 of ``order_id|payment_id``."""
    return hmac.new(key_secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


class RazorpayProvider(PaymentProvider):
    name = "razorpay"

    def __init__(self, *, key_id: str, key_secret: str, webhook_secret: str):
        if not key_id or not key_secret or not webhook_secret:
            raise ProviderError(
                "PAYMENT_PROVIDER=razorpay requires RAZORPAY_KEY_ID, "
                "RAZORPAY_KEY_SECRET and RAZORPAY_WEBHOOK_SECRET."
            )
        # Stored in memory only; never logged, never sent to clients.
        self._key_id = key_id
        self._key_secret = key_secret
        self._webhook_secret = webhook_secret

    @property
    def public_key_id(self) -> str:
        return self._key_id

    def create_order(self, *, amount: float, currency: str,
                     booking_reference: str, attempt_id: int) -> PaymentOrder:
        # Phase 4: no live HTTPS — synthesize a stable order id from
        # server-side inputs (amount comes from our DB, never the client).
        order_id = _synthesized_order_id(booking_reference, attempt_id)
        return PaymentOrder(
            provider="razorpay",
            provider_order_id=order_id,
            amount=amount,
            currency=currency,
            booking_reference=booking_reference,
            checkout={"key_id": self._key_id, "name": "Dhurandhar Cinema"},
        )

    def verify_payment(self, payload: dict[str, Any]) -> PaymentVerification:
        order_id = payload.get("provider_order_id") or payload.get("razorpay_order_id")
        payment_id = payload.get("provider_payment_id") or payload.get("razorpay_payment_id")
        signature = payload.get("signature") or payload.get("razorpay_signature")
        if not order_id or not payment_id or not signature:
            return PaymentVerification(ok=False, provider="razorpay",
                                       failure_reason="Missing Razorpay payment fields")
        expected = compute_payment_signature(
            order_id=str(order_id), payment_id=str(payment_id), key_secret=self._key_secret)
        if not hmac.compare_digest(expected, str(signature)):
            return PaymentVerification(ok=False, provider="razorpay",
                                       failure_reason="Invalid Razorpay payment signature")
        return PaymentVerification(ok=True, provider="razorpay",
                                   provider_order_id=str(order_id),
                                   provider_payment_id=str(payment_id))

    def verify_webhook(self, *, raw_body: bytes, headers: dict[str, str]) -> bool:
        lowered = {k.lower(): v for k, v in headers.items()}
        signature = lowered.get("x-razorpay-signature", "")
        if not signature:
            return False
        return hmac.compare_digest(compute_webhook_signature(raw_body, self._webhook_secret), str(signature))

    def parse_webhook(self, *, raw_body: bytes) -> WebhookEvent:
        payload = json.loads(raw_body.decode("utf-8"))
        event = str(payload.get("event", ""))
        entity = ((payload.get("payload") or {}).get("payment") or {}).get("entity") or {}
        paise = entity.get("amount")
        if event in ("payment.captured", "order.paid"):
            outcome = "paid"
        elif event in ("payment.failed", "order.failed"):
            outcome = "failed"
        elif event in ("payment.authorized", "order.authorized"):
            outcome = "authorized"
        else:
            outcome = "unknown"
        return WebhookEvent(
            provider="razorpay",
            event_id=str(payload.get("id") or payload.get("event_id") or ""),
            event_type=event,
            provider_order_id=entity.get("order_id"),
            provider_payment_id=entity.get("id"),
            amount=(paise / 100.0) if isinstance(paise, (int, float)) else None,
            currency=entity.get("currency"),
            outcome=outcome,
        )
