
from datetime import datetime, date, time
from enum import Enum
from sqlalchemy import (
    String, Integer, DateTime, Date, Time, ForeignKey, Numeric, Boolean,
    UniqueConstraint, Index, Text
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base

class SeatCategory(str, Enum):
    GOLD = "Gold"
    SILVER = "Silver"
    BRONZE = "Bronze"

class UserRole(str, Enum):
    """Extensible RBAC role.

    Phase 8 ships ``USER``/``ADMIN`` only; the string column + enum leave
    room for future ``MODERATOR``/``STAFF`` values without schema churn.
    The database is authoritative — roles never live in JWT claims.
    """

    USER = "USER"
    ADMIN = "ADMIN"


class ShowSeatStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    HELD = "HELD"
    BOOKED = "BOOKED"

class BookingStatus(str, Enum):
    HELD = "HELD"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"

class PaymentStatus(str, Enum):
    PENDING = "PENDING"
    PAID = "PAID"
    FAILED = "FAILED"


class PaymentAttemptStatus(str, Enum):
    """External payment lifecycle (Phase 4).

    The legacy ``Booking.payment_status`` (PENDING/PAID/FAILED) is preserved
    as a booking-level mirror so Phase 3 readers keep working. The
    per-attempt table below is the authoritative external-payment state.
    """

    CREATED = "CREATED"
    PENDING = "PENDING"
    AUTHORIZED = "AUTHORIZED"
    PAID = "PAID"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"


class WebhookEventStatus(str, Enum):
    RECEIVED = "RECEIVED"
    PROCESSED = "PROCESSED"
    IGNORED = "IGNORED"
    FAILED = "FAILED"

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    phone: Mapped[str] = mapped_column(String(20))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default=UserRole.USER.value, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    token_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    bookings = relationship("Booking", back_populates="user")
    reviews = relationship("Review", back_populates="user", cascade="all, delete-orphan")

class Movie(Base):
    __tablename__ = "movies"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), unique=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    shows = relationship("Show", back_populates="movie")
    reviews = relationship("Review", back_populates="movie", cascade="all, delete-orphan")

class City(Base):
    __tablename__ = "cities"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    theaters = relationship("Theater", back_populates="city")

