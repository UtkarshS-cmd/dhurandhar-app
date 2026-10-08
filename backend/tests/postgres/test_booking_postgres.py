"""PostgreSQL booking concurrency — REAL concurrent transactions, never SQLite."""
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.models import Booking, BookingSeat, BookingStatus, Show, ShowSeat, ShowSeatStatus, User
from app.services.booking import BookingConflict, BookingEngineError, create_hold_for_user
from app.security import hash_password


def _parallel_run(fn, args_list, timeout=60):
    barrier = threading.Barrier(len(args_list))
    results = []

    def worker(args):
        barrier.wait()
        try:
            results.append(("ok", fn(*args)))
        except Exception as exc:  # noqa: BLE001 — collected and asserted below
            results.append(("err", type(exc).__name__, str(exc)))

    with ThreadPoolExecutor(max_workers=len(args_list)) as pool:
        futures = [pool.submit(worker, args) for args in args_list]
        for future in futures:
            future.result(timeout=timeout)
    return results


def _hold(pg_migrated, show_id, seat_ids, prefix):
    factory = sessionmaker(bind=pg_migrated, autoflush=False, expire_on_commit=False, future=True)
    db = factory()
    try:
        user = User(
            full_name=f"{prefix.title()} User",
            email=f"{prefix}-{uuid.uuid4().hex[:12]}@example.com",
            phone="9876543210",
            password_hash=hash_password("Test@1234"),
        )
        db.add(user)
        db.flush()
        booking = create_hold_for_user(db, user, show_id, list(seat_ids), "PENDING")
        return booking.booking_reference
    finally:
        db.close()


def _seat_ids(pg_session, count):
    show_id = pg_session.scalar(select(Show.id))
    rows = (
        pg_session.execute(
            select(ShowSeat.seat_id)
            .where(ShowSeat.show_id == show_id, ShowSeat.status == ShowSeatStatus.AVAILABLE.value)
            .order_by(ShowSeat.id)
            .limit(count)
        )
        .scalars()
        .all()
    )
    assert len(rows) == count
    return show_id, list(rows)


def test_pg_same_seat_exactly_one_succeeds(pg_session, pg_migrated):
    show_id, seats = _seat_ids(pg_session, 1)
    results = _parallel_run(_hold, [(pg_migrated, show_id, seats, "alice"), (pg_migrated, show_id, seats, "bob")])
    assert len([r for r in results if r[0] == "ok"]) == 1
    assert len([r for r in results if r[0] == "err"]) == 1


def test_pg_different_seats_both_succeed(pg_session, pg_migrated):
    show_id, seats = _seat_ids(pg_session, 2)
    results = _parallel_run(
        _hold,
        [(pg_migrated, show_id, [seats[0]], "alice"), (pg_migrated, show_id, [seats[1]], "bob")],
    )
    assert len([r for r in results if r[0] == "ok"]) == 2


def test_pg_overlapping_sets_no_partial_booking(pg_session, pg_migrated):
    show_id, seats = _seat_ids(pg_session, 3)
    results = _parallel_run(
        _hold,
        [
            (pg_migrated, show_id, seats[:2], "alice"),
            (pg_migrated, show_id, seats[1:], "bob"),
        ],
    )
    assert len([r for r in results if r[0] == "ok"]) == 1
    assert len([r for r in results if r[0] == "err"]) == 1
    # Loser left nothing behind: every BookingSeat belongs to a HELD booking.
    factory = sessionmaker(bind=pg_migrated, future=True)
    db = factory()
    try:
        orphans = (
            db.execute(
                select(BookingSeat.id)
                .select_from(BookingSeat)
                .join(Booking, Booking.id == BookingSeat.booking_id)
                .where(Booking.status != BookingStatus.HELD.value)
            )
            .scalars()
            .all()
        )
        assert orphans == []
    finally:
        db.close()


def test_pg_reverse_order_no_deadlock(pg_session, pg_migrated):
    """A+B vs B+A: ORDER BY show_seats.id must prevent deadlocks."""
    show_id, seats = _seat_ids(pg_session, 2)
    results = _parallel_run(
        _hold,
        [
            (pg_migrated, show_id, [seats[0], seats[1]], "alice"),
            (pg_migrated, show_id, [seats[1], seats[0]], "bob"),
        ],
        timeout=90,
    )
    oks = [r for r in results if r[0] == "ok"]
    errs = [r for r in results if r[0] == "err"]
    assert len(oks) == 1 and len(errs) == 1
    assert "eadlock" not in errs[0][2]
    assert errs[0][1] in {"SeatConflict", "BookingConflict", "BookingEngineError", "OperationalError"}
