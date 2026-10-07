"""Admin show PATCH + cancel endpoints (guarded; see shows_detail guards)."""

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_admin
from ..models import Booking, BookingStatus, Movie, Screen, Show, User
from ..ratelimit import rate_limit
from .common import audit
from .shows import router
from .shows_create import _parse_date, _parse_time
from .shows_detail import _active_booking_count, _ensure_safe_to_mutate, _seat_counts


@router.patch("/{show_id}", dependencies=[Depends(rate_limit("admin_write"))])
def update_show(show_id: int, payload: dict, db: Session = Depends(get_db),
                admin: User = Depends(require_admin)):
    show = db.get(Show, show_id)
    if not show:
        raise HTTPException(404, "Show not found")
    data = payload or {}
    wants_structural_change = any(
        k in data and data[k] is not None
        for k in ("movie_id", "screen_id", "show_date", "show_time")
    )
    if wants_structural_change:
        _ensure_safe_to_mutate(db, show)
    changed: dict = {}
    if "movie_id" in data and data["movie_id"] is not None:
        try:
            movie_id = int(data["movie_id"])
        except (TypeError, ValueError):
            raise HTTPException(422, "movie_id must be a positive integer") from None
        if movie_id < 1 or not db.get(Movie, movie_id):
            raise HTTPException(404 if movie_id >= 1 else 422, "Movie not found" if movie_id >= 1 else "movie_id invalid")
        show.movie_id = movie_id
        changed["movie_id"] = movie_id
    if "screen_id" in data and data["screen_id"] is not None:
        try:
            screen_id = int(data["screen_id"])
        except (TypeError, ValueError):
            raise HTTPException(422, "screen_id must be a positive integer") from None
        if screen_id < 1 or not db.get(Screen, screen_id):
            raise HTTPException(404 if screen_id >= 1 else 422, "Screen not found" if screen_id >= 1 else "screen_id invalid")
        show.screen_id = screen_id
        changed["screen_id"] = screen_id
    if "show_date" in data and data["show_date"] is not None:
        show.show_date = _parse_date(data["show_date"])
        changed["show_date"] = str(show.show_date)
    if "show_time" in data and data["show_time"] is not None:
        show.show_time = _parse_time(data["show_time"])
        changed["show_time"] = str(show.show_time)
    if "status" in data and data["status"] is not None:
        status = str(data["status"]).strip().upper()
        if status not in ("ACTIVE", "CANCELLED"):
            raise HTTPException(422, "status must be ACTIVE or CANCELLED")
        if status == "CANCELLED" and show.status != "CANCELLED":
            _ensure_safe_to_mutate(db, show)
        show.status = status
        changed["status"] = status
    if not changed:
        raise HTTPException(422, "Provide at least one field to update")
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A show already exists on this screen at that date/time") from None
    audit(db, admin_user_id=admin.id, action="SHOW_UPDATED", resource_type="show",
          resource_id=show.id, metadata=changed)
    db.add(show)
    db.commit()
    db.refresh(show)
    held, booked = _seat_counts(db, show.id)
    return {"id": show.id, "movie_id": show.movie_id, "screen_id": show.screen_id,
            "show_date": show.show_date, "show_time": show.show_time, "status": show.status,
            "held_seats": held, "booked_seats": booked}


@router.post("/{show_id}/cancel", dependencies=[Depends(rate_limit("admin_write"))])
def cancel_show(show_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    show = db.get(Show, show_id)
    if not show:
        raise HTTPException(404, "Show not found")
    if show.status == "CANCELLED":
        return {"id": show.id, "status": "CANCELLED", "message": "Show already cancelled"}
    _ensure_safe_to_mutate(db, show)
    show.status = "CANCELLED"
    audit(db, admin_user_id=admin.id, action="SHOW_CANCELLED", resource_type="show",
          resource_id=show.id, metadata={})
    db.add(show)
    db.commit()
    db.refresh(show)
    return {"id": show.id, "status": show.status, "message": "Show cancelled"}
