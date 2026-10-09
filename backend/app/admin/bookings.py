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
    return _serialize_many(db, [booking])[0]


def _serialize_many(db: Session, bookings: list[Booking]) -> list[dict]:
    """Serialize bookings with a bounded number of queries (no per-row N+1).

    One page costs exactly 5 queries regardless of page size: users, shows,
    seat labels, movies/screens, theaters. The single-booking detail endpoint
    reuses the same path with a one-element list.
    """
    if not bookings:
        return []
    user_ids = sorted({b.user_id for b in bookings if b.user_id})
    show_ids = sorted({b.show_id for b in bookings})
    booking_ids = [b.id for b in bookings]
    users = (
        {u.id: u for u in db.scalars(select(User).where(User.id.in_(user_ids))).all()}
        if user_ids
        else {}
    )
    show_rows = (
        db.execute(
            select(Show, Movie.title, Screen.name, Screen.theater_id)
            .join(Movie, Show.movie_id == Movie.id)
            .join(Screen, Show.screen_id == Screen.id)
            .where(Show.id.in_(show_ids))
        ).all()
        if show_ids
        else []
    )
    shows = {show.id: (show, movie_title, screen_name, theater_id) for show, movie_title, screen_name, theater_id in show_rows}
    theater_ids = sorted({theater_id for _, _, _, theater_id in show_rows})
    theaters = (
        {t.id: t.name for t in db.scalars(select(Theater).where(Theater.id.in_(theater_ids))).all()}
        if theater_ids
        else {}
    )
    seat_rows = db.execute(
        select(BookingSeat.booking_id, Seat.row_label, Seat.seat_number)
        .join(Seat, BookingSeat.seat_id == Seat.id)
        .where(BookingSeat.booking_id.in_(booking_ids))
        .order_by(Seat.row_label.asc(), Seat.seat_number.asc())
    ).all()
    labels: dict[int, list[str]] = {bid: [] for bid in booking_ids}
    for booking_id, row_label, seat_number in seat_rows:
        labels.setdefault(booking_id, []).append(f"{row_label}{seat_number}")
    items = []
    for booking in bookings:
        user = users.get(booking.user_id) if booking.user_id else None
        show = shows.get(booking.show_id)
        movie_title = theater_name = screen_name = None
        show_date = show_time = None
        if show is not None:
            show_row, movie_title, screen_name, theater_id = show
            show_date, show_time = show_row.show_date, show_row.show_time
            theater_name = theaters.get(theater_id)
        items.append({
            "id": booking.id, "booking_reference": booking.booking_reference,
            "user_id": booking.user_id,
            "user_name": user.full_name if user else None,
            "user_email": user.email if user else None,
            "show_id": booking.show_id, "movie_title": movie_title,
            "theater_name": theater_name, "screen_name": screen_name,
            "show_date": show_date, "show_time": show_time,
            "seats": labels.get(booking.id, []), "status": booking.status,
            "payment_method": booking.payment_method, "payment_status": booking.payment_status,
            "total_amount": booking.total_amount, "created_at": booking.created_at,
        })
    return items


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
    return paginate(_serialize_many(db, rows), total, page, page_size)


@router.get("/{booking_id}", dependencies=[Depends(rate_limit("admin_read"))])
def get_booking(booking_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    booking = db.get(Booking, booking_id)
    if not booking:
        raise HTTPException(404, "Booking not found")
    return _serialize(db, booking)
