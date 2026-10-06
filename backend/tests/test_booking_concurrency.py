import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Booking, BookingSeat, BookingStatus, PaymentStatus, ShowSeat, ShowSeatStatus, User
from app.services import payment as payment_service
from app.services.booking import (
    BookingEngineError,
    PaymentFailed,
    PaymentUnavailable,
    booking_transaction,
    cleanup_expired_holds,
    confirm_booking_reference,
    create_hold_for_user,
    reset_clock,
    set_clock,
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


def _parallel_run(fn, args_list):
    barrier = threading.Barrier(len(args_list))
    results = []

    def worker(args):
        barrier.wait()
        try:
            results.append(("ok", fn(*args)))
        except Exception as exc:
            results.append(("err", type(exc).__name__, str(exc)))

    with ThreadPoolExecutor(max_workers=len(args_list)) as pool:
        futures = [pool.submit(worker, args) for args in args_list]
        for future in futures:
            future.result(timeout=20)
    return results


def test_same_seat_concurrent_hold_one_success_and_one_conflict():
    with TestingSessionLocal() as db:
        show_id = seed_minimal_show(db)
        seat_id = (
            db.query(ShowSeat.seat_id)
            .filter(ShowSeat.show_id == show_id, ShowSeat.status == ShowSeatStatus.AVAILABLE.value)
            .order_by(ShowSeat.id)
            .limit(1)
            .scalar()
        )
        assert seat_id is not None

    def attempt(prefix):
        with TestingSessionLocal() as db:
            user = _make_user(db, prefix)
            return create_hold_for_user(db, user, show_id, [seat_id], "PENDING")

    results = _parallel_run(attempt, [("alice",), ("bob",)])
    assert len([r for r in results if r[0] == "ok"]) == 1
    assert len([r for r in results if r[0] == "err"]) == 1
    with TestingSessionLocal() as db:
        held_rows = db.query(ShowSeat).filter_by(show_id=show_id, seat_id=seat_id).all()
        assert len(held_rows) == 1
        assert held_rows[0].status == ShowSeatStatus.HELD.value
        assert held_rows[0].booking_id is not None
        assert db.query(Booking).filter_by(show_id=show_id, status=BookingStatus.HELD.value).count() == 1
        assert db.query(BookingSeat).filter_by(show_id=show_id, seat_id=seat_id).count() == 1


def test_different_seats_concurrent_hold_both_succeed():
    with TestingSessionLocal() as db:
        show_id = seed_minimal_show(db)
        seat_ids = [
            row.seat_id
            for row in db.query(ShowSeat)
            .filter(ShowSeat.show_id == show_id, ShowSeat.status == ShowSeatStatus.AVAILABLE.value)
            .order_by(ShowSeat.id)
            .limit(2)
        ]
        assert len(seat_ids) == 2

    def attempt(prefix, selected):
        with TestingSessionLocal() as db:
            user = _make_user(db, prefix)
            return create_hold_for_user(db, user, show_id, selected, "PENDING")

    results = _parallel_run(attempt, [("alice", [seat_ids[0]]), ("bob", [seat_ids[1]])])
    assert len([r for r in results if r[0] == "ok"]) == 2
    with TestingSessionLocal() as db:
        assert db.query(Booking).filter_by(show_id=show_id, status=BookingStatus.HELD.value).count() == 2
        assert db.query(ShowSeat).filter_by(show_id=show_id, status=ShowSeatStatus.HELD.value).count() == 2
        assert db.query(BookingSeat).filter_by(show_id=show_id).count() == 2


def test_overlapping_seats_one_transaction_wins_no_partial_commit():
    with TestingSessionLocal() as db:
        show_id = seed_minimal_show(db)
        seat_ids = [
            row.seat_id
            for row in db.query(ShowSeat)
            .filter(ShowSeat.show_id == show_id, ShowSeat.status == ShowSeatStatus.AVAILABLE.value)
            .order_by(ShowSeat.id)
            .limit(3)
        ]
        assert len(seat_ids) == 3

    def attempt(prefix, selected):
        with TestingSessionLocal() as db:
            user = _make_user(db, prefix)
            return create_hold_for_user(db, user, show_id, selected, "PENDING")

    results = _parallel_run(
        attempt,
        [("alice", [seat_ids[0], seat_ids[1]]), ("bob", [seat_ids[1], seat_ids[2]])],
    )
    assert len([r for r in results if r[0] == "ok"]) == 1
    with TestingSessionLocal() as db:
        held = db.query(ShowSeat).filter(ShowSeat.show_id == show_id, ShowSeat.status == ShowSeatStatus.HELD.value).all()
        assert len(held) == 2
        assert db.query(Booking).filter_by(show_id=show_id, status=BookingStatus.HELD.value).count() == 1
        assert db.query(BookingSeat).filter(BookingSeat.show_id == show_id).count() == 2


def test_reverse_order_same_seats_no_deadlock_and_at_most_one_wins():
    with TestingSessionLocal() as db:
        show_id = seed_minimal_show(db)
        seat_ids = [
            row.seat_id
            for row in db.query(ShowSeat)
            .filter(ShowSeat.show_id == show_id, ShowSeat.status == ShowSeatStatus.AVAILABLE.value)
            .order_by(ShowSeat.id)
            .limit(2)
        ]
        assert len(seat_ids) == 2

    def attempt(prefix, selected):
        with TestingSessionLocal() as db:
            user = _make_user(db, prefix)
            return create_hold_for_user(db, user, show_id, selected, "PENDING")

    results = _parallel_run(
        attempt,
        [("alice", [seat_ids[0], seat_ids[1]]), ("bob", [seat_ids[1], seat_ids[0]])],
    )
    assert len([r for r in results if r[0] == "ok"]) <= 1
    with TestingSessionLocal() as db:
        assert db.query(Booking).filter_by(show_id=show_id, status=BookingStatus.HELD.value).count() <= 1
        assert db.query(BookingSeat).filter_by(show_id=show_id).count() <= 2


def test_rollback_exception_after_mutation_before_commit(monkeypatch):
    # A crash *inside* the hold transaction rolls back the hold, its seats
    # and its BookingSeat rows together — verified against a fresh session.
    import app.services.booking as booking_module

    with TestingSessionLocal() as db:
        show_id = seed_minimal_show(db)
        seat_id = (
            db.query(ShowSeat.seat_id)
            .filter(ShowSeat.show_id == show_id, ShowSeat.status == ShowSeatStatus.AVAILABLE.value)
            .order_by(ShowSeat.id)
            .limit(1)
            .scalar()
        )
        assert seat_id is not None
        real_add = db.add

        def crash_on_booking_seat(obj, *args, **kwargs):
            from app.models import BookingSeat as BookingSeatModel

            if isinstance(obj, BookingSeatModel):
                raise RuntimeError("boom before commit")
            return real_add(obj, *args, **kwargs)

        monkeypatch.setattr(db, "add", crash_on_booking_seat)
        user = _make_user(db, "rollback")
        with pytest.raises(RuntimeError, match="boom before commit"):
            booking_module.create_hold_for_user(db, user, show_id, [seat_id], "PENDING")

        with TestingSessionLocal() as db2:
            assert db2.query(Booking).filter_by(show_id=show_id).count() == 0
            assert db2.query(BookingSeat).filter_by(show_id=show_id).count() == 0
            row = db2.query(ShowSeat).filter_by(show_id=show_id, seat_id=seat_id).one()
            assert row.status == ShowSeatStatus.AVAILABLE.value
            assert row.booking_id is None


def test_payment_failure_keeps_hold_in_pending_state(monkeypatch):
    with TestingSessionLocal() as db:
        show_id = seed_minimal_show(db)
        seat_ids = [
            row.seat_id
            for row in db.query(ShowSeat)
            .filter(ShowSeat.show_id == show_id, ShowSeat.status == ShowSeatStatus.AVAILABLE.value)
            .order_by(ShowSeat.id)
            .limit(2)
        ]
        user = _make_user(db, "payfail")
        booking = create_hold_for_user(db, user, show_id, seat_ids, "PENDING")

        def boom(self, amount, method):
            raise RuntimeError("gateway down")

        monkeypatch.setattr(payment_service.PaymentGateway, "charge", boom)
        # Unexpected gateway crash maps to 502 (PaymentUnavailable), never a
        # raw 500; the transaction rolls back and the hold stays HELD.
        with pytest.raises(PaymentUnavailable):
            confirm_booking_reference(db, booking.booking_reference, "Credit / Debit Card")

        row = db.query(Booking).filter_by(booking_reference=booking.booking_reference).one()
        assert row.status == BookingStatus.HELD.value
        assert row.payment_status == PaymentStatus.PENDING.value
        for ss in db.query(ShowSeat).filter(ShowSeat.show_id == show_id, ShowSeat.seat_id.in_(seat_ids)).all():
            assert ss.status == ShowSeatStatus.HELD.value
            assert ss.booking_id == row.id


def test_payment_decline_records_failed_and_never_confirms(monkeypatch):
    """Explicit gateway decline: FAILED is durable, seats stay HELD, never BOOKED."""
    from app.services.payment import PaymentResult

    with TestingSessionLocal() as db:
        show_id = seed_minimal_show(db)
        seat_ids = [
            row.seat_id
            for row in db.query(ShowSeat)
            .filter(ShowSeat.show_id == show_id, ShowSeat.status == ShowSeatStatus.AVAILABLE.value)
            .order_by(ShowSeat.id)
            .limit(2)
        ]
        user = _make_user(db, "declined")
        booking = create_hold_for_user(db, user, show_id, seat_ids, "PENDING")

        def decline(self, amount, method):
            return PaymentResult(status="FAILED", provider_reference=None)

        monkeypatch.setattr(payment_service.PaymentGateway, "charge", decline)
        with pytest.raises(PaymentFailed):
            confirm_booking_reference(db, booking.booking_reference, "Credit / Debit Card")

        db.expire_all()
        row = db.query(Booking).filter_by(booking_reference=booking.booking_reference).one()
        assert row.status == BookingStatus.HELD.value  # never CONFIRMED
        assert row.payment_status == PaymentStatus.FAILED.value  # decline is durable
        for ss in db.query(ShowSeat).filter(ShowSeat.show_id == show_id, ShowSeat.seat_id.in_(seat_ids)).all():
            assert ss.status == ShowSeatStatus.HELD.value  # never BOOKED
            assert ss.booking_id == row.id
        assert db.query(BookingSeat).filter_by(booking_id=row.id).count() == 2


def test_duplicate_confirmation_is_deterministic_conflict():
    """Confirming an already-CONFIRMED booking returns 409, never re-charges."""
    with TestingSessionLocal() as db:
        show_id = seed_minimal_show(db)
        seat_id = (
            db.query(ShowSeat.seat_id)
            .filter(ShowSeat.show_id == show_id, ShowSeat.status == ShowSeatStatus.AVAILABLE.value)
            .order_by(ShowSeat.id)
            .limit(1)
            .scalar()
        )
        user = _make_user(db, "dupconf")
        booking = create_hold_for_user(db, user, show_id, [seat_id], "PENDING")
        confirmed = confirm_booking_reference(db, booking.booking_reference, "PENDING")
        assert confirmed.status == BookingStatus.CONFIRMED.value
        total = confirmed.total_amount

        with pytest.raises(BookingEngineError):
            confirm_booking_reference(db, booking.booking_reference, "PENDING")

        db.expire_all()
        row = db.query(Booking).filter_by(booking_reference=booking.booking_reference).one()
        assert row.status == BookingStatus.CONFIRMED.value
        assert row.total_amount == total
        assert db.query(ShowSeat).filter_by(show_id=show_id, seat_id=seat_id).one().status == ShowSeatStatus.BOOKED.value


def test_expiration_cleanup_and_confirmation_race_results_in_single_terminal_outcome():
    reset_clock()
    try:
        with TestingSessionLocal() as db:
            show_id = seed_minimal_show(db)
            seat_ids = [
                row.seat_id
                for row in db.query(ShowSeat)
                .filter(ShowSeat.show_id == show_id, ShowSeat.status == ShowSeatStatus.AVAILABLE.value)
                .order_by(ShowSeat.id)
                .limit(2)
            ]
            user = _make_user(db, "expiry")
            booking = create_hold_for_user(db, user, show_id, seat_ids, "PENDING")
            reference = booking.booking_reference

            future_now = datetime.utcnow() + timedelta(minutes=20)
            set_clock(lambda: future_now)

            errors: list[BaseException] = []

            def cleanup_task():
                try:
                    with TestingSessionLocal() as local_db:
                        cleanup_expired_holds(local_db)
                except Exception as exc:  # noqa: BLE001 — collected below
                    errors.append(exc)

            def confirm_task():
                try:
                    with TestingSessionLocal() as local_db:
                        confirm_booking_reference(local_db, reference, "PENDING")
                except BookingEngineError as exc:
                    # Either terminal outcome is valid: the confirm raced an
                    # already-expired hold (409) or it slipped in first (ok).
                    # Raw gateway crashes are NOT acceptable here.
                    assert exc.status_code in (409, 402), exc
                except Exception as exc:  # noqa: BLE001 — collected below
                    errors.append(exc)

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(cleanup_task), pool.submit(confirm_task)]
                for future in futures:
                    future.result(timeout=15)

            assert not errors, errors
    finally:
        reset_clock()

    with TestingSessionLocal() as db2:
        refreshed = db2.query(Booking).filter_by(booking_reference=reference).one()
        # Deterministic: an expired hold can NEVER end CONFIRMED — the loser
        # of the race cancels (or already cancelled) and the winner releases.
        assert refreshed.status == BookingStatus.CANCELLED.value
        seats = db2.query(ShowSeat).filter(ShowSeat.show_id == show_id, ShowSeat.seat_id.in_(seat_ids)).all()
        assert all(ss.status == ShowSeatStatus.AVAILABLE.value for ss in seats)
        assert all(ss.booking_id is None for ss in seats)
