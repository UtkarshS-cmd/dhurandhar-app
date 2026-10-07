"""Admin show creation (pre-creates ShowSeat rows from the screen map)."""

from datetime import date, time as dtime

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_admin
from ..models import Movie, Screen, Seat, Show, ShowSeat, ShowSeatStatus, User
from ..ratelimit import rate_limit
from .common import audit
from .shows import router


def _parse_date(value) -> date:
    try:
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        raise HTTPException(422, "show_date must be YYYY-MM-DD") from None


def _parse_time(value) -> dtime:
    try:
        if isinstance(value, dtime):
            return value
        return dtime.fromisoformat(str(value))
    except (ValueError, TypeError):
        raise HTTPException(422, "show_time must be HH:MM[:SS]") from None


@router.post("", status_code=201, dependencies=[Depends(rate_limit("admin_write"))])
def create_show(payload: dict, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    data = payload or {}
    try:
        movie_id = int(data.get("movie_id") or 0)
        screen_id = int(data.get("screen_id") or 0)
    except (TypeError, ValueError):
        raise HTTPException(422, "movie_id and screen_id must be positive integers") from None
    if movie_id < 1 or screen_id < 1:
        raise HTTPException(422, "movie_id and screen_id must be positive integers")
    if "show_date" not in data or "show_time" not in data:
        raise HTTPException(422, "show_date and show_time are required")
    show_date = _parse_date(data.get("show_date"))
    show_time = _parse_time(data.get("show_time"))
    status = str(data.get("status") or "ACTIVE").strip().upper() or "ACTIVE"
    if status not in ("ACTIVE", "CANCELLED"):
        raise HTTPException(422, "status must be ACTIVE or CANCELLED")
    movie = db.get(Movie, movie_id)
    if not movie:
        raise HTTPException(404, "Movie not found")
    screen = db.get(Screen, screen_id)
    if not screen:
        raise HTTPException(404, "Screen not found")
    if db.scalar(select(Show).where(Show.screen_id == screen_id, Show.show_date == show_date,
                                    Show.show_time == show_time)):
        raise HTTPException(409, "A show already exists on this screen at that date/time")
    show = Show(movie_id=movie.id, screen_id=screen.id, show_date=show_date,
                show_time=show_time, status=status)
    db.add(show)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A show already exists on this screen at that date/time") from None
    seats = db.scalars(select(Seat).where(Seat.screen_id == screen.id)).all()
    for seat in seats:
        db.add(ShowSeat(show_id=show.id, seat_id=seat.id, status=ShowSeatStatus.AVAILABLE.value))
    audit(db, admin_user_id=admin.id, action="SHOW_CREATED", resource_type="show",
          resource_id=show.id, metadata={"movie_id": movie.id, "screen_id": screen.id})
    db.commit()
    db.refresh(show)
    return {"id": show.id, "movie_id": show.movie_id, "screen_id": show.screen_id,
            "show_date": show.show_date, "show_time": show.show_time, "status": show.status}
