"""Phase 1 baseline: in-process FastAPI tests with an isolated SQLite database.

No manually running Uvicorn server is required. The suite never touches the
developer's ``backend/dhurandhar.db``: ``DATABASE_URL``/``JWT_SECRET`` are set
before ``app`` is imported, and every test gets a freshly created schema with
a minimal seeded show.
"""
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
os.environ["JWT_SECRET"] = "phase1-test-secret-that-is-long-enough-for-hs256"
os.environ["PAYMENT_MODE"] = "mock"

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

engine = create_engine(
    os.environ["DATABASE_URL"],
    connect_args={"check_same_thread": False},
    future=True,
)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def _seed_minimal_show(db):
    """One city/theater/screen/show with 80 seats: 20 Gold / 30 Silver / 30 Bronze."""
    movie = Movie(title="Dhurandhar Test", metadata_json="{}")
    db.add(movie)
    db.flush()
    city = City(name="Test City")
    db.add(city)
    db.flush()
    theater = Theater(city_id=city.id, name="Test Theater", address="Test Address")
    db.add(theater)
    db.flush()
    screen = Screen(theater_id=theater.id, name="Screen 1")
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


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        show_id = _seed_minimal_show(db)
    finally:
        db.close()

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as test_client:
            test_client.show_id = show_id
            yield test_client
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _user(prefix="user"):
    suffix = uuid.uuid4().hex[:8]
    return {
        "full_name": f"{prefix.title()} User",
        "email": f"{prefix}-{suffix}@example.com",
        "phone": "9876543210",
        "password": "Test@1234",
    }


def _silver_seat_ids(client, show_id, count=2):
    seats = client.get(f"/api/shows/{show_id}/seats").json()
    selected = [s for s in seats if s["status"] == "AVAILABLE" and s["category"] == "Silver"][:count]
    assert len(selected) == count
    return [s["id"] for s in selected]


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_catalog_retrieval(client):
    cities = client.get("/api/cities").json()
    assert len(cities) == 1
    theaters = client.get("/api/theaters", params={"city_id": cities[0]["id"]}).json()
    assert len(theaters) == 1
    shows = client.get(
        "/api/shows",
        params={"theater_id": theaters[0]["id"], "date": date.today().isoformat()},
    ).json()
    assert len(shows) == 1
    seats = client.get(f"/api/shows/{shows[0]['id']}/seats").json()
    assert len(seats) == 80


def test_registration(client):
    user = _user("register")
    response = client.post("/api/auth/register", json=user)
    assert response.status_code == 201
    body = response.json()
    assert body["user"]["email"] == user["email"]
    assert body["access_token"]


def test_login(client):
    user = _user("login")
    assert client.post("/api/auth/register", json=user).status_code == 201
    response = client.post(
        "/api/auth/login", json={"email": user["email"], "password": user["password"]}
    )
    assert response.status_code == 200
    assert response.json()["access_token"]


def test_invalid_login(client):
    user = _user("badlogin")
    assert client.post("/api/auth/register", json=user).status_code == 201
    response = client.post(
        "/api/auth/login", json={"email": user["email"], "password": "wrong-password"}
    )
    assert response.status_code == 401


def test_booking_hold(client):
    show_id = client.show_id
    seat_ids = _silver_seat_ids(client, show_id)
    response = client.post(
        "/api/bookings/hold",
        json={
            "user": _user("hold"),
            "show_id": show_id,
            "seat_ids": seat_ids,
            "payment_method": "UPI / Google Pay / PhonePe",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["show_id"] == show_id
    assert body["status"] == "HELD"


def test_authoritative_booking_price(client):
    show_id = client.show_id
    seat_ids = _silver_seat_ids(client, show_id)
    response = client.post(
        "/api/bookings/hold",
        json={
            "user": _user("price"),
            "show_id": show_id,
            "seat_ids": seat_ids,
            "payment_method": "UPI / Google Pay / PhonePe",
        },
    )
    assert response.status_code == 201
    # Backend prices Silver seats at 350 each; client totals are ignored.
    assert float(response.json()["total_amount"]) == 700


def test_double_booking_conflict(client):
    show_id = client.show_id
    seat_ids = _silver_seat_ids(client, show_id)
    first = client.post(
        "/api/bookings/hold",
        json={
            "user": _user("first"),
            "show_id": show_id,
            "seat_ids": seat_ids,
            "payment_method": "PENDING",
        },
    )
    assert first.status_code == 201
    reference = first.json()["booking_reference"]
    second = client.post(
        "/api/bookings/hold",
        json={
            "user": _user("second"),
            "show_id": show_id,
            "seat_ids": seat_ids,
            "payment_method": "PENDING",
        },
    )
    assert second.status_code == 409
    confirmed = client.post(
        f"/api/bookings/{reference}/confirm",
        json={"payment_method": "UPI / Google Pay / PhonePe"},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "CONFIRMED"
    assert confirmed.json()["payment_status"] == "PAID"
    third = client.post(
        "/api/bookings/hold",
        json={
            "user": _user("third"),
            "show_id": show_id,
            "seat_ids": seat_ids,
            "payment_method": "PENDING",
        },
    )
    assert third.status_code == 409


def test_invalid_seat_handling(client):
    response = client.post(
        "/api/bookings/hold",
        json={
            "user": _user("invalid"),
            "show_id": client.show_id,
            "seat_ids": [999999],
            "payment_method": "PENDING",
        },
    )
    assert response.status_code == 422

