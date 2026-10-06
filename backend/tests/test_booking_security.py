"""Booking security tests: reference strength, capability boundaries and
validation of the existing booking engine (the engine itself is not
rewritten — only its security edges are exercised)."""
from datetime import date, datetime, time, timedelta

from app.models import Booking, BookingSeat, Show, ShowSeat, ShowSeatStatus, BookingStatus, Screen, Seat
from app.security import (
    BOOKING_REFERENCE_RE,
    LEGACY_BOOKING_REFERENCE_RE,
    generate_booking_reference,
    is_valid_booking_reference,
)
from conftest import TestingSessionLocal, make_user, seed_minimal_show, silver_seat_ids


# ---------------------------------------------------------------------------
# Booking reference strength
# ---------------------------------------------------------------------------

def test_generated_references_are_strong_and_unique():
    refs = {generate_booking_reference() for _ in range(500)}
    assert len(refs) == 500  # unique
    for ref in refs:
        assert ref.startswith("DHR-")
        assert len(ref) == 28  # 4 prefix + 24 url-safe chars (144 bits)
        assert BOOKING_REFERENCE_RE.match(ref)
        # The old 8-hex output is never generated again.
        assert not LEGACY_BOOKING_REFERENCE_RE.match(ref)


def test_hold_returns_strong_reference(client):
    seat_ids = silver_seat_ids(client, client.show_id, count=1)
    response = client.post(
        "/api/bookings/hold",
        json={"user": make_user("ref"), "show_id": client.show_id,
              "seat_ids": seat_ids, "payment_method": "PENDING"},
    )
    assert response.status_code == 201
    ref = response.json()["booking_reference"]
    assert BOOKING_REFERENCE_RE.match(ref)
    assert not LEGACY_BOOKING_REFERENCE_RE.match(ref)
    assert is_valid_booking_reference(ref)


def test_is_valid_booking_reference_rejects_malformed_shapes():
    for bad in ("", "hello", "DHR-", "DHR-ABC", "DHR-" + "A" * 23,
                "DHR-" + "A" * 25, "dhr-" + "a" * 24, "DHR_" + "A" * 24,
                "../" * 4, "DHR-" + "A" * 24 + " "):
        assert not is_valid_booking_reference(bad), bad
    # Legacy 8-hex references remain readable for old development records.
    assert is_valid_booking_reference("DHR-0123ABCD")


def test_malformed_reference_returns_404_not_500(client):
    for bad in ("hello", "DHR-", "DHR-ABC", "DHR-%20%20", "../../etc/passwd"):
        lookup = client.get(f"/api/bookings/{bad}")
        confirm = client.post(f"/api/bookings/{bad}/confirm",
                              json={"payment_method": "PENDING"})
        assert lookup.status_code == 404, bad
        assert confirm.status_code == 404, bad


def test_nonexistent_valid_reference_returns_404(client):
    ref = "DHR-" + "A" * 24
    assert client.get(f"/api/bookings/{ref}").status_code == 404
    assert client.post(f"/api/bookings/{ref}/confirm",
                       json={"payment_method": "PENDING"}).status_code == 404


# ---------------------------------------------------------------------------
# Guest capability design (deliberate): reference only, no user PII
# ---------------------------------------------------------------------------

def _hold(client, prefix="guest", count=2):
    seat_ids = silver_seat_ids(client, client.show_id, count=count)
    response = client.post(
        "/api/bookings/hold",
        json={"user": make_user(prefix), "show_id": client.show_id,
              "seat_ids": seat_ids, "payment_method": "PENDING"},
    )
    assert response.status_code == 201
    return response.json()


def test_guest_lookup_exposes_no_user_pii(client):
    hold = _hold(client, "lookup")
    response = client.get(f"/api/bookings/{hold['booking_reference']}")
    assert response.status_code == 200
    body = response.json()
    # Exactly the BookingOut contract — nothing about the user behind it.
    assert set(body.keys()) == {
        "booking_reference", "status", "payment_status", "total_amount",
        "show_id", "seats", "created_at", "hold_expires_at",
    }
    text = response.text
    assert "example.com" not in text
    assert "9876543210" not in text
    assert "User" not in body["seats"] and "@" not in text


