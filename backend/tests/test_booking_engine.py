"""Phase 3 — booking-engine invariants: state machine, atomicity, reuse,
restart persistence, price integrity and database constraints.
"""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import (
    Booking,
    BookingSeat,
    BookingStatus,
    PaymentStatus,
    Seat,
    ShowSeat,
    ShowSeatStatus,
    User,
)
from app.services.booking import (
    InvalidTransition,
    SeatConflict,
    cleanup_expired_holds,
    confirm_booking_reference,
    create_hold_for_user,
    transition_booking,
    transition_seat,
)
from conftest import TestingSessionLocal, seed_minimal_show


def _make_user(db, prefix):
    user = User(
        full_name=f"{prefix.title()} User",
        email=f"{prefix}-{uuid.uuid4().hex[:12]}@example.com",
        phone="9876543210",
        password_hash="unused",
    )
    db.add(user)
    db.flush()
    return user


def _first_available(db, show_id, count=1):
    rows = (
        db.query(ShowSeat.seat_id)
        .filter(ShowSeat.show_id == show_id, ShowSeat.status == ShowSeatStatus.AVAILABLE.value)
        .order_by(ShowSeat.id)
        .limit(count)
        .all()
    )
    assert len(rows) == count
    return [row.seat_id for row in rows]


def test_state_machine_forbids_backward_transitions():
    with TestingSessionLocal() as db:
        show_id = seed_minimal_show(db)
        seat_id = _first_available(db, show_id)[0]
        user = _make_user(db, "machine")
        booking = create_hold_for_user(db, user, show_id, [seat_id], "PENDING")
        confirm_booking_reference(db, booking.booking_reference, "PENDING")
        db.expire_all()

        confirmed = db.query(Booking).filter_by(booking_reference=booking.booking_reference).one()
        assert confirmed.status == BookingStatus.CONFIRMED.value
        with pytest.raises(InvalidTransition):
            transition_booking(confirmed, BookingStatus.HELD.value)
        with pytest.raises(InvalidTransition):
            transition_booking(confirmed, BookingStatus.CANCELLED.value)
        booked = db.query(ShowSeat).filter_by(show_id=show_id, seat_id=seat_id).one()
        assert booked.status == ShowSeatStatus.BOOKED.value
        with pytest.raises(InvalidTransition):
            transition_seat(booked, ShowSeatStatus.AVAILABLE.value)


def test_partial_availability_fails_atomically():
    with TestingSessionLocal() as db:
        show_id = seed_minimal_show(db)
        seat_ids = _first_available(db, show_id, 3)
        create_hold_for_user(db, _make_user(db, "partial-a"), show_id, [seat_ids[1]], "PENDING")

        with pytest.raises(SeatConflict) as exc_info:
            create_hold_for_user(db, _make_user(db, "partial-b"), show_id, seat_ids, "PENDING")
        assert seat_ids[1] in exc_info.value.detail["seat_ids"]

    with TestingSessionLocal() as db2:
        assert db2.query(Booking).filter_by(show_id=show_id).count() == 1
        held = db2.query(ShowSeat).filter_by(show_id=show_id, status=ShowSeatStatus.HELD.value).all()
        assert [ss.seat_id for ss in held] == [seat_ids[1]]
        assert db2.query(BookingSeat).filter_by(show_id=show_id).count() == 1


def test_expired_hold_reuse_by_another_user():
    with TestingSessionLocal() as db:
        show_id = seed_minimal_show(db)
        seat_id = _first_available(db, show_id)[0]
        first = create_hold_for_user(db, _make_user(db, "reuse-a"), show_id, [seat_id], "PENDING")
        first_ref = first.booking_reference

        past = datetime.utcnow() - timedelta(minutes=1)
        held = db.query(Booking).filter_by(booking_reference=first_ref).one()
        held.hold_expires_at = past
        for ss in db.query(ShowSeat).filter_by(booking_id=held.id):
            ss.hold_expires_at = past
        db.commit()
        db.close()  # simulated restart: process state gone, DB file remains

    with TestingSessionLocal() as db2:
        cleanup_expired_holds(db2)
        assert db2.query(Booking).filter_by(booking_reference=first_ref).one().status == BookingStatus.CANCELLED.value
        second = create_hold_for_user(db2, _make_user(db2, "reuse-b"), show_id, [seat_id], "PENDING")
        assert second.booking_reference != first_ref
        row = db2.query(ShowSeat).filter_by(show_id=show_id, seat_id=seat_id).one()
        assert row.status == ShowSeatStatus.HELD.value
        assert row.booking_id == second.id


