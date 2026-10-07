"""Admin audit-log reads (append-only; no write/delete endpoints exist)."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_admin
from ..models import AdminAuditLog, User
from ..ratelimit import rate_limit
from .common import paginate

router = APIRouter(prefix="/api/admin/audit-logs", tags=["admin-audit"])


@router.get("", dependencies=[Depends(rate_limit("admin_read"))])
def list_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    action: str | None = Query(None, max_length=60),
    resource_type: str | None = Query(None, max_length=60),
    admin_user_id: int | None = Query(None, ge=1),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    query = select(AdminAuditLog, User.email).outerjoin(User, AdminAuditLog.admin_user_id == User.id)
    count_query = select(func.count(AdminAuditLog.id))
    filters = []
    if action:
        filters.append(AdminAuditLog.action == action.strip().upper())
    if resource_type:
        filters.append(AdminAuditLog.resource_type == resource_type.strip().lower())
    if admin_user_id:
        filters.append(AdminAuditLog.admin_user_id == admin_user_id)
    if filters:
        query = query.where(*filters)
        count_query = count_query.where(*filters)
    query = query.order_by(AdminAuditLog.created_at.desc(), AdminAuditLog.id.desc())
    total = int(db.scalar(count_query) or 0)
    rows = db.execute(query.offset((page - 1) * page_size).limit(page_size)).all()
    items = [{"id": log.id, "admin_user_id": log.admin_user_id, "admin_email": email,
              "action": log.action, "resource_type": log.resource_type,
              "resource_id": log.resource_id, "metadata_json": log.metadata_json,
              "created_at": log.created_at} for log, email in rows]
    return paginate(items, total, page, page_size)
