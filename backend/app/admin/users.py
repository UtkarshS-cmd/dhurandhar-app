"""Admin user management: list/detail/activate/deactivate/role."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_admin
from ..models import Booking, Review, User, UserRole
from ..ratelimit import rate_limit
from .common import audit, paginate

router = APIRouter(prefix="/api/admin/users", tags=["admin-users"])


@router.get("", dependencies=[Depends(rate_limit("admin_read"))])
def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    search: str | None = Query(None, max_length=120),
    is_active: bool | None = Query(None),
    role: str | None = Query(None, max_length=20),
    sort: str = Query("newest", max_length=20),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    query = select(User)
    count_query = select(func.count(User.id))
    filters = []
    if search:
        like = f"%{search.strip().lower()}%"
        filters.append(or_(func.lower(User.email).like(like), func.lower(User.full_name).like(like)))
    if is_active is not None:
        filters.append(User.is_active == is_active)
    if role:
        normalized = role.strip().upper()
        if normalized not in (UserRole.USER.value, UserRole.ADMIN.value):
            raise HTTPException(422, "Invalid role filter")
        filters.append(User.role == normalized)
    if filters:
        query = query.where(*filters)
        count_query = count_query.where(*filters)
    if sort == "oldest":
        query = query.order_by(User.created_at.asc(), User.id.asc())
    else:
        query = query.order_by(User.created_at.desc(), User.id.desc())
    total = int(db.scalar(count_query) or 0)
    rows = db.scalars(query.offset((page - 1) * page_size).limit(page_size)).all()
    ids = [u.id for u in rows]
    bcounts = dict(db.execute(select(Booking.user_id, func.count(Booking.id)).where(Booking.user_id.in_(ids)).group_by(Booking.user_id)).all()) if ids else {}
    rcounts = dict(db.execute(select(Review.user_id, func.count(Review.id)).where(Review.user_id.in_(ids)).group_by(Review.user_id)).all()) if ids else {}
    items = [
        {"id": u.id, "full_name": u.full_name, "email": u.email, "phone": u.phone,
         "role": u.role or UserRole.USER.value, "is_active": u.is_active, "created_at": u.created_at,
         "booking_count": int(bcounts.get(u.id, 0)), "review_count": int(rcounts.get(u.id, 0))}
        for u in rows
    ]
    return paginate(items, total, page, page_size)


@router.get("/{user_id}", dependencies=[Depends(rate_limit("admin_read"))])
def get_user(user_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    booking_count = db.scalar(select(func.count(Booking.id)).where(Booking.user_id == user.id)) or 0
    review_count = db.scalar(select(func.count(Review.id)).where(Review.user_id == user.id)) or 0
    return {
        "id": user.id, "full_name": user.full_name, "email": user.email, "phone": user.phone,
        "role": user.role or UserRole.USER.value, "is_active": user.is_active, "created_at": user.created_at,
        "booking_count": int(booking_count), "review_count": int(review_count),
    }


def _active_admin_count(db: Session) -> int:
    return int(db.scalar(select(func.count(User.id)).where(User.role == UserRole.ADMIN.value, User.is_active == True)) or 0)  # noqa: E712


def _ensure_not_last_admin(db: Session, target: User, verb: str) -> None:
    if (target.role or UserRole.USER.value) != UserRole.ADMIN.value or not target.is_active:
        return
    if _active_admin_count(db) <= 1:
        raise HTTPException(409, f"Cannot {verb} the last active administrator")


@router.post("/{user_id}/deactivate", dependencies=[Depends(rate_limit("admin_write"))])
def deactivate_user(user_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(404, "User not found")
    if target.id == admin.id:
        raise HTTPException(409, "Admins cannot deactivate their own account")
    if not target.is_active:
        return {"id": target.id, "is_active": False, "message": "User already deactivated"}
    _ensure_not_last_admin(db, target, "deactivate")
    target.is_active = False
    target.token_version = (target.token_version or 0) + 1
    audit(db, admin_user_id=admin.id, action="USER_DEACTIVATED", resource_type="user",
          resource_id=target.id, metadata={"email": target.email})
    db.add(target)
    db.commit()
    db.refresh(target)
    return {"id": target.id, "is_active": target.is_active, "message": "User deactivated"}


@router.post("/{user_id}/activate", dependencies=[Depends(rate_limit("admin_write"))])
def activate_user(user_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(404, "User not found")
    if target.is_active:
        return {"id": target.id, "is_active": True, "message": "User already active"}
    target.is_active = True
    audit(db, admin_user_id=admin.id, action="USER_ACTIVATED", resource_type="user",
          resource_id=target.id, metadata={"email": target.email})
    db.add(target)
    db.commit()
    db.refresh(target)
    return {"id": target.id, "is_active": target.is_active, "message": "User activated"}


@router.patch("/{user_id}", dependencies=[Depends(rate_limit("admin_write"))])
def update_user_role(user_id: int, payload: dict, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(404, "User not found")
    normalized = str((payload or {}).get("role") or "").strip().upper()
    if normalized not in (UserRole.USER.value, UserRole.ADMIN.value):
        raise HTTPException(422, "Role must be USER or ADMIN")
    current = target.role or UserRole.USER.value
    if current == normalized:
        return {"id": target.id, "role": current, "message": "Role unchanged"}
    if target.id == admin.id and normalized != UserRole.ADMIN.value:
        raise HTTPException(409, "Admins cannot demote their own account")
    if current == UserRole.ADMIN.value and normalized == UserRole.USER.value:
        _ensure_not_last_admin(db, target, "demote")
    target.role = normalized
    if normalized != UserRole.ADMIN.value:
        target.token_version = (target.token_version or 0) + 1
    audit(db, admin_user_id=admin.id, action="USER_ROLE_CHANGED", resource_type="user",
          resource_id=target.id, metadata={"from": current, "to": normalized})
    db.add(target)
    db.commit()
    db.refresh(target)
    return {"id": target.id, "role": target.role, "message": "Role updated"}
