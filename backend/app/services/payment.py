
from dataclasses import dataclass
from . import __name__ as _unused
from ..config import settings

@dataclass
class PaymentResult:
    status: str
    provider_reference: str | None = None

class PaymentGateway:
    def charge(self, amount: float, method: str) -> PaymentResult:
        if method == "Pay at Counter":
            return PaymentResult(status="PENDING", provider_reference=None)
        if settings.payment_mode == "mock":
            return PaymentResult(status="PAID", provider_reference="MOCK-LOCAL")
        raise RuntimeError("No real payment provider configured")

payment_gateway = PaymentGateway()
