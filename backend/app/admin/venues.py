"""Admin venues: city listing + theater CRUD (no destructive deletes)."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_admin
from ..models import City, Theater, User
from ..ratelimit import rate_limit
from .common import audit, paginate

router = APIRouter(prefix="/api/admin", tags=["admin-venues"])


@router.get("/cities", dependencies=[Depends(rate_limit("admin_read"))])
def list_cities(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    rows = db.scalars(select(City).order_by(City.name.asc())).all()
    return {"items": [{"id": c.id, "name": c.name} for c in rows], "total": len(rows)}


@router.get("/theaters", dependencies=[Depends(rate_limit("admin_read"))])
def list_theaters(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    city_id: int | None = Query(None, ge=1),
    search: str | None = Query(None, max_length=120),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    query = select(Theater, City.name).join(City, Theater.city_id == City.id)
    count_query = select(func.count(Theater.id))
    filters = []
    if city_id:
        filters.append(Theater.city_id == city_id)
    if search:
        filters.append(func.lower(Theater.name).like(f"%{search.strip().lower()}%"))
    if filters:
        query = query.where(*filters)
        count_query = count_query.where(*filters)
    query = query.order_by(Theater.name.asc(), Theater.id.asc())
    total = int(db.scalar(count_query) or 0)
    rows = db.execute(query.offset((page - 1) * page_size).limit(page_size)).all()
    items = [{"id": t.id, "city_id": t.city_id, "city_name": cname, "name": t.name,
              "address": t.address} for t, cname in rows]
    return paginate(items, total, page, page_size)


@router.post("/theaters", status_code=201, dependencies=[Depends(rate_limit("admin_write"))])
def create_theater(payload: dict, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    data = payload or {}
    try:
        city_id = int(data.get("city_id") or 0)
    except (TypeError, ValueError):
        raise HTTPException(422, "city_id must be a positive integer") from None
    name = str(data.get("name") or "").strip()
    address = str(data.get("address") or "").strip()
    if city_id < 1:
        raise HTTPException(422, "city_id must be a positive integer")
    if not name or len(name) > 160 or not address or len(address) > 300:
        raise HTTPException(422, "Name (1-160) and address (1-300) are required")
    if not db.get(City, city_id):
        raise HTTPException(404, "City not found")
    theater = Theater(city_id=city_id, name=name, address=address)
    db.add(theater)
    db.flush()
    audit(db, admin_user_id=admin.id, action="THEATER_CREATED", resource_type="theater",
          resource_id=theater.id, metadata={"name": name, "city_id": city_id})
    db.commit()
    db.refresh(theater)
    return {"id": theater.id, "city_id": theater.city_id, "name": theater.name,
            "address": theater.address}
