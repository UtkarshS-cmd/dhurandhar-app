"""PostgreSQL foreign-key tests — invalid references must fail; cascades honored."""
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import Booking, City, Movie, Screen, Seat, Show, Theater, User


def _base_ids(pg_session):
    return {
        "user_id": pg_session.scalar(select(User.id)),
        "show_id": pg_session.scalar(select(Show.id)),
        "seat_id": pg_session.scalar(select(Seat.id)),
        "movie_id": pg_session.scalar(select(Movie.id)),
    }


def test_booking_requires_valid_user_and_show(pg_session):
    ids = _base_ids(pg_session)
    bad_user = Booking(
        booking_reference="DHR-" + "u" * 24,
        user_id=999999999,
        show_id=ids["show_id"],
        status="HELD",
        payment_status="PENDING",
        total_amount="100.00",
    )
    pg_session.add(bad_user)
    with pytest.raises(IntegrityError):
        pg_session.flush()
    pg_session.rollback()

    bad_show = Booking(
        booking_reference="DHR-" + "s" * 24,
        user_id=ids["user_id"],
        show_id=999999999,
        status="HELD",
        payment_status="PENDING",
        total_amount="100.00",
    )
    pg_session.add(bad_show)
    with pytest.raises(IntegrityError):
        pg_session.flush()
    pg_session.rollback()


def test_city_cascade_deletes_theater_tree(pg_session):
    city = City(name="PG Cascade City")
    pg_session.add(city)
    pg_session.flush()
    theater = Theater(city_id=city.id, name="PG Cascade Theater", address="x")
    pg_session.add(theater)
    pg_session.flush()
    screen = Screen(theater_id=theater.id, name="PG Cascade Screen")
    pg_session.add(screen)
    pg_session.flush()
    pg_session.delete(city)
    pg_session.flush()
    assert pg_session.get(Theater, theater.id) is None
    assert pg_session.get(Screen, screen.id) is None