class Theater(Base):
    __tablename__ = "theaters"
    id: Mapped[int] = mapped_column(primary_key=True)
    city_id: Mapped[int] = mapped_column(ForeignKey("cities.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    address: Mapped[str] = mapped_column(String(300))
    city = relationship("City", back_populates="theaters")
    screens = relationship("Screen", back_populates="theater", cascade="all, delete-orphan")

class Screen(Base):
    __tablename__ = "screens"
    id: Mapped[int] = mapped_column(primary_key=True)
    theater_id: Mapped[int] = mapped_column(ForeignKey("theaters.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    theater = relationship("Theater", back_populates="screens")
    seats = relationship("Seat", back_populates="screen", cascade="all, delete-orphan")
    shows = relationship("Show", back_populates="screen")

class Seat(Base):
    __tablename__ = "seats"
    id: Mapped[int] = mapped_column(primary_key=True)
    screen_id: Mapped[int] = mapped_column(ForeignKey("screens.id", ondelete="CASCADE"), index=True)
    row_label: Mapped[str] = mapped_column(String(5))
    seat_number: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(20))
    price: Mapped[float] = mapped_column(Numeric(10, 2))
    screen = relationship("Screen", back_populates="seats")
    __table_args__ = (UniqueConstraint("screen_id", "row_label", "seat_number", name="uq_screen_seat"),)

class Show(Base):
    __tablename__ = "shows"
    id: Mapped[int] = mapped_column(primary_key=True)
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"), index=True)
    screen_id: Mapped[int] = mapped_column(ForeignKey("screens.id", ondelete="CASCADE"), index=True)
    show_date: Mapped[date] = mapped_column(Date, index=True)
    show_time: Mapped[time] = mapped_column(Time)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    movie = relationship("Movie", back_populates="shows")
    screen = relationship("Screen", back_populates="shows")
    show_seats = relationship("ShowSeat", back_populates="show", cascade="all, delete-orphan")
    __table_args__ = (UniqueConstraint("screen_id", "show_date", "show_time", name="uq_screen_showtime"),)

class Booking(Base):
    __tablename__ = "bookings"
    id: Mapped[int] = mapped_column(primary_key=True)
    booking_reference: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    show_id: Mapped[int] = mapped_column(ForeignKey("shows.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default=BookingStatus.HELD.value)
    total_amount: Mapped[float] = mapped_column(Numeric(10, 2))
    payment_method: Mapped[str] = mapped_column(String(60), default="MOCK")
    payment_status: Mapped[str] = mapped_column(String(20), default=PaymentStatus.PENDING.value)
    hold_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    user = relationship("User", back_populates="bookings")
    show = relationship("Show")
    booking_seats = relationship("BookingSeat", back_populates="booking", cascade="all, delete-orphan")
    payment_attempts = relationship("PaymentAttempt", back_populates="booking", cascade="all, delete-orphan")

class ShowSeat(Base):
    __tablename__ = "show_seats"
    id: Mapped[int] = mapped_column(primary_key=True)
    show_id: Mapped[int] = mapped_column(ForeignKey("shows.id", ondelete="CASCADE"), index=True)
    seat_id: Mapped[int] = mapped_column(ForeignKey("seats.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default=ShowSeatStatus.AVAILABLE.value, index=True)
    hold_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    booking_id: Mapped[int | None] = mapped_column(ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True, index=True)
    show = relationship("Show", back_populates="show_seats")
    seat = relationship("Seat")
    __table_args__ = (UniqueConstraint("show_id", "seat_id", name="uq_show_seat"),)

class BookingSeat(Base):
    __tablename__ = "booking_seats"
    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"), index=True)
    show_id: Mapped[int] = mapped_column(ForeignKey("shows.id", ondelete="CASCADE"), index=True)
    seat_id: Mapped[int] = mapped_column(ForeignKey("seats.id", ondelete="CASCADE"), index=True)
    price: Mapped[float] = mapped_column(Numeric(10, 2))
    booking = relationship("Booking", back_populates="booking_seats")
    seat = relationship("Seat")
    __table_args__ = (UniqueConstraint("booking_id", "seat_id", name="uq_booking_seat"),)


class PaymentAttempt(Base):
    """One external payment attempt against a booking (Phase 4).

    A booking keeps exactly one *active* attempt (CREATED/PENDING/AUTHORIZED);
    older attempts are terminal history (PAID/FAILED/CANCELLED/REFUNDED).
    Internal identity is ``id``; external identity is
    (``provider``, ``provider_order_id``, ``provider_payment_id``). The
    ``amount``/``currency`` snapshot is server-authoritative, copied from the
    booking inside the creation transaction — never from client input.
    """

    __tablename__ = "payment_attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(30), default="mock", index=True)
    provider_order_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    provider_payment_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    amount: Mapped[float] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(10), default="INR")
    status: Mapped[str] = mapped_column(String(20), default=PaymentAttemptStatus.CREATED.value, index=True)
    attempt_no: Mapped[int] = mapped_column(Integer, default=1)
    failure_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    booking = relationship("Booking", back_populates="payment_attempts")
    __table_args__ = (
        UniqueConstraint("booking_id", "attempt_no", name="uq_payment_attempt_no"),
        Index("ix_payment_attempt_provider_order", "provider", "provider_order_id"),
    )


class PaymentWebhookEvent(Base):
    """Durable webhook/event idempotency record (Phase 4).

    Unique on (provider, event_id) so redeliveries — even after a process
    restart — are detected from the database, never from memory.
    """

    __tablename__ = "payment_webhook_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(30), index=True)
    event_id: Mapped[str] = mapped_column(String(160), index=True)
    event_type: Mapped[str] = mapped_column(String(120))
    payment_attempt_id: Mapped[int | None] = mapped_column(
        ForeignKey("payment_attempts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(20), default=WebhookEventStatus.RECEIVED.value, index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    __table_args__ = (UniqueConstraint("provider", "event_id", name="uq_webhook_provider_event"),)

class Review(Base):
    __tablename__ = "reviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"), index=True)
    rating: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text)
    spoiler: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    user = relationship("User", back_populates="reviews")
    movie = relationship("Movie", back_populates="reviews")
    __table_args__ = (
        UniqueConstraint("user_id", "movie_id", name="uq_review_user_movie"),
        Index("ix_reviews_movie_created_at", "movie_id", "created_at"),
    )

class ReviewLike(Base):
    __tablename__ = "review_likes"
    id: Mapped[int] = mapped_column(primary_key=True)
    review_id: Mapped[int] = mapped_column(ForeignKey("reviews.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("review_id", "user_id", name="uq_review_user_like"),)

class NewsletterSubscriber(Base):
    __tablename__ = "newsletter_subscribers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class ContactMessage(Base):
    __tablename__ = "contact_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(120))
    message: Mapped[str] = mapped_column(Text)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AdminAuditLog(Base):
    """Append-only record of administrative mutations (Phase 8).

    ``admin_user_id`` is SET NULL on user deletion (users are never
    hard-deleted, but the FK must not block history). ``metadata_json`` holds
    small operational context only — never passwords, tokens, or secrets.
    """

    __tablename__ = "admin_audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    admin_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(60), index=True)
    resource_type: Mapped[str] = mapped_column(String(60), index=True)
    resource_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
