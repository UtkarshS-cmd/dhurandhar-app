
from datetime import date, datetime, timedelta, time
from decimal import Decimal
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Depends, HTTPException, status, Header, Request, Query
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, or_, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from .config import settings, ROOT
from .db import get_db
from .models import (
    User, Movie, City, Theater, Screen, Seat, Show, ShowSeat,
    Booking, BookingSeat, PaymentAttempt, Review, ReviewLike, NewsletterSubscriber, ContactMessage,
    ShowSeatStatus, BookingStatus, PaymentStatus
)
from .schemas import *
from .security import (
    hash_password, verify_password, create_token, decode_token, decode_token_payload,
    AuthenticationError, generate_booking_reference, is_valid_booking_reference,
)
from .ratelimit import rate_limit
from .services.booking import (
    BookingConflict,
    BookingEngineError,
    BookingExpired,
    BookingNotFound,
    InvalidSeatRequest,
    SeatConflict,
    ShowNotFound,
    booking_transaction,
    cleanup_expired_holds as cleanup_expired_holds_service,
    confirm_booking_reference,
    create_hold_for_user,
    transition_booking,
    transition_payment,
    transition_seat,
    validate_show_and_seats as validate_show_and_seats_service,
)
from .services.payment import payment_gateway
from .services.payments.base import ProviderError
from .services.payments.service import get_payment_service

app = FastAPI(title="Dhurandhar Cinema API", version="1.0.0")

# CORS is environment-driven (see config._resolve_cors_origins): localhost in
# development, explicit origins only in production, and never "*" while
# credentialed requests are allowed. An empty tuple means same-origin only —
# which is the normal single-process deployment where the API also serves the
# frontend. Methods/headers are restricted to what the frontend actually uses.
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )

# Initial Content-Security-Policy compatible with the current frontend:
# scripts are same-origin modules only (index.html has no inline scripts or
# event handlers after Phase 2), styles may be inline (the markup uses
# style="" attributes extensively) plus Google Fonts; media/images are local;
# frame-ancestors/object-src lock down embedding. This is a pragmatic first
# policy, not a completed browser-security audit.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' data: https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "media-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    return response


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    """422 responses stay 422 but never echo submitted values.

    Pydantic's raw errors include the ``input`` field, which would reflect
    submitted passwords/PII back into responses and logs. Only the location,
    message and error type are returned.
    """
    detail = [
        {"loc": error.get("loc"), "msg": error.get("msg"), "type": error.get("type")}
        for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": detail})

def cleanup_expired_holds(db: Session):
    cleanup_expired_holds_service(db)

def current_user(db: Session = Depends(get_db), authorization: str | None = Header(default=None)) -> User:
    """Resolve the authenticated user or raise a uniform 401.

    Authorization boundary: every failure mode (missing header, wrong
    scheme, malformed/expired/tampered token, token subject that no longer
    exists in the database) returns the same bare 401 with
    ``WWW-Authenticate: Bearer`` — no JWT internals, no database details,
    no account-existence signals.
    """
    if not authorization:
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = decode_token_payload(token.strip())
        uid = int(payload["sub"])
    except AuthenticationError:
        raise HTTPException(401, "Invalid or expired session", headers={"WWW-Authenticate": "Bearer"})
    user = db.get(User, uid)
    if not user or not user.is_active:
        raise HTTPException(401, "Invalid or expired session", headers={"WWW-Authenticate": "Bearer"})
    try:
        token_version = payload.get("ver")
        if token_version is None:
            if user.token_version != 1:
                raise ValueError("missing token version")
        else:
            if int(token_version) != user.token_version:
                raise ValueError("token version mismatch")
    except (TypeError, ValueError):
        raise HTTPException(401, "Invalid or expired session", headers={"WWW-Authenticate": "Bearer"})
    return user


def optional_current_user(db: Session = Depends(get_db), authorization: str | None = Header(default=None)) -> User | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    try:
        payload = decode_token_payload(token.strip())
        uid = int(payload["sub"])
    except AuthenticationError:
        return None
    user = db.get(User, uid)
    if not user or not user.is_active:
        return None
    try:
        token_version = payload.get("ver")
        if token_version is None:
            if user.token_version != 1:
                return None
        else:
            if int(token_version) != user.token_version:
                return None
    except (TypeError, ValueError):
        return None
    return user


@app.get("/api/health")
def health():
    return {"status": "ok", "payment_mode": settings.payment_mode}

@app.post("/api/auth/register", response_model=AuthOut, status_code=201,
          dependencies=[Depends(rate_limit("register"))])
def register(payload: UserCreate, db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.email == payload.email.lower())):
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    user = User(
        full_name=payload.full_name.strip(),
        email=payload.email.lower(),
        phone=payload.phone,
        password_hash=hash_password(payload.password),
        is_active=True,
        token_version=1,
    )
    db.add(user); db.commit(); db.refresh(user)
    return AuthOut(access_token=create_token(user.id, user.token_version), user=UserOut.model_validate(user, from_attributes=True))

