"""Shared test environment for the baseline and Phase 2 security suites.

pytest imports this file *before* any test module, so the environment set
here is in place before ``app`` is imported:

* an isolated temporary SQLite database (never ``backend/dhurandhar.db``),
* a fixed, strong ``JWT_SECRET`` so token behaviour is deterministic,
* ``APP_ENV=development`` and mock payment mode.

Every test gets a freshly created schema with a minimal seeded show via the
``client`` fixture, which also resets the in-process rate limiter so
rate-limit tests stay deterministic and independent of each other.
"""
import atexit
import os
import sys
import tempfile
import uuid
from datetime import date, time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

_TMPDIR = tempfile.TemporaryDirectory(prefix="dhurandhar-test-")
_TEST_DB_PATH = Path(_TMPDIR.name, "test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH.as_posix()}"
os.environ["APP_ENV"] = "development"
os.environ["JWT_SECRET"] = "phase2-test-secret-that-is-long-enough-for-hs256"
os.environ["PAYMENT_MODE"] = "mock"
os.environ["CORS_ORIGINS"] = "http://localhost:5000,http://127.0.0.1:5000"

from app.db import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    City,
    Movie,
    Screen,
    Seat,
    Show,
    ShowSeat,
    ShowSeatStatus,
    Theater,
)
from app.ratelimit import limiter  # noqa: E402
from app.services.booking import reset_clock  # noqa: E402

engine = create_engine(
    os.environ["DATABASE_URL"],
    connect_args={"check_same_thread": False},
    future=True,
)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
Base.metadata.create_all(bind=engine)
# Release the connection create_all used immediately: the client fixture
# disposes at teardown, but a selection that never instantiates ``client``
# (e.g. the standalone Alembic tests) would otherwise leave the pooled
# connection open and Windows could not delete the temp dir at exit.
engine.dispose()
# Some suites open connections without the client fixture — including the
# app's own engine via the default get_db — so idle pooled connections can
# still exist after the last client teardown. Windows then refuses to delete
# the temp dir at interpreter exit (PermissionError → pytest exit code 1).
# atexit callbacks run before the tempfile finalizer, so disposing every
# engine bound to the temp DB here guarantees the file is released.
def _exit_dispose_pools():
    from app.db import engine as app_engine

    engine.dispose()
    app_engine.dispose()

atexit.register(_exit_dispose_pools)


def seed_minimal_show(db):
    """One city/theater/screen/show with 80 seats: 20 Gold / 30 Silver / 30 Bronze."""
    suffix = uuid.uuid4().hex[:8]
    movie = Movie(title=f"Dhurandhar Test {suffix}", metadata_json="{}")
    db.add(movie)
    db.flush()
    city = City(name=f"Test City {suffix}")
    db.add(city)
    db.flush()
    theater = Theater(city_id=city.id, name=f"Test Theater {suffix}", address=f"Test Address {suffix}")
    db.add(theater)
    db.flush()
    screen = Screen(theater_id=theater.id, name=f"Screen {suffix}")
    db.add(screen)
    db.flush()
    seat_rows = []
    layout = [("G", 1, 20, "Gold", 450), ("S", 21, 30, "Silver", 350), ("B", 51, 30, "Bronze", 200)]
    for row_label, start, count, category, price in layout:
        for n in range(start, start + count):
            seat = Seat(
                screen_id=screen.id,
                row_label=row_label,
                seat_number=n,
                category=category,
                price=price,
            )
            db.add(seat)
            seat_rows.append(seat)
    db.flush()
    show = Show(
        movie_id=movie.id,
        screen_id=screen.id,
        show_date=date.today(),
        show_time=time(18, 30),
        status="ACTIVE",
    )
    db.add(show)
    db.flush()
    for seat in seat_rows:
        db.add(ShowSeat(show_id=show.id, seat_id=seat.id, status=ShowSeatStatus.AVAILABLE.value))
    db.commit()
    return show.id


def make_user(prefix="user"):
    """A unique, schema-valid user payload."""
    suffix = uuid.uuid4().hex[:8]
    return {
        "full_name": f"{prefix.title()} User",
        "email": f"{prefix}-{suffix}@example.com",
        "phone": "9876543210",
        "password": "Test@1234",
    }


def silver_seat_ids(client, show_id, count=2):
    """First ``count`` available Silver seat ids for the given show."""
    seats = client.get(f"/api/shows/{show_id}/seats").json()
    selected = [s for s in seats if s["status"] == "AVAILABLE" and s["category"] == "Silver"][:count]
    assert len(selected) == count
    return [s["id"] for s in selected]


@pytest.fixture(autouse=True)
def _isolate_booking_clock():
    """A test that injects a mock clock must never leak it into others."""
    reset_clock()
    try:
        yield
    finally:
        reset_clock()


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        show_id = seed_minimal_show(db)
        # Resolve movie_id from the seeded show so review tests use the correct ID.
        show = db.get(Show, show_id)
        movie_id = show.movie_id
    finally:
        db.close()

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    limiter.reset()
    try:
        with TestClient(app) as test_client:
            test_client.show_id = show_id
            test_client.movie_id = movie_id
            yield test_client
    finally:
        app.dependency_overrides.clear()
        engine.dispose()