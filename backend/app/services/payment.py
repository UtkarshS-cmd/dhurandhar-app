
"""Legacy sync payment gateway — Phase 3 compatibility shim (Phase 4).

Phase 3 tests and the mock checkout call ``PaymentGateway().charge()``;
that contract is frozen. New code must use
``app.services.payments.service.PaymentService`` + ``PaymentProvider``
instead. This shim delegates mock behavior to ``MockProvider`` semantics
(same outcomes as Phase 3) and never performs network I/O.
"""
from dataclasses import dataclass

from ..config import settings


@dataclass
class PaymentResult:
    status: str
    provider_reference: str | None = None


class PaymentGateway:
    """Synchronous mock charge (development/tests + Pay-at-Counter only)."""

    def charge(self, amount: float, method: str) -> PaymentResult:
        if method == "Pay at Counter":
            return PaymentResult(status="PENDING", provider_reference=None)
        provider = (getattr(settings, "payment_provider", "") or "mock").strip().lower()
        if provider == "mock" or settings.payment_mode == "mock":
            return PaymentResult(status="PAID", provider_reference="MOCK-LOCAL")
        raise RuntimeError("No real payment provider configured")


payment_gateway = PaymentGateway()

