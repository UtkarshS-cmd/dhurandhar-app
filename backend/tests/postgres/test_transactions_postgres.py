"""PostgreSQL transaction rollback — a mid-transaction failure rolls everything back."""
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.models import Booking, BookingSeat, BookingStatus, Show, ShowSeat, ShowSeatStatus, User
from app.services.booking import booking_transaction


def test_pg_failed_transaction_leaves_no_partial_state(pg_session, pg_migrated):
    show_id = pg_session.scalar(select(Show.id))
    user_id = pg_session.scalar(select(User.id))
    seat_id = pg_session.scalar(
        select(ShowSeat.seat_id).where(
            ShowSeat.show_id == show_id, ShowSeat.status == ShowSeatStatus.AVAILABLE.value
        )
    )
    before_bookings = pg_session.scalar(select(func.count()).select_from(Booking))
    before_links = pg_session.scalar(select(func.count()).select_from(BookingSeat))

    factory = sessionmaker(bind=pg_migrated, autoflush=False, expire_on_commit=False, future=True)
    db = factory()
    try:
        def _op():
            booking = Booking(
                booking_reference="DHR-" + "r" * 24,
                user_id=user_id,
                show_id=show_id,
                status=BookingStatus.HELD.value,
                payment_status="PENDING",
                total_amount="450.00",
            )
            db.add(booking)
            db.flush()
            db.add(BookingSeat(booking_id=booking.id, show_id=show_id, seat_id=seat_id, price="450.00"))
            db.flush()
            raise RuntimeError("injected failure before commit")

        try:
            booking_transaction(db, _op)
        except RuntimeError:
            pass
    finally:
        db.close()

    pg_session.expire_all()
    assert pg_session.scalar(select(func.count()).select_from(Booking)) == before_bookings
    assert pg_session.scalar(select(func.count()).select_from(BookingSeat)) == before_links
    row = pg_session.scalar(select(ShowSeat).where(ShowSeat.show_id == show_id, ShowSeat.seat_id == seat_id))
    assert row.status == ShowSeatStatus.AVAILABLE.value
