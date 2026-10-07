"""Admin show mutation guards + cancellation.

Historical/active shows are never casually mutated: any update touching a
show with HELD/BOOKED seats or non-cancelled bookings is refused with 409.
Cancellation flips status only (seat/booking rows untouched) after the same
safety check; the booking engine remains the sole writer of seat state.
"""

from datetime import date

from fastapi import Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_admin
from ..models import (
    Booking, BookingStatus, Movie, Screen, Show, ShowSeat, ShowSeatStatus, User,
)
from ..ratelimit import rate_limit
from .common import audit
from .shows import _seat_counts, router
from .shows_create import _parse_date, _parse_time


def _active_booking_count(db: Session, show_id: int) -> int:
    return int(db.scalar(select(func.count(Booking.id)).where(
        Booking.show_id == show_id,
        Booking.status.in_((BookingStatus.HELD.value, BookingStatus.CONFIRMED.value)))) or 0)


def _ensure_safe_to_mutate(db: Session, show: Show) -> None:
    held, booked = _seat_counts(db, show.id)
    if held or booked or _active_booking_count(db, show.id):
        raise HTTPException(409, "Show has active holds/bookings and cannot be modified")


@router.get("/{show_id}", dependencies=[Depends(rate_limit("admin_read"))])
def get_show(show_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    from ..models import Movie as _Movie, Screen as _Screen, Theater as _Theater
    row = db.execute(select(Show, _Movie.title, _Theater.name, _Screen.name)
                     .join(_Movie, Show.movie_id == _Movie.id)
                     .join(_Screen, Show.screen_id == _Screen.id)
                     .join(_Theater, _Screen.theater_id == _Theater.id)
                     .where(Show.id == show_id)).first()
    if not row:
        raise HTTPException(404, "Show not found")
    show, movie_title, theater_name, screen_name = row
    held, booked = _seat_counts(db, show.id)
    return {"id": show.id, "movie_id": show.movie_id, "movie_title": movie_title,
            "screen_id": show.screen_id, "screen_name": screen_name,
            "theater_name": theater_name, "show_date": show.show_date,
            "show_time": show.show_time, "status": show.status,
            "held_seats": held, "booked_seats": booked}
