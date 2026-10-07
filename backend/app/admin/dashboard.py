"""Admin dashboard: aggregated operational metrics (COUNT/SUM only)."""

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
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
    total_users = int(db.scalar(select(func.count(User.id))) or 0)
    active_users = int(db.scalar(select(func.count(User.id)).where(User.is_active == True)) or 0)  # noqa: E712
    total_movies = int(db.scalar(select(func.count(Movie.id))) or 0)
    total_theaters = int(db.scalar(select(func.count(Theater.id))) or 0)
    total_shows = int(db.scalar(select(func.count(Show.id))) or 0)
    upcoming_shows = int(db.scalar(select(func.count(Show.id)).where(Show.show_date >= date.today())) or 0)
    total_bookings = int(db.scalar(select(func.count(Booking.id))) or 0)
    confirmed = int(db.scalar(select(func.count(Booking.id)).where(Booking.status == BookingStatus.CONFIRMED.value)) or 0)
    held = int(db.scalar(select(func.count(Booking.id)).where(Booking.status == BookingStatus.HELD.value)) or 0)
    cancelled = int(db.scalar(select(func.count(Booking.id)).where(Booking.status == BookingStatus.CANCELLED.value)) or 0)
    revenue = db.scalar(select(func.coalesce(func.sum(Booking.total_amount), 0)).where(
        Booking.status == BookingStatus.CONFIRMED.value, Booking.payment_status == PaymentStatus.PAID.value))
    pending_payments = int(db.scalar(select(func.count(PaymentAttempt.id)).where(
        PaymentAttempt.status.in_((PaymentAttemptStatus.CREATED.value, PaymentAttemptStatus.PENDING.value,
                                   PaymentAttemptStatus.AUTHORIZED.value)))) or 0)
    failed_payments = int(db.scalar(select(func.count(PaymentAttempt.id)).where(
        PaymentAttempt.status == PaymentAttemptStatus.FAILED.value)) or 0)
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
