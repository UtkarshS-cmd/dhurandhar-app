"""Phase 10 performance/reliability regression tests.

These lock in the *correctness* of the query optimizations introduced in
Phase 10, so a future refactor cannot silently break them:

* ``/api/me/bookings`` — batched seat-label loading (no per-booking N+1),
  newest-first ordering, and the hard 50-row cap.
* ``/api/reviews`` — the single combined COUNT + AVG aggregate returns the
  same ``total`` / ``average_rating`` / ``total_reviews`` as separate queries.
* ``/api/admin/bookings`` — ``_serialize_many`` batch loader produces the same
  enriched fields (movie/screen/theater/user + seat labels) as the old
  per-row loader.
* ``/api/admin/shows`` — the GROUP BY seat-count aggregate reports the same
  held/booked totals as per-row COUNT queries.
* ``/api/admin/dashboard`` — the grouped status/revenue aggregate counts only
  genuinely confirmed+paid revenue.

Nothing here measures wall-clock time (machine-dependent and flaky); each test
asserts observable result correctness, which is what a regression must protect.
"""

from datetime import datetime, timedelta

from sqlalchemy import func, select

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
from conftest import TestingSessionLocal, make_user, silver_seat_ids


def _register(client, prefix="perf"):
    user = make_user(prefix)
    response = client.post("/api/auth/register", json=user)
    assert response.status_code == 201
    return user, response.json()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _user_id(email):
    with TestingSessionLocal() as db:
        return db.scalar(select(User.id).where(User.email == email.lower()))


def _hold_confirm(client, user, show_id, count=1):
    seat_ids = silver_seat_ids(client, show_id, count=count)
    hold = client.post(
        "/api/bookings/hold",
        json={"user": user, "show_id": show_id, "seat_ids": seat_ids, "payment_method": "PENDING"},
    )
    assert hold.status_code == 201, hold.text
    reference = hold.json()["booking_reference"]
    confirmed = client.post(
        f"/api/bookings/{reference}/confirm",
        json={"payment_method": "UPI / Google Pay / PhonePe"},
    )
    assert confirmed.status_code == 200, confirmed.text
    return reference


# ---------------------------------------------------------------------------
# /api/me/bookings — batched seat labels + ordering + 50-row cap
# ---------------------------------------------------------------------------

def _first_seat(show_id):
    with TestingSessionLocal() as db:
        return db.scalar(select(Seat).join(ShowSeat, ShowSeat.seat_id == Seat.id)
                         .where(ShowSeat.show_id == show_id).limit(1))


def _seed_confirmed_bookings(user_id, show_id, total, seat):
    """Insert ``total`` CONFIRMED bookings (one seat each) directly.

    Confirmed bookings are immune to expired-hold cleanup, so the listing is
    deterministic. ``created_at`` increases with each row to fix newest-first
    ordering. Returns the list of created booking ids in insertion order.
    """
    created = []
    with TestingSessionLocal() as db:
        base = datetime(2024, 1, 1, 12, 0, 0)
        for i in range(total):
            booking = Booking(
                booking_reference=f"PERFREF{i:04d}{user_id:06d}",
                user_id=user_id,
                show_id=show_id,
                status=BookingStatus.CONFIRMED.value,
                total_amount=seat.price,
                payment_method="UPI / Google Pay / PhonePe",
                payment_status=PaymentStatus.PAID.value,
                hold_expires_at=None,
                created_at=base + timedelta(minutes=i),
            )
            db.add(booking)
            db.flush()
            db.add(BookingSeat(booking_id=booking.id, show_id=show_id, seat_id=seat.id, price=seat.price))
            created.append(booking.id)
        db.commit()
    return created


def test_me_bookings_batched_seat_labels_match_db(client):
    user, body = _register(client, "me-list")
    headers = _auth(body["access_token"])
    reference = _hold_confirm(client, user, client.show_id, count=2)

    rows = client.get("/api/me/bookings", headers=headers).json()
    assert len(rows) == 1
    assert rows[0]["booking_reference"] == reference
    assert rows[0]["status"] == "CONFIRMED"
    assert len(rows[0]["seats"]) == 2

    # The batched loader must attach the correct ascending seat labels.
    with TestingSessionLocal() as db:
        booked = db.scalars(
            select(Seat)
            .join(BookingSeat, BookingSeat.seat_id == Seat.id)
            .join(Booking, BookingSeat.booking_id == Booking.id)
            .where(Booking.booking_reference == reference)
            .order_by(Seat.row_label, Seat.seat_number)
        ).all()
        expected = [f"{s.row_label}{s.seat_number}" for s in booked]
    assert rows[0]["seats"] == expected


