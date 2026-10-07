"""Admin ops inbox: contact messages + newsletter subscribers (read-mostly)."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_admin
from ..models import ContactMessage, NewsletterSubscriber, User
from ..ratelimit import rate_limit
from .common import audit, paginate

router = APIRouter(prefix="/api/admin", tags=["admin-ops"])


@router.get("/contact-messages", dependencies=[Depends(rate_limit("admin_read"))])
def list_contacts(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    search: str | None = Query(None, max_length=120),
    is_read: bool | None = Query(None),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    query = select(ContactMessage)
    count_query = select(func.count(ContactMessage.id))
    filters = []
    if search:
        like = f"%{search.strip().lower()}%"
        filters.append(or_(func.lower(ContactMessage.email).like(like),
                           func.lower(ContactMessage.name).like(like)))
    if is_read is not None:
        filters.append(ContactMessage.is_read == is_read)
    if filters:
        query = query.where(*filters)
        count_query = count_query.where(*filters)
    query = query.order_by(ContactMessage.created_at.desc(), ContactMessage.id.desc())
    total = int(db.scalar(count_query) or 0)
    rows = db.scalars(query.offset((page - 1) * page_size).limit(page_size)).all()
    items = [{"id": m.id, "name": m.name, "email": m.email, "subject": m.subject,
              "message": m.message, "is_read": bool(m.is_read),
              "created_at": m.created_at} for m in rows]
    return paginate(items, total, page, page_size)


@router.patch("/contact-messages/{message_id}", dependencies=[Depends(rate_limit("admin_write"))])
def mark_contact(message_id: int, payload: dict, db: Session = Depends(get_db),
                 admin: User = Depends(require_admin)):
    msg = db.get(ContactMessage, message_id)
    if not msg:
        raise HTTPException(404, "Contact message not found")
    data = payload or {}
    if "is_read" not in data or not isinstance(data["is_read"], bool):
        raise HTTPException(422, "is_read (boolean) is required")
    msg.is_read = data["is_read"]
    audit(db, admin_user_id=admin.id, action="CONTACT_MARKED_READ", resource_type="contact_message",
          resource_id=msg.id, metadata={"is_read": bool(msg.is_read)})
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return {"id": msg.id, "is_read": bool(msg.is_read)}


@router.get("/newsletter-subscribers", dependencies=[Depends(rate_limit("admin_read"))])
def list_subscribers(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    search: str | None = Query(None, max_length=120),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    query = select(NewsletterSubscriber)
    count_query = select(func.count(NewsletterSubscriber.id))
    if search:
        like = f"%{search.strip().lower()}%"
        query = query.where(func.lower(NewsletterSubscriber.email).like(like))
        count_query = count_query.where(func.lower(NewsletterSubscriber.email).like(like))
    query = query.order_by(NewsletterSubscriber.created_at.desc(), NewsletterSubscriber.id.desc())
    total = int(db.scalar(count_query) or 0)
    rows = db.scalars(query.offset((page - 1) * page_size).limit(page_size)).all()
    items = [{"id": s.id, "name": s.name, "email": s.email, "created_at": s.created_at} for s in rows]
    return paginate(items, total, page, page_size)
