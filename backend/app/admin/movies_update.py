"""Admin movie update (PATCH only; no delete by design)."""

from fastapi import Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_admin
from ..models import Movie, User
from ..ratelimit import rate_limit
from .common import audit
from .movies import _parse_metadata, router


@router.patch("/{movie_id}", dependencies=[Depends(rate_limit("admin_write"))])
def update_movie(movie_id: int, payload: dict, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    movie = db.get(Movie, movie_id)
    if not movie:
        raise HTTPException(404, "Movie not found")
    data = payload or {}
    changed: dict = {}
    if "title" in data and data["title"] is not None:
        title = str(data["title"]).strip()
        if not title or len(title) > 200:
            raise HTTPException(422, "Title must be 1-200 characters")
        existing = db.scalar(select(Movie).where(func.lower(Movie.title) == title.lower(), Movie.id != movie.id))
        if existing:
            raise HTTPException(409, "A movie with this title already exists")
        movie.title = title
        changed["title"] = title
    if "metadata_json" in data and data["metadata_json"] is not None:
        movie.metadata_json = _parse_metadata(data["metadata_json"])
        changed["metadata_json"] = True
    if "is_active" in data and data["is_active"] is not None:
        movie.is_active = bool(data["is_active"])
        changed["is_active"] = bool(movie.is_active)
    if not changed:
        raise HTTPException(422, "Provide at least one field to update")
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A movie with this title already exists") from None
    audit(db, admin_user_id=admin.id, action="MOVIE_UPDATED", resource_type="movie",
          resource_id=movie.id, metadata=changed)
    db.add(movie)
    db.commit()
    db.refresh(movie)
    return {"id": movie.id, "title": movie.title, "metadata_json": movie.metadata_json,
            "is_active": bool(movie.is_active), "message": "Movie updated"}
