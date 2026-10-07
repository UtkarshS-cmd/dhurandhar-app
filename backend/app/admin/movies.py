"""Admin movie management (non-destructive lifecycle).

Movies are referenced by shows/reviews/bookings, so deletion is not
offered; publishing is controlled via ``is_active``.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_admin
from ..models import Movie, User
from ..ratelimit import rate_limit
from .common import audit, paginate

router = APIRouter(prefix="/api/admin/movies", tags=["admin-movies"])


def _parse_metadata(raw: str | None) -> str:
    text = (raw if raw is not None else "{}").strip() or "{}"
    try:
        json.loads(text)
    except (ValueError, TypeError):
        raise HTTPException(422, "metadata_json must be valid JSON") from None
    if len(text) > 20000:
        raise HTTPException(422, "metadata_json is too large")
    return text


@router.get("", dependencies=[Depends(rate_limit("admin_read"))])
def list_movies(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    search: str | None = Query(None, max_length=120),
    is_active: bool | None = Query(None),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    query = select(Movie)
    count_query = select(func.count(Movie.id))
    filters = []
    if search:
        filters.append(func.lower(Movie.title).like(f"%{search.strip().lower()}%"))
    if is_active is not None:
        filters.append(Movie.is_active == is_active)
    if filters:
        query = query.where(*filters)
        count_query = count_query.where(*filters)
    query = query.order_by(Movie.title.asc(), Movie.id.asc())
    total = int(db.scalar(count_query) or 0)
    rows = db.scalars(query.offset((page - 1) * page_size).limit(page_size)).all()
    items = [{"id": m.id, "title": m.title, "metadata_json": m.metadata_json,
              "is_active": bool(m.is_active)} for m in rows]
    return paginate(items, total, page, page_size)


@router.post("", status_code=201, dependencies=[Depends(rate_limit("admin_write"))])
def create_movie(payload: dict, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    data = payload or {}
    title = str(data.get("title") or "").strip()
    if not title or len(title) > 200:
        raise HTTPException(422, "Title must be 1-200 characters")
    if db.scalar(select(Movie).where(func.lower(Movie.title) == title.lower())):
        raise HTTPException(409, "A movie with this title already exists")
    movie = Movie(title=title, metadata_json=_parse_metadata(data.get("metadata_json")),
                  is_active=bool(data.get("is_active", True)))
    db.add(movie)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A movie with this title already exists") from None
    audit(db, admin_user_id=admin.id, action="MOVIE_CREATED", resource_type="movie",
          resource_id=movie.id, metadata={"title": title})
    db.commit()
    db.refresh(movie)
    return {"id": movie.id, "title": movie.title, "metadata_json": movie.metadata_json,
            "is_active": bool(movie.is_active)}


@router.get("/{movie_id}", dependencies=[Depends(rate_limit("admin_read"))])
def get_movie(movie_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    movie = db.get(Movie, movie_id)
    if not movie:
        raise HTTPException(404, "Movie not found")
    return {"id": movie.id, "title": movie.title, "metadata_json": movie.metadata_json,
            "is_active": bool(movie.is_active)}
