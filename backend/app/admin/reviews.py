"""Admin review moderation: filtered listing + removal (constraints intact)."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_admin
from ..models import Movie, Review, ReviewLike, User
from ..ratelimit import rate_limit
from .common import audit, paginate

router = APIRouter(prefix="/api/admin/reviews", tags=["admin-reviews"])


@router.get("", dependencies=[Depends(rate_limit("admin_read"))])
def list_reviews(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    movie_id: int | None = Query(None, ge=1),
    rating: int | None = Query(None, ge=1, le=10),
    spoiler: bool | None = Query(None),
    search: str | None = Query(None, max_length=120),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    query = (select(Review, User.full_name, Movie.title,
                    func.coalesce(func.count(ReviewLike.id), 0))
             .join(User, Review.user_id == User.id)
             .join(Movie, Review.movie_id == Movie.id)
             .outerjoin(ReviewLike, ReviewLike.review_id == Review.id)
             .group_by(Review.id, User.full_name, Movie.title))
    count_query = select(func.count(Review.id))
    filters = []
    if movie_id:
        filters.append(Review.movie_id == movie_id)
    if rating:
        filters.append(Review.rating == rating)
    if spoiler is not None:
        filters.append(Review.spoiler == spoiler)
    if search:
        like = f"%{search.strip().lower()}%"
        filters.append(func.lower(Review.title).like(like))
    if filters:
        query = query.where(*filters)
        count_query = count_query.where(*filters)
    query = query.order_by(Review.created_at.desc(), Review.id.desc())
    total = int(db.scalar(count_query) or 0)
    rows = db.execute(query.offset((page - 1) * page_size).limit(page_size)).all()
    items = [{"id": r.id, "user_id": r.user_id, "user_name": uname, "movie_id": r.movie_id,
              "movie_title": mtitle, "rating": r.rating, "title": r.title, "body": r.body,
              "spoiler": bool(r.spoiler), "likes": int(likes or 0),
              "created_at": r.created_at, "updated_at": r.updated_at}
             for r, uname, mtitle, likes in rows]
    return paginate(items, total, page, page_size)


@router.delete("/{review_id}", dependencies=[Depends(rate_limit("admin_write"))])
def remove_review(review_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    review = db.get(Review, review_id)
    if not review:
        raise HTTPException(404, "Review not found")
    audit(db, admin_user_id=admin.id, action="REVIEW_REMOVED", resource_type="review",
          resource_id=review.id, metadata={"movie_id": review.movie_id, "user_id": review.user_id})
    db.delete(review)
    db.commit()
    return {"status": "deleted", "review_id": review_id}