def test_restart_persistence_active_and_confirmed():
    """DB is authoritative: restart preserves active holds and confirmations."""
    with TestingSessionLocal() as db:
        show_id = seed_minimal_show(db)
        seat_ids = _first_available(db, show_id, 2)
        active = create_hold_for_user(db, _make_user(db, "restart-a"), show_id, [seat_ids[0]], "PENDING")
        done = create_hold_for_user(db, _make_user(db, "restart-d"), show_id, [seat_ids[1]], "PENDING")
        confirm_booking_reference(db, done.booking_reference, "PENDING")
        active_ref, done_ref = active.booking_reference, done.booking_reference
        db.close()

    with TestingSessionLocal() as db2:
        cleanup_expired_holds(db2)  # must not touch the active hold
        assert db2.query(Booking).filter_by(booking_reference=active_ref).one().status == BookingStatus.HELD.value
        assert db2.query(ShowSeat).filter_by(show_id=show_id, seat_id=seat_ids[0]).one().status == ShowSeatStatus.HELD.value
        assert db2.query(Booking).filter_by(booking_reference=done_ref).one().status == BookingStatus.CONFIRMED.value
        assert db2.query(ShowSeat).filter_by(show_id=show_id, seat_id=seat_ids[1]).one().status == ShowSeatStatus.BOOKED.value


def test_price_integrity_from_database():
    with TestingSessionLocal() as db:
        show_id = seed_minimal_show(db)
        seat_ids = _first_available(db, show_id, 2)
        expected = sum(float(db.get(Seat, sid).price) for sid in seat_ids)
        booking = create_hold_for_user(db, _make_user(db, "pricing"), show_id, seat_ids, "PENDING")
        assert float(booking.total_amount) == pytest.approx(expected)
        for row in db.query(BookingSeat).filter_by(booking_id=booking.id).all():
            assert float(row.price) == pytest.approx(float(db.get(Seat, row.seat_id).price))


def test_database_constraints_guard_invariants():
    with TestingSessionLocal() as db:
        show_id = seed_minimal_show(db)
        seat_id = _first_available(db, show_id)[0]
        user = _make_user(db, "constraints")
        booking = create_hold_for_user(db, user, show_id, [seat_id], "PENDING")
        db.add(BookingSeat(booking_id=booking.id, show_id=show_id, seat_id=seat_id, price=350))
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
        db.add(ShowSeat(show_id=show_id, seat_id=seat_id, status=ShowSeatStatus.AVAILABLE.value))
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
        db.add(
            Booking(
                booking_reference=booking.booking_reference,
                user_id=user.id,
                show_id=show_id,
                status=BookingStatus.HELD.value,
                total_amount=350,
                payment_method="PENDING",
                payment_status=PaymentStatus.PENDING.value,
            )
        )
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()


def test_booking_seat_consistency_invariants_hold():
    with TestingSessionLocal() as db:
        show_id = seed_minimal_show(db)
        seat_ids = _first_available(db, show_id, 2)
        booking = create_hold_for_user(db, _make_user(db, "invariants"), show_id, seat_ids, "PENDING")
        confirm_booking_reference(db, booking.booking_reference, "PENDING")
        db.expire_all()
        confirmed = db.query(Booking).filter_by(booking_reference=booking.booking_reference).one()
        assert confirmed.status == BookingStatus.CONFIRMED.value
        seat_rows = db.query(ShowSeat).filter(ShowSeat.show_id == show_id, ShowSeat.seat_id.in_(seat_ids)).all()
        assert all(ss.status == ShowSeatStatus.BOOKED.value for ss in seat_rows)
        assert all(ss.booking_id == confirmed.id for ss in seat_rows)
        assert {row.seat_id for row in db.query(BookingSeat).filter_by(booking_id=confirmed.id)} == set(seat_ids)
        assert db.query(ShowSeat).filter(ShowSeat.show_id == show_id, ShowSeat.status == ShowSeatStatus.BOOKED.value, ShowSeat.booking_id.is_(None)).count() == 0
