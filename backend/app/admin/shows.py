"""Admin show reads + creation.

Creation validates movie/screen existence, honors the
``uq_screen_showtime`` constraint, and pre-creates ShowSeat rows from the
screen's seat map. Mutation guards live in ``shows_write.py``.
"""

from datetime import date, time as dtime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_admin
from ..models import Movie, Screen, Seat, Show, ShowSeat, ShowSeatStatus, Theater, User
from ..ratelimit import rate_limit
from .common import audit, paginate

router = APIRouter(prefix="/api/admin/shows", tags=["admin-shows"])


def _serialize_show(show: Show, movie_title: str | None, theater_name: str | None,
                    screen_name: str | None, held: int, booked: int) -> dict:
    return {
        "id": show.id, "movie_id": show.movie_id, "movie_title": movie_title,
        "screen_id": show.screen_id, "screen_name": screen_name,
        "theater_name": theater_name,
        "show_date": show.show_date, "show_time": show.show_time,
        "status": show.status, "held_seats": held, "booked_seats": booked,
    }


def _seat_counts(db: Session, show_id: int) -> tuple[int, int]:
    held = int(db.scalar(select(func.count(ShowSeat.id)).where(
        ShowSeat.show_id == show_id, ShowSeat.status == ShowSeatStatus.HELD.value)) or 0)
    booked = int(db.scalar(select(func.count(ShowSeat.id)).where(
        ShowSeat.show_id == show_id, ShowSeat.status == ShowSeatStatus.BOOKED.value)) or 0)
    return held, booked


@router.get("", dependencies=[Depends(rate_limit("admin_read"))])
def list_shows(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    movie_id: int | None = Query(None, ge=1),
    upcoming: bool | None = Query(None),
    status: str | None = Query(None, max_length=20),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    query = (select(Show, Movie.title, Theater.name, Screen.name)
             .join(Movie, Show.movie_id == Movie.id)
             .join(Screen, Show.screen_id == Screen.id)
             .join(Theater, Screen.theater_id == Theater.id))
    count_query = select(func.count(Show.id))
    filters = []
    if movie_id:
        filters.append(Show.movie_id == movie_id)
    if status:
        filters.append(Show.status == status.strip().upper())
    if upcoming is True:
        filters.append(Show.show_date >= date.today())
    if filters:
        query = query.where(*filters)
        count_query = count_query.where(*filters)
    query = query.order_by(Show.show_date.desc(), Show.show_time.desc(), Show.id.desc())
    total = int(db.scalar(count_query) or 0)
    rows = db.execute(query.offset((page - 1) * page_size).limit(page_size)).all()
    show_ids = [show.id for show, _, _, _ in rows]
    counts: dict[int, tuple[int, int]] = {}
    if show_ids:
        # One GROUP BY for the whole page instead of 2 COUNT queries per row.
        agg = db.execute(
            select(
                ShowSeat.show_id,
                func.sum(case((ShowSeat.status == ShowSeatStatus.HELD.value, 1), else_=0)).label("held"),
                func.sum(case((ShowSeat.status == ShowSeatStatus.BOOKED.value, 1), else_=0)).label("booked"),
            )
            .where(ShowSeat.show_id.in_(show_ids))
            .group_by(ShowSeat.show_id)
        ).all()
        counts = {show_id: (int(held or 0), int(booked or 0)) for show_id, held, booked in agg}
    items = []
    for show, movie_title, theater_name, screen_name in rows:
        held, booked = counts.get(show.id, (0, 0))
        items.append(_serialize_show(show, movie_title, theater_name, screen_name, held, booked))
    return paginate(items, total, page, page_size)
