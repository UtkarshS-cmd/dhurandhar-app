
from datetime import date, datetime, timedelta, time
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, status, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, or_, func
from sqlalchemy.orm import Session, joinedload

from .config import settings, ROOT
from .db import get_db
from .models import (
    User, Movie, City, Theater, Screen, Seat, Show, ShowSeat,
    Booking, BookingSeat, Review, ReviewLike, NewsletterSubscriber, ContactMessage,
    ShowSeatStatus, BookingStatus, PaymentStatus
)
from .schemas import *
from .security import (
    hash_password, verify_password, create_token, decode_token,
    AuthenticationError, generate_booking_reference, is_valid_booking_reference,
)
from .ratelimit import rate_limit
from .services.payment import payment_gateway

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
        allow_methods=["GET", "POST", "OPTIONS"],
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
    now = datetime.utcnow()
    seats = db.scalars(select(ShowSeat).where(
        ShowSeat.status == ShowSeatStatus.HELD.value,
        ShowSeat.hold_expires_at < now
    )).all()
    for ss in seats:
        ss.status = ShowSeatStatus.AVAILABLE.value
        ss.hold_expires_at = None
        ss.booking_id = None
    expired_bookings = db.scalars(select(Booking).where(
        Booking.status == BookingStatus.HELD.value,
        Booking.hold_expires_at < now
    )).all()
    for b in expired_bookings:
        b.status = BookingStatus.CANCELLED.value
    db.commit()

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
        uid = decode_token(token.strip())
    except AuthenticationError:
        raise HTTPException(401, "Invalid or expired session", headers={"WWW-Authenticate": "Bearer"})
    user = db.get(User, uid)
    if not user:
        raise HTTPException(401, "Invalid or expired session", headers={"WWW-Authenticate": "Bearer"})
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
        password_hash=hash_password(payload.password)
    )
    db.add(user); db.commit(); db.refresh(user)
    return AuthOut(access_token=create_token(user.id), user=UserOut.model_validate(user, from_attributes=True))

@app.post("/api/auth/login", response_model=AuthOut,
          dependencies=[Depends(rate_limit("login", "identity")), Depends(rate_limit("login_ip"))])
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    # Uniform failure for unknown email and wrong password alike: responses
    # never reveal which accounts exist.
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return AuthOut(access_token=create_token(user.id), user=UserOut.model_validate(user, from_attributes=True))

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
    show=db.get(Show,show_id)
    if not show or show.status!="ACTIVE":
        raise HTTPException(404,"Show not found or inactive")
    if len(set(seat_ids)) != len(seat_ids):
        raise HTTPException(422,"Duplicate seats are not allowed")
    rows=db.execute(
        select(ShowSeat,Seat).join(Seat,ShowSeat.seat_id==Seat.id).where(
            ShowSeat.show_id==show_id, Seat.id.in_(seat_ids)
        )
    ).all()
    if len(rows)!=len(seat_ids):
        raise HTTPException(422,"One or more seats do not belong to this show")
    return show, rows

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
        with db.begin():
            user=get_or_create_user(payload.user,db)
            show, rows=validate_show_and_seats(payload.show_id,payload.seat_ids,db)
            # PostgreSQL row locking; SQLite serializes writes.
            locked=db.scalars(
                select(ShowSeat).where(ShowSeat.show_id==show.id,ShowSeat.seat_id.in_(payload.seat_ids)).with_for_update()
            ).all()
            now=datetime.utcnow()
            for ss in locked:
                if ss.status == ShowSeatStatus.BOOKED.value:
                    raise HTTPException(409, detail={"message":"One or more selected seats are already booked","seat_ids":[ss.seat_id]})
                if ss.status == ShowSeatStatus.HELD.value and ss.hold_expires_at and ss.hold_expires_at > now:
                    raise HTTPException(409, detail={"message":"One or more selected seats are temporarily held","seat_ids":[ss.seat_id]})
            expires=now+timedelta(minutes=10)
            # 144-bit cryptographically random reference (see security.py);
            # never derived from database IDs or short token_hex output.
            ref=generate_booking_reference()
            total=sum(Decimal(seat.price) for _,seat in rows)
            booking=Booking(
                booking_reference=ref,user_id=user.id,show_id=show.id,status=BookingStatus.HELD.value,
                total_amount=total,payment_method=payload.payment_method,payment_status=PaymentStatus.PENDING.value,
                hold_expires_at=expires
            )
            db.add(booking); db.flush()
            for ss,seat in rows:
                ss.status=ShowSeatStatus.HELD.value; ss.hold_expires_at=expires; ss.booking_id=booking.id
                db.add(BookingSeat(booking_id=booking.id,show_id=show.id,seat_id=seat.id,price=seat.price))
        return booking_out(db, booking)
    except HTTPException:
        db.rollback(); raise
    except Exception:
        db.rollback(); raise

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
    # Malformed references are indistinguishable from unknown ones (404).
    if not is_valid_booking_reference(reference):
        raise HTTPException(404,"Booking hold not found")
    with db.begin():
        booking=db.scalar(select(Booking).where(Booking.booking_reference==reference).with_for_update())
        if not booking: raise HTTPException(404,"Booking hold not found")
        if booking.status != BookingStatus.HELD.value: raise HTTPException(409,"Booking is no longer available for confirmation")
        if not booking.hold_expires_at or booking.hold_expires_at < datetime.utcnow():
            booking.status=BookingStatus.CANCELLED.value
            raise HTTPException(409,"Seat hold expired; please choose seats again")
        seat_rows=db.scalars(select(ShowSeat).where(ShowSeat.booking_id==booking.id).with_for_update()).all()
        if len(seat_rows)==0 or any(s.status!=ShowSeatStatus.HELD.value for s in seat_rows):
            raise HTTPException(409,"Selected seats are no longer held")
        result=payment_gateway.charge(float(booking.total_amount),payload.payment_method)
        booking.payment_method=payload.payment_method
        booking.payment_status=result.status
        booking.status=BookingStatus.CONFIRMED.value
        booking.hold_expires_at=None
        for ss in seat_rows:
            ss.status=ShowSeatStatus.BOOKED.value
            ss.hold_expires_at=None
    return booking_out(db,booking)