@app.post("/api/auth/login", response_model=AuthOut,
          dependencies=[Depends(rate_limit("login", "identity")), Depends(rate_limit("login_ip"))])
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    # Uniform failure for unknown email and wrong password alike: responses
    # never reveal which accounts exist.
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return AuthOut(access_token=create_token(user.id, user.token_version), user=UserOut.model_validate(user, from_attributes=True))

@app.get("/api/dates")
def available_dates():
    today=date.today()
    return [{"value": (today+timedelta(days=i)).isoformat(), "label": (today+timedelta(days=i)).strftime("%A, %d %b %Y").replace(" 0"," ")} for i in range(7)]

@app.get("/api/cities")
def cities(db: Session = Depends(get_db)):
    return [{"id": c.id, "name": c.name} for c in db.scalars(select(City).order_by(City.name)).all()]

@app.get("/api/theaters")
def theaters(city_id: int, db: Session = Depends(get_db)):
    rows = db.scalars(select(Theater).where(Theater.city_id == city_id).order_by(Theater.name)).all()
    return [{"id": t.id, "name": t.name, "address": t.address} for t in rows]

@app.get("/api/shows", response_model=list[ShowOut])
def shows(theater_id: int, date: date, db: Session = Depends(get_db)):
    rows = db.execute(
        select(Show, Screen, Theater)
        .join(Screen, Show.screen_id == Screen.id)
        .join(Theater, Screen.theater_id == Theater.id)
        .where(Theater.id == theater_id, Show.show_date == date, Show.status == "ACTIVE")
        .order_by(Show.show_time)
    ).all()
    return [ShowOut(id=s.id, date=s.show_date, time=s.show_time, theater_id=t.id, theater_name=t.name, screen_id=sc.id) for s,sc,t in rows]

@app.get("/api/shows/{show_id}/seats", response_model=list[SeatOut])
def show_seats(show_id: int, db: Session = Depends(get_db)):
    cleanup_expired_holds(db)
    show = db.get(Show, show_id)
    if not show:
        raise HTTPException(404, "Show not found")
    rows = db.execute(
        select(ShowSeat, Seat)
        .join(Seat, ShowSeat.seat_id == Seat.id)
        .where(ShowSeat.show_id == show_id)
        .order_by(Seat.row_label, Seat.seat_number)
    ).all()
    now = datetime.utcnow()
    result=[]
    for ss, seat in rows:
        st=ss.status
        if st == ShowSeatStatus.HELD.value and ss.hold_expires_at and ss.hold_expires_at < now:
            st=ShowSeatStatus.AVAILABLE.value
        result.append(SeatOut(id=seat.id,row=seat.row_label,number=seat.seat_number,category=seat.category,price=Decimal(seat.price),status=st))
    return result

def get_or_create_user(payload: UserCreate, db: Session) -> User:
    email=payload.email.lower()
    user=db.scalar(select(User).where(User.email==email))
    if user:
        if not verify_password(payload.password, user.password_hash):
            raise HTTPException(409, "Email already registered; use the existing account password")
        return user
    user=User(full_name=payload.full_name.strip(),email=email,phone=payload.phone,password_hash=hash_password(payload.password))
    db.add(user); db.flush()
    return user

