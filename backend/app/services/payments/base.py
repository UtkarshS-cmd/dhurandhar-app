"""Normalized payment-provider boundary (Phase 4).

Application code operates only on the dataclasses below. Provider SDK types
must never leak past the adapter that produced them.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PaymentOrder:
    provider: str
    provider_order_id: str
    amount: float
    currency: str = "INR"
    booking_reference: str = ""
    # Only provider checkout hints (e.g. public key id / display name).
    # Never secrets.
    checkout: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PaymentVerification:
    ok: bool
    provider: str = ""
    provider_order_id: str | None = None
    provider_payment_id: str | None = None
    amount: float | None = None
    currency: str | None = None
    failure_code: str | None = None
    failure_reason: str | None = None


@dataclass(frozen=True)
class WebhookEvent:
    provider: str
    event_id: str
    event_type: str
    provider_order_id: str | None = None
    provider_payment_id: str | None = None
    amount: float | None = None
    currency: str | None = None
    # "paid" | "failed" | "unknown" — normalized outcome, never raw payload.
    outcome: str = "unknown"


class ProviderError(RuntimeError):
    """Provider outage / misconfiguration — booking stays retryable."""


class PaymentProvider(ABC):
    """Integration boundary for an external payment provider."""

    name: str = "base"

    @abstractmethod
    def create_order(self, *, amount: float, currency: str,
                     booking_reference: str, attempt_id: int) -> PaymentOrder:
        ...

    @abstractmethod
    def verify_payment(self, payload: dict[str, Any]) -> PaymentVerification:
        """Verify a client-returned payment callback (signature-checked)."""

    @abstractmethod
    def verify_webhook(self, *, raw_body: bytes, headers: dict[str, str]) -> bool:
        """Return True only if the webhook signature is valid."""

    @abstractmethod
    def parse_webhook(self, *, raw_body: bytes) -> WebhookEvent:
        """Normalize a verified webhook body into a WebhookEvent."""

    def refund(self, *, provider_payment_id: str, amount: float | None = None) -> dict[str, Any]:
        raise NotImplementedError("refunds are not required in Phase 4")