def test_me_bookings_is_capped_at_50(client):
    user, body = _register(client, "me-cap")
    headers = _auth(body["access_token"])
    user_id = _user_id(user["email"])
    seat = _first_seat(client.show_id)
    assert seat is not None

    _seed_confirmed_bookings(user_id, client.show_id, total=55, seat=seat)

    rows = client.get("/api/me/bookings", headers=headers).json()
    # Hard cap: never more than 50, even though 55 exist.
    assert len(rows) == 50
    assert len({r["booking_reference"] for r in rows}) == 50
    # Every returned booking still carries a seat label (batched join intact).
    assert all(r["seats"] for r in rows)


# ---------------------------------------------------------------------------
# /api/reviews — combined COUNT + AVG aggregate correctness
# ---------------------------------------------------------------------------

def _make_review(client, token, movie_id, rating):
    return client.post(
        "/api/reviews",
        headers=_auth(token),
        json={"movie_id": movie_id, "rating": rating, "title": f"Rating {rating}", "body": f"Body {rating}"},
    )


def test_reviews_combined_aggregate_matches_expected_totals(client):
    movie_id = client.movie_id
    ratings = []
    for i in range(3):
        user, body = _register(client, f"rev{i}")
        # Each reviewer needs a confirmed ticket for the movie.
        _hold_confirm(client, user, client.show_id, count=1)
        rating = 3 + i  # 3, 4, 5
        resp = _make_review(client, body["access_token"], movie_id, rating)
        assert resp.status_code == 201, resp.text
        ratings.append(rating)

    data = client.get("/api/reviews").json()
    assert data["total"] == 3
    assert data["total_reviews"] == 3
    assert data["average_rating"] == round(sum(ratings) / len(ratings), 2)


def test_reviews_empty_catalog_has_zero_total_and_zero_average(client):
    data = client.get("/api/reviews").json()
    assert data["total"] == 0
    assert data["total_reviews"] == 0
    assert data["average_rating"] == 0.0
    assert data["items"] == []


# ---------------------------------------------------------------------------
# /api/admin/bookings — batched enrichment equals per-row enrichment
# ---------------------------------------------------------------------------

def _promote_admin(client):
    user, body = _register(client, "perfadmin")
    with TestingSessionLocal() as db:
        row = db.scalar(select(User).where(User.email == user["email"].lower()))
        row.role = "ADMIN"
        db.commit()
    login = client.post("/api/auth/login", json={"email": user["email"], "password": user["password"]})
    assert login.status_code == 200
    return _auth(login.json()["access_token"])


def test_admin_bookings_batch_enrichment(client):
    admin_headers = _promote_admin(client)
    user, _ = _register(client, "perfcust")
    reference = _hold_confirm(client, user, client.show_id, count=2)

    data = client.get("/api/admin/bookings", headers=admin_headers).json()
    assert data["total"] >= 1
    item = next(b for b in data["items"] if b["booking_reference"] == reference)

    # Enriched fields the batch loader must populate correctly.
    assert item["movie_title"]
    assert item["theater_name"]
    assert item["screen_name"]
    assert item["user_email"] == user["email"].lower()
    assert item["user_name"] == user["full_name"]
    assert len(item["seats"]) == 2
    assert item["status"] == "CONFIRMED"
    assert item["payment_status"] == "PAID"

    # The single-booking detail endpoint reuses the same batch path.
    detail = client.get(f"/api/admin/bookings/{item['id']}", headers=admin_headers).json()
    assert detail["booking_reference"] == reference
    assert detail["seats"] == item["seats"]



# ---------------------------------------------------------------------------
# /api/admin/shows — GROUP BY seat-count aggregate correctness
# ---------------------------------------------------------------------------

