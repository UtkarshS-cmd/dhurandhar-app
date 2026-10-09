"""Admin dashboard: aggregated operational metrics (COUNT/SUM only)."""

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_admin
from ..models import (
    Booking, BookingStatus, City, ContactMessage, Movie, NewsletterSubscriber,
    PaymentAttempt, PaymentAttemptStatus, PaymentStatus, Review, Show, Theater, User,
)
from ..ratelimit import rate_limit

router = APIRouter(prefix="/api/admin/dashboard", tags=["admin-dashboard"])


@router.get("", dependencies=[Depends(rate_limit("admin_dashboard"))])
def dashboard(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    # Two GROUP BY queries replace ~16 sequential COUNT/SUM round-trips.
    # Booking statuses: one grouped count + one conditional revenue sum.
    booking_rows = db.execute(
        select(
            Booking.status,
            func.count(Booking.id),
            func.sum(
                case(
                    (Booking.payment_status == PaymentStatus.PAID.value, Booking.total_amount),
                    else_=0,
                )
            ),
        ).group_by(Booking.status)
    ).all()
    counts = {status: int(n or 0) for status, n, _ in booking_rows}
    revenue = sum((float(total or 0) for status, _, total in booking_rows
                   if status == BookingStatus.CONFIRMED.value), 0.0)
    total_users = int(db.scalar(select(func.count(User.id))) or 0)
    active_users = int(db.scalar(select(func.count(User.id)).where(User.is_active == True)) or 0)  # noqa: E712
    total_movies = int(db.scalar(select(func.count(Movie.id))) or 0)
    total_theaters = int(db.scalar(select(func.count(Theater.id))) or 0)
    total_shows = int(db.scalar(select(func.count(Show.id))) or 0)
    upcoming_shows = int(db.scalar(select(func.count(Show.id)).where(Show.show_date >= date.today())) or 0)
    total_bookings = sum(counts.values())
    confirmed = counts.get(BookingStatus.CONFIRMED.value, 0)
    held = counts.get(BookingStatus.HELD.value, 0)
    cancelled = counts.get(BookingStatus.CANCELLED.value, 0)
    attempt_rows = db.execute(
        select(PaymentAttempt.status, func.count(PaymentAttempt.id))
        .group_by(PaymentAttempt.status)
    ).all()
    attempts = {status: int(n or 0) for status, n in attempt_rows}
    pending_payments = sum(attempts.get(s, 0) for s in (
        PaymentAttemptStatus.CREATED.value, PaymentAttemptStatus.PENDING.value,
        PaymentAttemptStatus.AUTHORIZED.value))
    failed_payments = attempts.get(PaymentAttemptStatus.FAILED.value, 0)
    total_reviews = int(db.scalar(select(func.count(Review.id))) or 0)
    unread_contacts = int(db.scalar(select(func.count(ContactMessage.id)).where(ContactMessage.is_read == False)) or 0)  # noqa: E712
    subscribers = int(db.scalar(select(func.count(NewsletterSubscriber.id))) or 0)
    return {
        "total_users": total_users, "active_users": active_users,
        "total_movies": total_movies, "total_theaters": total_theaters,
        "total_shows": total_shows, "upcoming_shows": upcoming_shows,
        "total_bookings": total_bookings, "confirmed_bookings": confirmed,
        "held_bookings": held, "cancelled_bookings": cancelled,
        "total_revenue": float(revenue or 0),
        "pending_payments": pending_payments, "failed_payments": failed_payments,
        "total_reviews": total_reviews, "pending_reviews": 0,
        "unread_contact_messages": unread_contacts,
        "newsletter_subscribers": subscribers,
    }
