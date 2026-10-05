"""Phase 1 baseline: in-process FastAPI tests with an isolated SQLite database.

Environment setup (temp database, JWT secret, seeded show, ``client``
fixture and rate-limiter reset) lives in ``conftest.py`` so this suite and
the Phase 2 security suites share exactly the same isolation. No manually
running Uvicorn server is required and the developer's
``backend/dhurandhar.db`` is never touched.
"""
from datetime import date

from conftest import make_user as _user, silver_seat_ids as _silver_seat_ids  # noqa: F401


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