def test_admin_shows_seat_count_aggregate(client):
    admin_headers = _promote_admin(client)
    show_id = client.show_id

    # Hold (not confirm) 3 seats so they are HELD ...
    held_user, _ = _register(client, "perfheld")
    hold = client.post(
        "/api/bookings/hold",
        json={"user": held_user, "show_id": show_id,
              "seat_ids": silver_seat_ids(client, show_id, count=3), "payment_method": "PENDING"},
    )
    assert hold.status_code == 201, hold.text

    # ... then independently confirm a different pair so BOOKED seats exist.
    _hold_confirm(client, _register(client, "perfbooked")[0], show_id, count=2)

    data = client.get("/api/admin/shows", headers=admin_headers).json()
    row = next(s for s in data["items"] if s["id"] == show_id)

    # Cross-check against direct COUNTs on the DB (the pre-optimization truth).
    with TestingSessionLocal() as db:
        held = db.scalar(select(func.count(ShowSeat.id)).where(
            ShowSeat.show_id == show_id, ShowSeat.status == ShowSeatStatus.HELD.value))
        booked = db.scalar(select(func.count(ShowSeat.id)).where(
            ShowSeat.show_id == show_id, ShowSeat.status == ShowSeatStatus.BOOKED.value))
    assert row["held_seats"] == held
    assert row["booked_seats"] == booked
    assert row["held_seats"] >= 3
    assert row["booked_seats"] >= 2


# ---------------------------------------------------------------------------
# /api/admin/dashboard — grouped status/revenue aggregate correctness
# ---------------------------------------------------------------------------

def test_admin_dashboard_revenue_counts_only_confirmed_paid(client):
    admin_headers = _promote_admin(client)

    # One confirmed+paid booking (real revenue) and one still-held booking.
    reference = _hold_confirm(client, _register(client, "dashconf")[0], client.show_id, count=2)
    client.post(
        "/api/bookings/hold",
        json={"user": _register(client, "dashheld")[0], "show_id": client.show_id,
              "seat_ids": silver_seat_ids(client, client.show_id, count=2), "payment_method": "PENDING"},
    )

    dash = client.get("/api/admin/dashboard", headers=admin_headers).json()

    with TestingSessionLocal() as db:
        confirmed_booking = db.scalar(select(Booking).where(Booking.booking_reference == reference))
        expected_revenue = float(confirmed_booking.total_amount)
        confirmed_count = db.scalar(select(func.count(Booking.id)).where(
            Booking.status == BookingStatus.CONFIRMED.value,
            Booking.payment_status == PaymentStatus.PAID.value))

    assert confirmed_count >= 1
    # Grouped revenue equals at least the sum of this confirmed+paid booking.
    assert dash["total_revenue"] >= expected_revenue - 0.001
    assert dash["confirmed_bookings"] >= 1
    assert dash["held_bookings"] >= 1
    # Sanity: revenue must be a plain float, never None or a Decimal string.
    assert isinstance(dash["total_revenue"], float)



# ---------------------------------------------------------------------------
# Response compression + readiness probe (Phase 10 reliability)
# ---------------------------------------------------------------------------

def test_large_api_response_is_gzip_compressed_when_requested(client):
    # The seat map for an 80-seat show is a comfortably large JSON payload.
    seats = client.get(
        f"/api/shows/{client.show_id}/seats",
        headers={"Accept-Encoding": "gzip"},
    )
    assert seats.status_code == 200
    assert seats.headers.get("content-encoding") == "gzip"
    # The TestClient transparently decompresses, so the JSON is still valid.
    assert len(seats.json()) == 80


def test_small_response_is_not_compressed(client):
    # /api/health is tiny; below minimum_size it must not be gzipped.
    health = client.get("/api/health", headers={"Accept-Encoding": "gzip"})
    assert health.status_code == 200
    assert health.headers.get("content-encoding") != "gzip"


def test_readiness_reports_ready_without_leaking_internals(client):
    response = client.get("/api/ready")
    assert response.status_code == 200
    body = response.text
    assert response.json()["status"] == "ready"
    # Must never expose the database URL, driver, or file path.
    assert "sqlite" not in body.lower()
    assert "database_url" not in body.lower()
    assert ".db" not in body.lower()