@app.post("/api/bookings", response_model=BookingOut, status_code=201,
          dependencies=[Depends(rate_limit("hold")), Depends(rate_limit("hold_identity", "identity"))])
def create_booking(payload: BookingCreateRequest, db: Session = Depends(get_db)):
    hold=create_hold(payload,db)
    return confirm_booking(hold.booking_reference,BookingConfirmRequest(payment_method=payload.payment_method),db)

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

@app.get("/api/me/bookings", response_model=list[BookingOut])
def my_bookings(db: Session=Depends(get_db), user: User=Depends(current_user)):
    cleanup_expired_holds(db)
    rows=db.scalars(select(Booking).where(Booking.user_id==user.id).order_by(Booking.created_at.desc())).all()
    return [booking_out(db,b) for b in rows]

@app.get("/api/reviews")
def reviews(db: Session=Depends(get_db)):
    rows=db.execute(select(Review,User).join(User,Review.user_id==User.id).order_by(Review.created_at.desc()).limit(30)).all()
    like_counts=dict(db.execute(select(ReviewLike.review_id,func.count()).group_by(ReviewLike.review_id)).all())
    return [{"id":r.id,"name":u.full_name,"rating":r.rating,"title":r.title,"body":r.body,"spoiler":r.spoiler,
             "likes":like_counts.get(r.id,0),"created_at":r.created_at.isoformat()} for r,u in rows]

@app.post("/api/reviews/{review_id}/like")
def like_review(review_id: int, db: Session=Depends(get_db), user: User=Depends(current_user)):
    review=db.get(Review, review_id)
    if not review:
        raise HTTPException(404, "Review not found")
    existing=db.scalar(select(ReviewLike).where(
        ReviewLike.review_id == review_id, ReviewLike.user_id == user.id
    ))
    if existing:
        db.delete(existing); liked=False
    else:
        db.add(ReviewLike(review_id=review_id, user_id=user.id)); liked=True
    db.commit()
    count=db.scalar(select(func.count()).select_from(ReviewLike).where(ReviewLike.review_id == review_id))
    return {"likes": count, "liked": liked}

@app.post("/api/reviews", status_code=201)
def create_review(payload: ReviewCreate, db: Session=Depends(get_db), user: User=Depends(current_user)):
    r=Review(user_id=user.id,**payload.model_dump()); db.add(r); db.commit(); db.refresh(r)
    return {"id":r.id}

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