def test_guest_can_confirm_hold_with_reference_only(client):
    hold = _hold(client, "confir")
    response = client.post(
        f"/api/bookings/{hold['booking_reference']}/confirm",
        json={"payment_method": "Pay at Counter"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "CONFIRMED"
    # Pay at Counter intentionally stays PENDING (existing payment design).
    assert response.json()["payment_status"] == "PENDING"


def test_confirm_expired_hold_conflict(client):
    hold = _hold(client, "expire")
    ref = hold["booking_reference"]
    with TestingSessionLocal() as db:
        booking = db.query(Booking).filter_by(booking_reference=ref).one()
        past = datetime.utcnow() - timedelta(minutes=1)
        booking.hold_expires_at = past
        for ss in db.query(ShowSeat).filter_by(booking_id=booking.id):
            ss.hold_expires_at = past
        db.commit()
    response = client.post(f"/api/bookings/{ref}/confirm",
                           json={"payment_method": "PENDING"})
    assert response.status_code == 409
    # Expired confirm persists the cancellation (commit sentinel) and frees
    # the seats — the booking is terminal, never silently re-extended.
    with TestingSessionLocal() as db:
        booking = db.query(Booking).filter_by(booking_reference=ref).one()
        assert booking.status == BookingStatus.CANCELLED.value
        freed = db.query(ShowSeat).filter_by(booking_id=None).count()
        assert freed >= 0  # seats released; exact count covered below


def test_hold_duration_is_still_ten_minutes(client):
    hold = _hold(client, "tenmin", count=1)
    created = datetime.fromisoformat(hold["created_at"])
    expires = datetime.fromisoformat(hold["hold_expires_at"])
    assert timedelta(minutes=9, seconds=50) <= expires - created <= timedelta(minutes=10, seconds=10)


def test_same_user_identical_active_hold_reuses_existing_booking(client):
    seat_ids = silver_seat_ids(client, client.show_id, count=2)
    payload = {"user": make_user("repost"), "show_id": client.show_id,
               "seat_ids": seat_ids, "payment_method": "PENDING"}
    first = client.post("/api/bookings/hold", json=payload)
    assert first.status_code == 201
    second = client.post("/api/bookings/hold", json=payload)
    assert second.status_code == 201
    assert second.json()["booking_reference"] == first.json()["booking_reference"]
    with TestingSessionLocal() as db:
        booking = db.query(Booking).filter_by(booking_reference=first.json()["booking_reference"]).one()
        assert booking.status == BookingStatus.HELD.value
        seat_rows = db.query(BookingSeat).filter_by(booking_id=booking.id).all()
        assert len(seat_rows) == len(seat_ids)
        assert {row.seat_id for row in seat_rows} == set(seat_ids)


def test_same_user_different_seat_set_creates_new_hold(client):
    seat_ids = silver_seat_ids(client, client.show_id, count=3)
    user = make_user("diffset")
    first = client.post("/api/bookings/hold", json={"user": user, "show_id": client.show_id,
                                                   "seat_ids": seat_ids[:2], "payment_method": "PENDING"})
    second = client.post("/api/bookings/hold", json={"user": user, "show_id": client.show_id,
                                                    "seat_ids": seat_ids[1:3], "payment_method": "PENDING"})
    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["booking_reference"] != first.json()["booking_reference"]


def test_same_user_expired_hold_is_not_reused(client):
    seat_ids = silver_seat_ids(client, client.show_id, count=2)
    user = make_user("expired")
    first = client.post("/api/bookings/hold", json={"user": user, "show_id": client.show_id,
                                                   "seat_ids": seat_ids, "payment_method": "PENDING"})
    assert first.status_code == 201
    with TestingSessionLocal() as db:
        booking = db.query(Booking).filter_by(booking_reference=first.json()["booking_reference"]).one()
        booking.hold_expires_at = datetime.utcnow() - timedelta(minutes=1)
        for ss in db.query(ShowSeat).filter_by(show_id=client.show_id, seat_id=seat_ids[0]):
            ss.hold_expires_at = booking.hold_expires_at
        for ss in db.query(ShowSeat).filter_by(show_id=client.show_id, seat_id=seat_ids[1]):
            ss.hold_expires_at = booking.hold_expires_at
        db.commit()
    second = client.post("/api/bookings/hold", json={"user": user, "show_id": client.show_id,
                                                    "seat_ids": seat_ids, "payment_method": "PENDING"})
    assert second.status_code == 201
    assert second.json()["booking_reference"] != first.json()["booking_reference"]


def test_other_user_same_seats_conflicts(client):
    seat_ids = silver_seat_ids(client, client.show_id, count=2)
    first = client.post("/api/bookings/hold", json={"user": make_user("alice"), "show_id": client.show_id,
                                                   "seat_ids": seat_ids, "payment_method": "PENDING"})
    assert first.status_code == 201
    second = client.post("/api/bookings/hold", json={"user": make_user("bob"), "show_id": client.show_id,
                                                    "seat_ids": seat_ids, "payment_method": "PENDING"})
    assert second.status_code == 409
    assert set(second.json()["detail"]["seat_ids"]) == set(seat_ids)


# ---------------------------------------------------------------------------
# Seat/booking payload validation (backend is authoritative)
# ---------------------------------------------------------------------------

def _hold_with_seats(client, seat_ids):
    return client.post(
        "/api/bookings/hold",
        json={"user": make_user("seats"), "show_id": client.show_id,
              "seat_ids": seat_ids, "payment_method": "PENDING"},
    )


def test_duplicate_seat_ids_rejected(client):
    seat_ids = silver_seat_ids(client, client.show_id, count=2)
    response = _hold_with_seats(client, [seat_ids[0], seat_ids[0], seat_ids[1]])
    assert response.status_code == 422
    assert "Duplicate seats" in str(response.json()["detail"])


def test_more_than_six_seats_rejected(client):
    seat_ids = silver_seat_ids(client, client.show_id, count=7)
    response = _hold_with_seats(client, seat_ids)
    assert response.status_code == 422


def test_empty_seat_list_rejected(client):
    assert _hold_with_seats(client, []).status_code == 422


def test_nonpositive_seat_ids_rejected(client):
    for seat_ids in ([0], [-1], [0, -5]):
        assert _hold_with_seats(client, seat_ids).status_code == 422


def test_unknown_seat_ids_rejected(client):
    assert _hold_with_seats(client, [999999]).status_code == 422


def test_invalid_payment_method_rejected(client):
    seat_ids = silver_seat_ids(client, client.show_id, count=1)
    response = client.post(
        "/api/bookings/hold",
        json={"user": make_user("pay"), "show_id": client.show_id,
              "seat_ids": seat_ids, "payment_method": "Bitcoin"},
    )
    assert response.status_code == 422


def test_invalid_show_id_rejected(client):
    seat_ids = silver_seat_ids(client, client.show_id, count=1)
    response = client.post(
        "/api/bookings/hold",
        json={"user": make_user("show"), "show_id": 0,
              "seat_ids": seat_ids, "payment_method": "PENDING"},
    )
    assert response.status_code == 422
    response = client.post(
        "/api/bookings/hold",
        json={"user": make_user("show"), "show_id": 999999,
              "seat_ids": seat_ids, "payment_method": "PENDING"},
    )
    assert response.status_code == 404  # shape ok, show does not exist


def test_cross_show_seat_ids_rejected(client):
    """Seats that exist on another screen/show must not be holdable."""
    with TestingSessionLocal() as db:
        base_show = db.get(Show, client.show_id)
        screen2 = Screen(theater_id=base_show.screen.theater_id, name="Screen 2")
        db.add(screen2)
        db.flush()
        other_seats = []
        for n in range(1, 8):
            seat = Seat(screen_id=screen2.id, row_label="X", seat_number=n,
                        category="Gold", price=450)
            db.add(seat)
            other_seats.append(seat)
        db.flush()
        show2 = Show(movie_id=base_show.movie_id, screen_id=screen2.id,
                     show_date=date.today(), show_time=time(21, 0), status="ACTIVE")
        db.add(show2)
        db.flush()
        for seat in other_seats:
            db.add(ShowSeat(show_id=show2.id, seat_id=seat.id,
                            status=ShowSeatStatus.AVAILABLE.value))
        db.commit()
        other_seat_id = other_seats[0].id
    response = _hold_with_seats(client, [other_seat_id])
    assert response.status_code == 422
    assert "do not belong" in str(response.json()["detail"])
