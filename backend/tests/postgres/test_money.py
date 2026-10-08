"""PostgreSQL NUMERIC(10,2) round-trips — exact decimals, never float math."""
from decimal import Decimal

from sqlalchemy import select

from app.models import Booking, BookingSeat, Seat, Show


def test_money_columns_round_trip_exactly(pg_session):
    seat = pg_session.scalars(select(Seat)).first()
    seat.price = Decimal("9999.99")
    pg_session.flush()
    pg_session.expire_all()
    assert pg_session.get(Seat, seat.id).price == Decimal("9999.99")

    show_id = pg_session.scalar(select(Show.id))
    from app.models import User

    user_id = pg_session.scalar(select(User.id))
    booking = Booking(
        booking_reference="DHR-" + "m" * 24,
        user_id=user_id,
        show_id=show_id,
        status="HELD",
        payment_status="PENDING",
        total_amount=Decimal("450.00"),
    )
    pg_session.add(booking)
    pg_session.flush()
    assert booking.total_amount == Decimal("450.00")

    row = BookingSeat(booking_id=booking.id, show_id=show_id, seat_id=seat.id, price=Decimal("350.00"))
    pg_session.add(row)
    pg_session.flush()
    pg_session.expire_all()
    assert pg_session.get(BookingSeat, row.id).price == Decimal("350.00")
    assert pg_session.get(Booking, booking.id).total_amount == Decimal("450.00")


def test_numeric_column_types_are_numeric_10_2(pg_session):
    from sqlalchemy import text

    rows = pg_session.execute(
        text(
            "SELECT table_name, column_name, numeric_precision, numeric_scale "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' AND data_type = 'numeric'"
        )
    ).all()
    by_col = {(r[0], r[1]): (r[2], r[3]) for r in rows}
    assert by_col.get(("seats", "price")) == (10, 2)
    assert by_col.get(("bookings", "total_amount")) == (10, 2)
    assert by_col.get(("booking_seats", "price")) == (10, 2)
    assert by_col.get(("payment_attempts", "amount")) == (10, 2)
