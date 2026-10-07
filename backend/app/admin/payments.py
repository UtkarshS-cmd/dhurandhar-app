"""Admin payment visibility (read-only; state machine never mutated here)."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_admin
from ..models import Booking, PaymentAttempt, PaymentWebhookEvent, User
from ..ratelimit import rate_limit
from .common import paginate

router = APIRouter(prefix="/api/admin", tags=["admin-payments"])


def _serialize_attempt(attempt: PaymentAttempt, booking_reference: str | None) -> dict:
    return {
        "id": attempt.id, "booking_id": attempt.booking_id,
        "booking_reference": booking_reference, "provider": attempt.provider,
        "provider_order_id": attempt.provider_order_id,
        "provider_payment_id": attempt.provider_payment_id,
        "amount": attempt.amount, "currency": attempt.currency,
        "status": attempt.status, "attempt_no": attempt.attempt_no,
        "failure_code": attempt.failure_code, "failure_reason": attempt.failure_reason,
        "created_at": attempt.created_at, "updated_at": attempt.updated_at,
    }


@router.get("/payments", dependencies=[Depends(rate_limit("admin_read"))])
def list_payments(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    status: str | None = Query(None, max_length=20),
    provider: str | None = Query(None, max_length=30),
    search: str | None = Query(None, max_length=64),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    query = (select(PaymentAttempt, Booking.booking_reference)
             .outerjoin(Booking, PaymentAttempt.booking_id == Booking.id))
    count_query = select(func.count(PaymentAttempt.id))
    filters = []
    if status:
        filters.append(PaymentAttempt.status == status.strip().upper())
    if provider:
        filters.append(PaymentAttempt.provider == provider.strip().lower())
    if search:
        filters.append(func.lower(Booking.booking_reference).like(f"%{search.strip().lower()}%"))
    if filters:
        query = query.where(*filters)
        count_query = count_query.where(*filters)
    if search:
        count_query = select(func.count(PaymentAttempt.id)).select_from(PaymentAttempt).outerjoin(
            Booking, PaymentAttempt.booking_id == Booking.id).where(*filters)
    query = query.order_by(PaymentAttempt.created_at.desc(), PaymentAttempt.id.desc())
    total = int(db.scalar(count_query) or 0)
    rows = db.execute(query.offset((page - 1) * page_size).limit(page_size)).all()
    items = [_serialize_attempt(a, ref) for a, ref in rows]
    return paginate(items, total, page, page_size)


@router.get("/payment-attempts/{attempt_id}", dependencies=[Depends(rate_limit("admin_read"))])
def get_attempt(attempt_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    attempt = db.get(PaymentAttempt, attempt_id)
    if not attempt:
        raise HTTPException(404, "Payment attempt not found")
    booking = db.get(Booking, attempt.booking_id)
    events = db.scalars(select(PaymentWebhookEvent).where(
        PaymentWebhookEvent.payment_attempt_id == attempt.id).order_by(
        PaymentWebhookEvent.received_at.desc())).all()
    payload = _serialize_attempt(attempt, booking.booking_reference if booking else None)
    payload["webhook_events"] = [
        {"id": e.id, "provider": e.provider, "event_id": e.event_id, "event_type": e.event_type,
         "status": e.status, "received_at": e.received_at, "processed_at": e.processed_at}
        for e in events
    ]
    return payload
