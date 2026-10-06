"""Phase 4 — provider-agnostic payment package.

The cinema backend owns booking/payment state; external providers are only
an integration boundary behind :class:`base.PaymentProvider`. Application
code depends on the normalized dataclasses and :class:`service.PaymentService`
— never on provider SDK types.
"""
from .service import PaymentService, get_payment_service

__all__ = ["PaymentService", "get_payment_service"]