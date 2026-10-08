"""Shared PostgreSQL fixtures (Phase 9.1).

* ``pg_url`` fails fast when POSTGRES_TEST_DATABASE_URL is missing (never
  falls back to SQLite or production DATABASE_URL).
* ``pg_engine`` is a bounded-pool engine for the test database.
* ``pg_migrated`` runs ``alembic upgrade head`` from empty, then asserts
  ``current == head`` with exactly one head.
* ``pg_session`` seeds isolated rows per test and cleans up afterwards.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.config import _normalize_database_url


BACKEND_DIR = Path(__file__).resolve().parents[2]


def _require_pg_url() -> str:
    raw = (os.environ.get("POSTGRES_TEST_DATABASE_URL") or "").strip()
    if not raw:
        pytest.fail(
            "POSTGRES_TEST_DATABASE_URL is not set — refusing to run PostgreSQL "
            "tests against SQLite or production. Example: "
            "postgresql+psycopg://dhurandhar:dhurandhar-dev-pw@localhost:5432/dhurandhar_test",
            pytrace=False,
        )
    url = _normalize_database_url(raw)
    if not url.startswith("postgresql"):
        pytest.fail(
            "POSTGRES_TEST_DATABASE_URL must be a PostgreSQL URL; "
            "PostgreSQL tests never run on SQLite.",
            pytrace=False,
        )
    return url


def _alembic_config(url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture(scope="session")
def pg_url() -> str:
    return _require_pg_url()


@pytest.fixture(scope="session")
def pg_engine(pg_url: str) -> Iterator[Engine]:
    engine = create_engine(
        pg_url,
        future=True,
        pool_size=5,
        max_overflow=5,
        pool_timeout=30,
        pool_recycle=1800,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 10},
    )
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.fail(f"Cannot connect to POSTGRES_TEST_DATABASE_URL: {exc}", pytrace=False)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def pg_migrated(pg_engine: Engine, pg_url: str) -> Engine:
    """Migrate the test database from empty and verify a single head."""
    from alembic import command as alembic_command

    with pg_engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
        conn.commit()
    alembic_command.upgrade(_alembic_config(pg_url), "head")

    scripts = ScriptDirectory.from_config(_alembic_config(pg_url))
    heads = scripts.get_heads()
    assert len(heads) == 1, f"expected exactly one Alembic head, got {heads!r}"
    with pg_engine.connect() as conn:
        context = MigrationContext.configure(conn)
        current = context.get_current_heads()
    assert tuple(current) == tuple(heads), f"current {current!r} != head {heads!r}"
    return pg_engine


@pytest.fixture()
def pg_session(pg_migrated: Engine) -> Iterator[Session]:
    """Isolated data per test: committed seed rows, then full cleanup."""
    import uuid
    from datetime import date, time

    from sqlalchemy.orm import sessionmaker

    from app.models import (
        City,
        Movie,
        Screen,
        Seat,
        Show,
        ShowSeat,
        ShowSeatStatus,
        Theater,
        User,
    )
    from app.security import hash_password

    factory = sessionmaker(bind=pg_migrated, autoflush=False, expire_on_commit=False, future=True)
    db = factory()
    suffix = uuid.uuid4().hex[:8]
    try:
        movie = Movie(title=f"PG Test Movie {suffix}", metadata_json="{}")
        db.add(movie)
        db.flush()
        city = City(name=f"PG City {suffix}")
        db.add(city)
        db.flush()
        theater = Theater(city_id=city.id, name=f"PG Theater {suffix}", address="PG Addr")
        db.add(theater)
        db.flush()
        screen = Screen(theater_id=theater.id, name="PG Screen 1")
        db.add(screen)
        db.flush()
        seats = [
            Seat(screen_id=screen.id, row_label="A", seat_number=1, category="Gold", price="450.00"),
            Seat(screen_id=screen.id, row_label="A", seat_number=2, category="Gold", price="450.00"),
            Seat(screen_id=screen.id, row_label="B", seat_number=1, category="Silver", price="350.00"),
            Seat(screen_id=screen.id, row_label="C", seat_number=1, category="Bronze", price="200.00"),
        ]
        db.add_all(seats)
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
        db.add_all(
            ShowSeat(show_id=show.id, seat_id=s.id, status=ShowSeatStatus.AVAILABLE.value)
            for s in seats
        )
        user = User(
            full_name="PG User",
            email=f"pg-user-{suffix}@example.com",
            phone="9876543210",
            password_hash=hash_password("Test@1234"),
        )
        db.add(user)
        db.commit()
        db.expire_all()
        yield db
    finally:
        db.rollback()
        db.close()
        cleanup = sessionmaker(bind=pg_migrated, future=True)()
        try:
            for table in (
                "booking_seats",
                "show_seats",
                "bookings",
                "payment_webhook_events",
                "payment_attempts",
                "review_likes",
                "reviews",
                "shows",
                "seats",
                "screens",
                "theaters",
                "movies",
                "cities",
                "users",
                "newsletter_subscribers",
                "contact_messages",
                "admin_audit_logs",
            ):
                cleanup.execute(text(f'DELETE FROM "{table}"'))
            cleanup.commit()
        finally:
            cleanup.close()

