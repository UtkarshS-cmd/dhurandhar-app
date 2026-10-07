"""Admin booking inspection (read-only; state machine untouched)."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from ..db import get_db
from ..deps import require_admin
from ..models import (
    Booking, BookingSeat, Movie, Screen, Seat, Show, Theater, User,
)
from ..ratelimit import rate_limit
from .common import paginate

router = APIRouter(prefix="/api/admin/bookings", tags=["admin-bookings"])


def _serialize(db: Session, booking: Booking) -> dict:
    user = db.get(User, booking.user_id) if booking.user_id else None
    show = db.get(Show, booking.show_id)
    movie_title = theater_name = screen_name = None
    show_date = show_time = None
    if show:
        movie = db.get(Movie, show.movie_id)
        screen = db.get(Screen, show.screen_id)
        movie_title = movie.title if movie else None
        screen_name = screen.name if screen else None
        show_date, show_time = show.show_date, show.show_time
        if screen:
            theater = db.get(Theater, screen.theater_id)
            theater_name = theater.name if theater else None
    seat_rows = (db.scalars(select(Seat).join(BookingSeat, BookingSeat.seat_id == Seat.id)
                            .where(BookingSeat.booking_id == booking.id)
                            .order_by(Seat.row_label.asc(), Seat.seat_number.asc())).all())
    labels = [f"{s.row_label}{s.seat_number}" for s in seat_rows]
    return {
        "id": booking.id, "booking_reference": booking.booking_reference,
        "user_id": booking.user_id,
        "user_name": user.full_name if user else None,
        "user_email": user.email if user else None,
        "show_id": booking.show_id, "movie_title": movie_title,
        "theater_name": theater_name, "screen_name": screen_name,
        "show_date": show_date, "show_time": show_time,
        "seats": labels, "status": booking.status,
        "payment_method": booking.payment_method, "payment_status": booking.payment_status,
        "total_amount": booking.total_amount, "created_at": booking.created_at,
    }


@router.get("", dependencies=[Depends(rate_limit("admin_read"))])
def list_bookings(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    search: str | None = Query(None, max_length=64),
    status: str | None = Query(None, max_length=20),
    payment_status: str | None = Query(None, max_length=20),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    query = select(Booking)
    count_query = select(func.count(Booking.id))
    filters = []
    if search:
        filters.append(func.lower(Booking.booking_reference).like(f"%{search.strip().lower()}%"))
    if status:
        filters.append(Booking.status == status.strip().upper())
    if payment_status:
        filters.append(Booking.payment_status == payment_status.strip().upper())
    if date_from:
        filters.append(func.date(Booking.created_at) >= date_from.isoformat())
    if date_to:
        filters.append(func.date(Booking.created_at) <= date_to.isoformat())
    if filters:
        query = query.where(*filters)
        count_query = count_query.where(*filters)
    query = query.order_by(Booking.created_at.desc(), Booking.id.desc())
    total = int(db.scalar(count_query) or 0)
    rows = db.scalars(query.offset((page - 1) * page_size).limit(page_size)).all()
    return paginate([_serialize(db, b) for b in rows], total, page, page_size)


@router.get("/{booking_id}", dependencies=[Depends(rate_limit("admin_read"))])
def get_booking(booking_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    booking = db.get(Booking, booking_id)
    if not booking:
        raise HTTPException(404, "Booking not found")
    return _serialize(db, booking)