def validate_show_and_seats(show_id: int, seat_ids: list[int], db: Session):
    return validate_show_and_seats_service(show_id, seat_ids, db)

# Booking security model (Phase 2)
# --------------------------------
# Guest checkout is intentional: a hold can be created with embedded user
# details and later confirmed with *only* the booking reference. The
# reference is therefore a capability, protected in three layers:
#   1. 144 bits of entropy from ``secrets`` (unguessable, see security.py),
#   2. a shape check before any database access (uniform 404s),
#   3. per-IP rate limits on lookup/confirm (brute-force enumeration cap).
# The booking payload intentionally contains no user PII (no email/phone/
# name), so a leaked reference reveals seats/amount/status only.
# Authenticated listings live behind ``/api/me/bookings`` (own bookings only).
@app.post("/api/bookings/hold", response_model=BookingOut, status_code=201,
          dependencies=[Depends(rate_limit("hold")), Depends(rate_limit("hold_identity", "identity"))])
def create_hold(payload: BookingCreateRequest, db: Session = Depends(get_db)):
    cleanup_expired_holds(db)
    try:
        user = get_or_create_user(payload.user, db)
        booking = create_hold_for_user(db, user, payload.show_id, payload.seat_ids, payload.payment_method)
        return booking_out(db, booking)
    except BookingEngineError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except HTTPException:
        raise

def booking_out(db: Session, booking: Booking) -> BookingOut:
    seats=db.scalars(select(Seat).join(BookingSeat,BookingSeat.seat_id==Seat.id).where(BookingSeat.booking_id==booking.id).order_by(Seat.row_label,Seat.seat_number)).all()
    return BookingOut(
        booking_reference=booking.booking_reference,status=booking.status,payment_status=booking.payment_status,
        total_amount=Decimal(booking.total_amount),show_id=booking.show_id,
        seats=[f"{s.row_label}{s.seat_number}" for s in seats],created_at=booking.created_at,hold_expires_at=booking.hold_expires_at
    )

@app.post("/api/bookings/{reference}/confirm", response_model=BookingOut,
          dependencies=[Depends(rate_limit("booking_ref"))])
def confirm_booking(reference: str, payload: BookingConfirmRequest, db: Session = Depends(get_db)):
    cleanup_expired_holds(db)
    if not is_valid_booking_reference(reference):
        raise HTTPException(404, "Booking hold not found")
    try:
        booking = confirm_booking_reference(db, reference, payload.payment_method)
        return booking_out(db, booking)
    except BookingEngineError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except HTTPException:
        raise
def _webhook_response(outcome: dict):
    name = outcome.get("outcome")
    if name == "confirmed":
        return {"status": "ok", "booking_reference": outcome.get("booking_reference")}
    if name in ("already_processed", "terminal_ignored"):
        return {"status": "already_processed"}
    if name == "failed_recorded":
        return {"status": "ok", "recorded": "failed"}
    return {"status": "ignored", "reason": outcome.get("reason", "ignored")}

# -- Phase 4: provider-agnostic payment orders + webhooks -------------------
@app.post("/api/payments/orders", dependencies=[Depends(rate_limit("booking_ref"))])
def create_payment_order(payload: PaymentOrderRequest, db: Session = Depends(get_db)):
    cleanup_expired_holds(db)
    try:
        service = get_payment_service()
    except ProviderError as exc:
        raise HTTPException(500, str(exc)) from exc
    try:
        _attempt, order = service.create_order_for_booking(db, payload.booking_reference)
    except BookingEngineError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {
        "provider": order.provider, "provider_order_id": order.provider_order_id,
        "amount": order.amount, "currency": order.currency,
        "booking_reference": order.booking_reference, "checkout": order.checkout,
    }


