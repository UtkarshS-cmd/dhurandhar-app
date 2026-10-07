"""Admin theater update (PATCH only; FK history is preserved)."""

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_admin
from ..models import Theater, User
from ..ratelimit import rate_limit
from .common import audit
from .venues import router


@router.patch("/theaters/{theater_id}", dependencies=[Depends(rate_limit("admin_write"))])
def update_theater(theater_id: int, payload: dict, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    theater = db.get(Theater, theater_id)
    if not theater:
        raise HTTPException(404, "Theater not found")
    data = payload or {}
    changed: dict = {}
    if "name" in data and data["name"] is not None:
        name = str(data["name"]).strip()
        if not name or len(name) > 160:
            raise HTTPException(422, "Name must be 1-160 characters")
        theater.name = name
        changed["name"] = name
    if "address" in data and data["address"] is not None:
        address = str(data["address"]).strip()
        if not address or len(address) > 300:
            raise HTTPException(422, "Address must be 1-300 characters")
        theater.address = address
        changed["address"] = True
    if not changed:
        raise HTTPException(422, "Provide at least one field to update")
    audit(db, admin_user_id=admin.id, action="THEATER_UPDATED", resource_type="theater",
          resource_id=theater.id, metadata=changed)
    db.add(theater)
    db.commit()
    db.refresh(theater)
    return {"id": theater.id, "city_id": theater.city_id, "name": theater.name,
            "address": theater.address}