@app.post("/api/payments/retry", dependencies=[Depends(rate_limit("booking_ref"))])
def retry_payment_order(payload: PaymentOrderRequest, db: Session = Depends(get_db)):
    cleanup_expired_holds(db)
    try:
        service = get_payment_service()
    except ProviderError as exc:
        raise HTTPException(500, str(exc)) from exc
    try:
        _attempt, order = service.retry_payment(db, payload.booking_reference)
    except BookingEngineError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {
        "provider": order.provider, "provider_order_id": order.provider_order_id,
        "amount": order.amount, "currency": order.currency,
        "booking_reference": order.booking_reference, "checkout": order.checkout,
    }


@app.get("/api/payments/status/{reference}", dependencies=[Depends(rate_limit("booking_ref"))])
def payment_status(reference: str, db: Session = Depends(get_db)):
    cleanup_expired_holds(db)
    if not is_valid_booking_reference(reference):
        raise HTTPException(404, "Booking not found")
    booking = db.scalar(select(Booking).where(Booking.booking_reference == reference))
    if not booking:
        raise HTTPException(404, "Booking not found")
    attempts = db.scalars(select(PaymentAttempt).where(
        PaymentAttempt.booking_id == booking.id
        ).order_by(PaymentAttempt.attempt_no)).all()
    return {
        "booking_reference": booking.booking_reference,
        "booking_status": booking.status, "payment_status": booking.payment_status,
        "total_amount": float(booking.total_amount), "currency": "INR",
        "attempts": [
            {"attempt_no": a.attempt_no, "provider": a.provider,
             "provider_order_id": a.provider_order_id,
             "provider_payment_id": a.provider_payment_id,
             "amount": float(a.amount), "currency": a.currency,
             "status": a.status} for a in attempts],
    }


@app.post("/api/payments/webhook/{provider}", include_in_schema=False)
async def payment_webhook(provider: str, request: Request, db: Session = Depends(get_db)):
    provider = (provider or "").strip().lower()
    try:
        service = get_payment_service()
    except ProviderError as exc:
        raise HTTPException(500, str(exc)) from exc
    if provider != service.provider.name:
        raise HTTPException(404, "Unknown payment provider")
    raw_body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    if not service.provider.verify_webhook(raw_body=raw_body, headers=headers):
        raise HTTPException(401, "Invalid webhook signature")
    try:
        event = service.provider.parse_webhook(raw_body=raw_body)
    except Exception as exc:
        raise HTTPException(400, f"Unparseable webhook body: {exc}") from exc
    if event.provider != service.provider.name:
        raise HTTPException(400, "Provider mismatch in webhook payload")
    try:
        outcome = service.process_webhook_event(db, event)
    except BookingEngineError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return _webhook_response(outcome)


@app.post("/api/payments/webhook", include_in_schema=False)
async def payment_webhook_default(request: Request, db: Session = Depends(get_db)):
    try:
        service = get_payment_service()
    except ProviderError as exc:
        raise HTTPException(500, str(exc)) from exc
    raw_body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    if not service.provider.verify_webhook(raw_body=raw_body, headers=headers):
        raise HTTPException(401, "Invalid webhook signature")
    try:
        event = service.provider.parse_webhook(raw_body=raw_body)
    except Exception as exc:
        raise HTTPException(400, f"Unparseable webhook body: {exc}") from exc
    try:
        outcome = service.process_webhook_event(db, event)
    except BookingEngineError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return _webhook_response(outcome)


@app.post("/api/bookings", response_model=BookingOut, status_code=201,
          dependencies=[Depends(rate_limit("hold")), Depends(rate_limit("hold_identity", "identity"))])
def create_booking(payload: BookingCreateRequest, db: Session = Depends(get_db)):
    cleanup_expired_holds(db)
    try:
        user = get_or_create_user(payload.user, db)
        booking = create_hold_for_user(db, user, payload.show_id, payload.seat_ids, payload.payment_method)
        confirmed = confirm_booking_reference(db, booking.booking_reference, payload.payment_method)
        return booking_out(db, confirmed)
    except BookingEngineError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except HTTPException:
        raise

@app.get("/api/bookings/{reference}", response_model=BookingOut,
         dependencies=[Depends(rate_limit("booking_ref"))])
def get_booking(reference: str, db: Session = Depends(get_db)):
    cleanup_expired_holds(db)
    # Guest capability lookup: reference shape is validated first so probing
    # never reaches the database and always returns the same 404.
    if not is_valid_booking_reference(reference):
        raise HTTPException(404,"Booking not found")
    b=db.scalar(select(Booking).where(Booking.booking_reference==reference))
    if not b: raise HTTPException(404,"Booking not found")
    return booking_out(db,b)

@app.get("/api/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return UserOut.model_validate(user, from_attributes=True)


@app.patch("/api/me", response_model=UserOut)
def update_me(payload: UserProfileUpdate, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if payload.full_name is not None:
        user.full_name = payload.full_name.strip()
    if payload.phone is not None:
        user.phone = payload.phone
    db.add(user)
    db.commit(); db.refresh(user)
    return UserOut.model_validate(user, from_attributes=True)


@app.post("/api/me/password")
def change_password(payload: PasswordChangeRequest, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid current password")
    if payload.new_password == payload.current_password:
        raise HTTPException(status_code=422, detail="New password must be different from the current password")
    user.password_hash = hash_password(payload.new_password)
    user.token_version = (user.token_version or 0) + 1
    db.add(user)
    db.commit()
    return {"status": "ok"}


@app.get("/api/me/bookings", response_model=list[BookingOut])
def my_bookings(db: Session=Depends(get_db), user: User=Depends(current_user)):
    cleanup_expired_holds(db)
    rows=db.scalars(select(Booking).where(Booking.user_id==user.id).order_by(Booking.created_at.desc())).all()
    return [booking_out(db,b) for b in rows]

VALID_REVIEW_SORTS = {"newest", "oldest", "highest", "lowest", "most_liked"}


def _review_eligibility_for_user(db: Session, user: User, movie_id: int) -> None:
    if not movie_id:
        raise HTTPException(422, "Movie is required")
    movie = db.get(Movie, movie_id)
    if not movie:
        raise HTTPException(404, "Movie not found")
    booking = db.scalar(
        select(Booking)
        .where(
            Booking.user_id == user.id,
            Booking.status == BookingStatus.CONFIRMED.value,
            Booking.show_id.in_(
                select(Show.id).where(Show.movie_id == movie_id)
            ),
        )
        .order_by(Booking.created_at.desc())
    )
    if not booking:
        raise HTTPException(403, "Only confirmed ticket holders can review this movie")


def _review_list_query(db: Session, sort: str, page: int, limit: int):
    stmt = (
        select(Review, User, func.coalesce(func.count(ReviewLike.id), 0).label("like_count"))
        .join(User, Review.user_id == User.id)
        .outerjoin(ReviewLike, ReviewLike.review_id == Review.id)
        .group_by(Review.id, User.id)
    )
    if sort == "newest":
        stmt = stmt.order_by(Review.created_at.desc(), Review.id.desc())
    elif sort == "oldest":
        stmt = stmt.order_by(Review.created_at.asc(), Review.id.asc())
    elif sort == "highest":
        stmt = stmt.order_by(Review.rating.desc(), Review.created_at.desc())
    elif sort == "lowest":
        stmt = stmt.order_by(Review.rating.asc(), Review.created_at.desc())
    elif sort == "most_liked":
        stmt = stmt.order_by(func.coalesce(func.count(ReviewLike.id), 0).desc(), Review.created_at.desc())
    else:
        raise HTTPException(422, "Invalid sort")
    return stmt.offset((page - 1) * limit).limit(limit)


@app.get("/api/reviews")
def reviews(
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=20),
    sort: str = Query("newest"),
    user: User | None = Depends(optional_current_user),
):
    if sort not in VALID_REVIEW_SORTS:
        raise HTTPException(422, "Invalid sort")
    total = db.scalar(select(func.count()).select_from(Review))
    stmt = (
        select(Review, User, func.coalesce(func.count(ReviewLike.id), 0).label("like_count"))
        .join(User, Review.user_id == User.id)
        .outerjoin(ReviewLike, ReviewLike.review_id == Review.id)
        .group_by(Review.id, User.id)
    )
    if sort == "newest":
        stmt = stmt.order_by(Review.created_at.desc(), Review.id.desc())
    elif sort == "oldest":
        stmt = stmt.order_by(Review.created_at.asc(), Review.id.asc())
    elif sort == "highest":
        stmt = stmt.order_by(Review.rating.desc(), Review.created_at.desc())
    elif sort == "lowest":
        stmt = stmt.order_by(Review.rating.asc(), Review.created_at.desc())
    elif sort == "most_liked":
        stmt = stmt.order_by(func.coalesce(func.count(ReviewLike.id), 0).desc(), Review.created_at.desc())
    rows = db.execute(stmt.offset((page - 1) * limit).limit(limit)).all()
    liked_review_ids = set()
    if user is not None:
        liked_review_ids = set(
            db.scalars(
                select(ReviewLike.review_id).where(ReviewLike.user_id == user.id, ReviewLike.review_id.in_([r.id for r, _, _ in rows]))
            ).all()
        )
    items = []
    for review, reviewer, like_count in rows:
        items.append({
            "id": review.id,
            "user_id": review.user_id,
            "name": reviewer.full_name,
            "rating": review.rating,
            "title": review.title,
            "body": review.body,
            "spoiler": review.spoiler,
            "likes": int(like_count),
            "liked": user is not None and review.id in liked_review_ids,
            "created_at": review.created_at.isoformat(),
            "updated_at": review.updated_at.isoformat() if review.updated_at else None,
        })
    return {
        "items": items,
        "page": page,
        "limit": limit,
        "total": total or 0,
        "has_more": total > page * limit,
        "average_rating": round((db.scalar(select(func.avg(Review.rating))) or 0), 2) if total else 0.0,
        "total_reviews": total or 0,
    }


@app.post("/api/reviews/{review_id}/like", dependencies=[Depends(rate_limit("review_like"))])
def like_review(review_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    review = db.get(Review, review_id)
    if not review:
        raise HTTPException(404, "Review not found")
    existing = db.scalar(select(ReviewLike).where(ReviewLike.review_id == review_id, ReviewLike.user_id == user.id))
    if existing:
        db.delete(existing)
        liked = False
    else:
        try:
            db.add(ReviewLike(review_id=review_id, user_id=user.id))
            db.flush()
            liked = True
        except IntegrityError:
            # Expected race: a concurrent request inserted the same
            # (review_id, user_id) row between our SELECT and INSERT.
            # Recover by observing the committed state — any integrity
            # failure that did NOT leave our like row present is re-raised,
            # so unexpected DB errors are never masked as a successful like.
            db.rollback()
            existing = db.scalar(select(ReviewLike).where(ReviewLike.review_id == review_id, ReviewLike.user_id == user.id))
            if existing is None:
                raise
            liked = True
    db.commit()
    count = db.scalar(select(func.count()).select_from(ReviewLike).where(ReviewLike.review_id == review_id)) or 0
    return {"likes": count, "liked": liked}


@app.post("/api/reviews", status_code=201, dependencies=[Depends(rate_limit("review_create"))])
def create_review(payload: ReviewCreate, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _review_eligibility_for_user(db, user, payload.movie_id)
    review = Review(user_id=user.id, movie_id=payload.movie_id, **payload.model_dump(exclude={"movie_id"}))
    db.add(review)
    try:
        db.commit()
    except IntegrityError:
        # Only the (user_id, movie_id) duplicate becomes 409; verify the
        # duplicate row actually exists so every other integrity failure
        # surfaces as-is instead of being disguised as "already reviewed".
        db.rollback()
        duplicate = db.scalar(
            select(Review.id).where(Review.user_id == user.id, Review.movie_id == payload.movie_id)
        )
        if duplicate is None:
            raise
        raise HTTPException(409, "You have already reviewed this movie")
    db.refresh(review)
    return {"id": review.id, "message": "Review created"}


@app.get("/api/reviews/{review_id}")
def get_review(review_id: int, db: Session = Depends(get_db), user: User | None = Depends(optional_current_user)):
    row = db.execute(
        select(Review, User, func.coalesce(func.count(ReviewLike.id), 0).label("like_count"))
        .join(User, Review.user_id == User.id)
        .outerjoin(ReviewLike, ReviewLike.review_id == Review.id)
        .where(Review.id == review_id)
        .group_by(Review.id, User.id)
    ).first()
    if not row:
        raise HTTPException(404, "Review not found")
    review, reviewer, like_count = row
    liked_ids = set()
    if user is not None:
        liked_ids = set(db.scalars(
            select(ReviewLike.review_id).where(ReviewLike.user_id == user.id, ReviewLike.review_id == review_id)
        ).all())
    return {
        "id": review.id,
        "user_id": review.user_id,
        "name": reviewer.full_name,
        "rating": review.rating,
        "title": review.title,
        "body": review.body,
        "spoiler": review.spoiler,
        "likes": int(like_count),
        "liked": user is not None and review.id in liked_ids,
        "created_at": review.created_at.isoformat(),
        "updated_at": review.updated_at.isoformat() if review.updated_at else None,
    }


@app.patch("/api/reviews/{review_id}", dependencies=[Depends(rate_limit("review_update_delete"))])
def update_review(review_id: int, payload: ReviewUpdate, db: Session = Depends(get_db), user: User = Depends(current_user)):
    review = db.get(Review, review_id)
    if not review:
        raise HTTPException(404, "Review not found")
    if review.user_id != user.id:
        raise HTTPException(403, "You cannot edit another user's review")
    data = payload.model_dump(exclude_unset=True)
    if "rating" in data and data["rating"] is not None:
        review.rating = data["rating"]
    if "title" in data and data["title"] is not None:
        review.title = data["title"].strip()
    if "body" in data and data["body"] is not None:
        review.body = data["body"].strip()
    if "spoiler" in data:
        review.spoiler = bool(data["spoiler"])
    db.add(review)
    db.commit()
    db.refresh(review)
    return {
        "id": review.id,
        "rating": review.rating,
        "title": review.title,
        "body": review.body,
        "spoiler": review.spoiler,
        "updated_at": review.updated_at.isoformat(),
        "message": "Review updated",
    }


@app.delete("/api/reviews/{review_id}", dependencies=[Depends(rate_limit("review_update_delete"))])
def delete_review(review_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    review = db.get(Review, review_id)
    if not review:
        raise HTTPException(404, "Review not found")
    if review.user_id != user.id:
        raise HTTPException(403, "You cannot delete another user's review")
    db.delete(review)
    db.commit()
    return {"status": "deleted", "review_id": review_id}

@app.post("/api/newsletter", status_code=201, dependencies=[Depends(rate_limit("newsletter"))])
def newsletter(payload: NewsletterCreate, db: Session=Depends(get_db)):
    if not db.scalar(select(NewsletterSubscriber).where(NewsletterSubscriber.email==payload.email.lower())):
        db.add(NewsletterSubscriber(name=payload.name.strip(),email=payload.email.lower())); db.commit()
    return {"status":"subscribed"}

@app.post("/api/contact", status_code=201, dependencies=[Depends(rate_limit("contact"))])
def contact(payload: ContactCreate, db: Session=Depends(get_db)):
    db.add(ContactMessage(**payload.model_dump())); db.commit()
    return {"status":"received"}

# Static frontend — backend source is not exposed.
app.mount("/assets", StaticFiles(directory=ROOT/"assets"), name="assets")
app.mount("/css", StaticFiles(directory=ROOT/"css"), name="css")
app.mount("/js", StaticFiles(directory=ROOT/"js"), name="js")

@app.get("/", include_in_schema=False)
def index():
    return FileResponse(ROOT/"index.html")
