"""Phase 7 review-feature tests.

Every test runs against an isolated SQLite database created by the ``client``
fixture (same isolation as the other suites), so the developer's
``backend/dhurandhar.db`` is never touched. The suite covers:

* auth (``401`` for unauthenticated create/update/delete/like)
* verified booking, including the "only confirmed ticket holders" gate and the
  case where a confirmed booking is for a *different* movie
* ownership / IDOR (``403`` on another user's review, unknown-field rejection)
* duplicate reviews and the ``uq_review_user_movie`` unique constraint
* request validation (rating range, empty / oversized strings, missing fields)
* likes (single toggle, second like disables, duplicate likes do not corrupt the
  count, concurrent-insert race recovers consistently, unexpected DB errors are
  never masked as a successful like, nonexistent review)
* server-driven listing (page / limit / max limit, five sorts, invalid sort,
  total / has_more / average_rating / total_reviews)
* PII leakage and malicious content returned as plain text
* owner identity contract: list/detail payloads carry ``user_id`` (the field
  the frontend owner controls render against) with no other user PII, and
  ``movie_id`` is a required, never-defaulted part of review creation
* rate limiting on review create / update / delete / like (429 + Retry-After)
* real Alembic runs: NOT NULL ``movie_id`` + FK + unique + indexes, valid
  downgrade round trip, deterministic legacy backfill, and a loud failure
  when a legacy review has no resolvable movie

Backend authorization is authoritative: the frontend never trusts
client-supplied ownership flags or user_id overrides.
"""

from datetime import date, time

import dataclasses
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig

import app.config as app_config
from app.models import (
    Booking, BookingSeat, Movie, ReviewLike, Show, ShowSeat, ShowSeatStatus, User,
)
from conftest import make_user as _user, silver_seat_ids as _silver_seat_ids
from conftest import TestingSessionLocal, seed_minimal_show

BACKEND_DIR = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _register(client, prefix="review"):
    user = _user(prefix)
    response = client.post("/api/auth/register", json=user)
    assert response.status_code == 201
    return user, response.json()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _hold_confirm(client, user, show_id, count=2, payment_method="PENDING"):
    """Holds seats, confirms the booking, returns the booking reference."""
    seat_ids = _silver_seat_ids(client, show_id, count=count)
    response = client.post(
        "/api/bookings/hold",
        json={"user": user, "show_id": show_id, "seat_ids": seat_ids, "payment_method": payment_method},
    )
    assert response.status_code == 201, response.text
    reference = response.json()["booking_reference"]
    confirmed = client.post(
        f"/api/bookings/{reference}/confirm",
        json={"payment_method": "UPI / Google Pay / PhonePe"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "CONFIRMED"
    return reference


def _make_review(client, user, token, movie_id, rating, title, body, spoiler=False):
    return client.post(
        "/api/reviews",
        headers=_auth(token),
        json={"movie_id": movie_id, "rating": rating, "title": title, "body": body, "spoiler": spoiler},
    )


def _create_second_movie_and_show(db, show_id, movie_title="Second Movie"):
    """Create a second movie + show on the same screen (its own seats).
    Returns (movie_id, new_show_id).
    """
    from app.models import Seat

    show = db.get(Show, show_id)
    screen = show.screen
    movie = Movie(title=movie_title, metadata_json="{}")
    db.add(movie)
    db.flush()
    new_show = Show(
        movie_id=movie.id,
        screen_id=screen.id,
        show_date=date.today(),
        show_time=time(20, 30),
        status="ACTIVE",
    )
    db.add(new_show)
    db.flush()
    new_seats = []
    for n in range(1, 6):
        seat = Seat(screen_id=screen.id, row_label="B", seat_number=n, category="Silver", price=350)
        db.add(seat)
        new_seats.append(seat)
    db.flush()
    for seat in new_seats:
        db.add(ShowSeat(show_id=new_show.id, seat_id=seat.id, status="AVAILABLE"))
    db.commit()
    return movie.id, new_show.id


def _book_second_movie(client, user, show_id, count=2):
    """Create a second movie + show, book and confirm a seat in it.
    Returns (movie_id, new_show_id).
    """
    with TestingSessionLocal() as db:
        movie_id, new_show_id = _create_second_movie_and_show(db, show_id)
    seat_ids = _silver_seat_ids(client, new_show_id, count=count)
    response = client.post(
        "/api/bookings/hold",
        json={"user": user, "show_id": new_show_id, "seat_ids": seat_ids, "payment_method": "PENDING"},
    )
    assert response.status_code == 201, response.text
    reference = response.json()["booking_reference"]
    confirmed = client.post(
        f"/api/bookings/{reference}/confirm",
        json={"payment_method": "UPI / Google Pay / PhonePe"},
    )
    assert confirmed.status_code == 200, confirmed.text
    return movie_id, new_show_id


# ---------------------------------------------------------------------------
# 1. Authentication
# ---------------------------------------------------------------------------

def test_create_review_requires_authentication(client):
    response = client.post("/api/reviews", json={"movie_id": 1, "rating": 5, "title": "x", "body": "y"})
    assert response.status_code == 401


def test_update_review_requires_authentication(client):
    response = client.patch("/api/reviews/1", json={"title": "x"})
    assert response.status_code == 401


def test_delete_review_requires_authentication(client):
    response = client.delete("/api/reviews/1")
    assert response.status_code == 401


def test_like_review_requires_authentication(client):
    response = client.post("/api/reviews/1/like")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# 2. Verified booking
# ---------------------------------------------------------------------------

def test_confirmed_ticket_holder_can_review(client):
    user, body = _register(client, "verified")
    token = body["access_token"]
    _hold_confirm(client, user, client.show_id)
    response = _make_review(client, user, token, client.movie_id, 8, "Great film", "Absolutely loved it.")
    assert response.status_code == 201
    assert response.json()["message"] == "Review created"


def test_held_booking_cannot_review(client):
    user, body = _register(client, "held")
    token = body["access_token"]
    seat_ids = _silver_seat_ids(client, client.show_id)
    client.post(
        "/api/bookings/hold",
        json={"user": user, "show_id": client.show_id, "seat_ids": seat_ids, "payment_method": "PENDING"},
    )
    response = _make_review(client, user, token, client.movie_id, 8, "x", "y")
    assert response.status_code == 403
    assert "confirmed" in response.json()["detail"].lower()


def test_no_booking_cannot_review(client):
    user, body = _register(client, "nobook")
    token = body["access_token"]
    response = _make_review(client, user, token, client.movie_id, 8, "x", "y")
    assert response.status_code == 403


def test_booking_for_different_movie_cannot_review(client):
    show_id = client.show_id
    movie_id = client.movie_id

    user_a, body_a = _register(client, "moviea")
    token_a = body_a["access_token"]
    _hold_confirm(client, user_a, show_id)
    r_a = _make_review(client, user_a, token_a, movie_id, 7, "Movie A review", "Body A")
    assert r_a.status_code == 201

    user_b, body_b = _register(client, "movieb")
    token_b = body_b["access_token"]
    movie_id_b, show_id_b = _book_second_movie(client, user_b, show_id)
    r_b = _make_review(client, user_b, token_b, movie_id_b, 9, "Movie B review", "Body B")
    assert r_b.status_code == 201

    # user_a (confirmed for movie A) tries to review movie B → 403
    blocked_a = client.post(
        "/api/reviews",
        headers=_auth(token_a),
        json={"movie_id": movie_id_b, "rating": 7, "title": "x", "body": "y"},
    )
    # user_b (confirmed for movie B) tries to review movie A → 403
    blocked_b = client.post(
        "/api/reviews",
        headers=_auth(token_b),
        json={"movie_id": movie_id, "rating": 7, "title": "x", "body": "y"},
    )
    assert blocked_a.status_code == 403
    assert blocked_b.status_code == 403


# ---------------------------------------------------------------------------
# 3. Ownership / IDOR
# ---------------------------------------------------------------------------

def test_owner_can_update_review(client):
    user, body = _register(client, "owner")
    token = body["access_token"]
    _hold_confirm(client, user, client.show_id)
    review = _make_review(client, user, token, client.movie_id, 5, "Old title", "Old body")
    assert review.status_code == 201, review.text
    review_id = review.json()["id"]

    response = client.patch(
        f"/api/reviews/{review_id}",
        headers=_auth(token),
        json={"rating": 9, "title": "New title", "body": "New body"},
    )
    assert response.status_code == 200
    assert response.json()["rating"] == 9
    assert response.json()["title"] == "New title"
    assert "New body" in response.json()["body"]


def test_other_user_cannot_update_review(client):
    user_a, body_a = _register(client, "owner-a")
    token_a = body_a["access_token"]
    _hold_confirm(client, user_a, client.show_id)
    review = _make_review(client, user_a, token_a, client.movie_id, 5, "x", "y")
    assert review.status_code == 201, review.text
    review_id = review.json()["id"]

    user_b, body_b = _register(client, "owner-b")
    token_b = body_b["access_token"]
    response = client.patch(
        f"/api/reviews/{review_id}",
        headers=_auth(token_b),
        json={"title": "hacked"},
    )
    assert response.status_code == 403


def test_owner_can_delete_review(client):
    user, body = _register(client, "owner")
    token = body["access_token"]
    _hold_confirm(client, user, client.show_id)
    review = _make_review(client, user, token, client.movie_id, 5, "x", "y")
    assert review.status_code == 201, review.text
    review_id = review.json()["id"]

    response = client.delete(f"/api/reviews/{review_id}", headers=_auth(token))
    assert response.status_code == 200
    assert response.json()["status"] == "deleted"
    assert client.delete(f"/api/reviews/{review_id}", headers=_auth(token)).status_code == 404


def test_other_user_cannot_delete_review(client):
    user_a, body_a = _register(client, "owner-a")
    token_a = body_a["access_token"]
    _hold_confirm(client, user_a, client.show_id)
    review = _make_review(client, user_a, token_a, client.movie_id, 5, "x", "y")
    assert review.status_code == 201, review.text
    review_id = review.json()["id"]

    user_b, body_b = _register(client, "owner-b")
    token_b = body_b["access_token"]
    response = client.delete(f"/api/reviews/{review_id}", headers=_auth(token_b))
    assert response.status_code == 403
    # Review still exists
    assert client.get(f"/api/reviews/{review_id}").status_code == 200


# ---------------------------------------------------------------------------
# 4. Request validation
# ---------------------------------------------------------------------------

def test_create_rejects_invalid_rating(client):
    user, body = _register(client, "rating")
    token = body["access_token"]
    _hold_confirm(client, user, client.show_id)
    for bad in (0, 11, 12):
        response = _make_review(client, user, token, client.movie_id, bad, "t", "b")
        assert response.status_code == 422, bad


def test_create_rejects_empty_or_whitespace_text(client):
    user, body = _register(client, "empty")
    token = body["access_token"]
    _hold_confirm(client, user, client.show_id)
    for payload in (
        {"rating": 5, "title": "", "body": "x"},
        {"rating": 5, "title": "   ", "body": "x"},
        {"rating": 5, "title": "t", "body": ""},
        {"rating": 5, "title": "t", "body": "   "},
    ):
        response = client.post("/api/reviews", headers=_auth(token), json=payload)
        assert response.status_code == 422, payload


def test_create_rejects_oversized_text(client):
    user, body = _register(client, "long")
    token = body["access_token"]
    _hold_confirm(client, user, client.show_id)
    assert client.post(
        "/api/reviews", headers=_auth(token),
        json={"movie_id": client.movie_id, "rating": 5, "title": "T" * 161, "body": "x"},
    ).status_code == 422
    assert client.post(
        "/api/reviews", headers=_auth(token),
        json={"movie_id": client.movie_id, "rating": 5, "title": "t", "body": "B" * 3001},
    ).status_code == 422


def test_create_rejects_unknown_or_invalid_movie(client):
    user, body = _register(client, "movie")
    token = body["access_token"]
    _hold_confirm(client, user, client.show_id)
    # movie_id=0 fails schema validation (ge=1)
    assert client.post(
        "/api/reviews", headers=_auth(token),
        json={"movie_id": 0, "rating": 5, "title": "t", "body": "b"},
    ).status_code == 422
    # movie_id=999999 passes schema but 404 from DB
    assert client.post(
        "/api/reviews", headers=_auth(token),
        json={"movie_id": 999999, "rating": 5, "title": "t", "body": "b"},
    ).status_code == 404


def test_update_rejects_unknown_fields_and_zero_rating(client):
    user, body = _register(client, "upd")
    token = body["access_token"]
    _hold_confirm(client, user, client.show_id)
    review = _make_review(client, user, token, client.movie_id, 5, "x", "y")
    assert review.status_code == 201, review.text
    review_id = review.json()["id"]
    assert client.patch(
        f"/api/reviews/{review_id}", headers=_auth(token),
        json={"user_id": 9999, "title": "x"},
    ).status_code == 422
    assert client.patch(
        f"/api/reviews/{review_id}", headers=_auth(token),
        json={"rating": 0},
    ).status_code == 422


def test_patch_requires_at_least_one_field(client):
    user, body = _register(client, "patch")
    token = body["access_token"]
    _hold_confirm(client, user, client.show_id)
    review = _make_review(client, user, token, client.movie_id, 5, "x", "y")
    assert review.status_code == 201, review.text
    review_id = review.json()["id"]
    assert client.patch(f"/api/reviews/{review_id}", headers=_auth(token), json={}).status_code == 422


# ---------------------------------------------------------------------------
# 5. Likes
# ---------------------------------------------------------------------------

def test_like_review_toggles_on_and_off(client):
    user, body = _register(client, "like")
    token = body["access_token"]
    _hold_confirm(client, user, client.show_id)
    review = _make_review(client, user, token, client.movie_id, 7, "x", "y")
    assert review.status_code == 201, review.text
    review_id = review.json()["id"]

    first = client.post(f"/api/reviews/{review_id}/like", headers=_auth(token))
    assert first.status_code == 200
    assert first.json()["liked"] is True
    assert first.json()["likes"] >= 1

    second = client.post(f"/api/reviews/{review_id}/like", headers=_auth(token))
    assert second.status_code == 200
    assert second.json()["liked"] is False
    assert second.json()["likes"] >= 0


def test_duplicate_like_does_not_corrupt_count(client):
    user, body = _register(client, "dup")
    token = body["access_token"]
    _hold_confirm(client, user, client.show_id)
    review = _make_review(client, user, token, client.movie_id, 7, "x", "y")
    assert review.status_code == 201, review.text
    review_id = review.json()["id"]

    client.post(f"/api/reviews/{review_id}/like", headers=_auth(token))   # 1st: on
    client.post(f"/api/reviews/{review_id}/like", headers=_auth(token))   # 2nd: off
    result = client.post(f"/api/reviews/{review_id}/like", headers=_auth(token))  # 3rd: on
    assert result.status_code == 200
    assert result.json()["likes"] == 1
    assert result.json()["liked"] is True


def test_like_nonexistent_review_returns_404(client):
    user, body = _register(client, "noref")
    token = body["access_token"]
    assert client.post("/api/reviews/999999/like", headers=_auth(token)).status_code == 404


def test_like_requires_authentication(client):
    assert client.post("/api/reviews/1/like").status_code == 401


# ---------------------------------------------------------------------------
# 6. Listing
# ---------------------------------------------------------------------------

def test_list_returns_page_and_total(client):
    # Two different users review the same movie (UNIQUE constraint is per user)
    user_a, body_a = _register(client, "pag-a")
    token_a = body_a["access_token"]
    _hold_confirm(client, user_a, client.show_id)
    _make_review(client, user_a, token_a, client.movie_id, 7, "r1", "b1")

    user_b, body_b = _register(client, "pag-b")
    token_b = body_b["access_token"]
    _hold_confirm(client, user_b, client.show_id)
    _make_review(client, user_b, token_b, client.movie_id, 8, "r2", "b2")

    response = client.get("/api/reviews", params={"page": 1, "limit": 5})
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 1
    assert data["limit"] == 5
    assert len(data["items"]) == 2
    assert data["total"] == 2
    assert data["has_more"] is False
    assert "average_rating" in data
    assert "total_reviews" in data


def test_list_invalid_limit(client):
    user, body = _register(client, "lim")
    token = body["access_token"]
    _hold_confirm(client, user, client.show_id)
    _make_review(client, user, token, client.movie_id, 7, "x", "y")
    # limit=0 and limit=-1 are rejected by schema
    assert client.get("/api/reviews", params={"limit": 0}).status_code == 422
    assert client.get("/api/reviews", params={"limit": -1}).status_code == 422
    # limit=1000 exceeds max (20) → 422
    assert client.get("/api/reviews", params={"limit": 1000}).status_code == 422


def test_list_invalid_sort(client):
    user, body = _register(client, "sort")
    token = body["access_token"]
    _hold_confirm(client, user, client.show_id)
    _make_review(client, user, token, client.movie_id, 7, "x", "y")
    assert client.get("/api/reviews", params={"sort": "bogus"}).status_code == 422


def test_list_sorts_are_independent(client):
    def reviewer(prefix, rating):
        user, body = _register(client, prefix)
        token = body["access_token"]
        _hold_confirm(client, user, client.show_id)
        _make_review(client, user, token, client.movie_id, rating, prefix, prefix)
        return rating

    reviewer("c", 3)
    reviewer("b", 8)
    reviewer("a", 6)

    highest = client.get("/api/reviews", params={"sort": "highest"}).json()["items"]
    assert [r["rating"] for r in highest] == [8, 6, 3]

    lowest = client.get("/api/reviews", params={"sort": "lowest"}).json()["items"]
    assert [r["rating"] for r in lowest] == [3, 6, 8]

    newest = client.get("/api/reviews", params={"sort": "newest"}).json()["items"]
    ids = [r["id"] for r in newest]
    assert ids == sorted(ids, reverse=True)


# ---------------------------------------------------------------------------
# 7. Security
# ---------------------------------------------------------------------------

def test_listing_returns_raw_plain_text_body(client):
    user, body = _register(client, "raw")
    token = body["access_token"]
    _hold_confirm(client, user, client.show_id)
    payload_body = "<script>alert(1)</script>"
    response = _make_review(client, user, token, client.movie_id, 6, "t", payload_body)
    assert response.status_code == 201, response.text
    review_id = response.json()["id"]

    items = client.get("/api/reviews").json()["items"]
    assert len(items) == 1
    assert items[0]["body"] == payload_body
    assert "<script>" in items[0]["body"]


def test_create_review_ignores_secret_fields(client):
    user, body = _register(client, "secret")
    token = body["access_token"]
    _hold_confirm(client, user, client.show_id)
    payload = {
        "movie_id": client.movie_id, "rating": 5, "title": "t", "body": "b",
        "email": "stealth@example.com", "password": "x", "is_active": False,
    }
    assert client.post("/api/reviews", headers=_auth(token), json=payload).status_code == 422


# ---------------------------------------------------------------------------
# 8. Rate limiting
# ---------------------------------------------------------------------------

def test_rate_limit_review_create(client):
    limit = pytest.importorskip("app.ratelimit").RATE_LIMITS["review_create"]
    user, body = _register(client, "ratelimit-cr")
    token = body["access_token"]
    _hold_confirm(client, user, client.show_id)
    statuses = []
    last = None
    for _ in range(limit.max_requests + 1):
        last = client.post(
            "/api/reviews",
            headers=_auth(token),
            json={"movie_id": client.movie_id, "rating": 5, "title": "t", "body": "b"},
        )
        statuses.append(last.status_code)
    # First N should succeed (201) or hit 409 (duplicate) — not 429
    # Last one must be 429 once the rate limit is reached
    assert statuses[-1] == 429
    assert "Retry-After" in last.headers


def test_rate_limit_review_update_delete(client):
    limit = pytest.importorskip("app.ratelimit").RATE_LIMITS["review_update_delete"]
    user, body = _register(client, "rateu")
    token = body["access_token"]
    _hold_confirm(client, user, client.show_id)
    review = _make_review(client, user, token, client.movie_id, 5, "x", "y")
    assert review.status_code == 201, review.text
    review_id = review.json()["id"]
    statuses = []
    last = None
    for _ in range(limit.max_requests + 1):
        last = client.patch(
            f"/api/reviews/{review_id}", headers=_auth(token), json={"title": "n"},
        )
        statuses.append(last.status_code)
    assert all(s == 200 for s in statuses[:-1])
    assert statuses[-1] == 429
    assert "Retry-After" in last.headers


def test_rate_limit_review_like(client):
    limit = pytest.importorskip("app.ratelimit").RATE_LIMITS["review_like"]
    user, body = _register(client, "ratel")
    token = body["access_token"]
    _hold_confirm(client, user, client.show_id)
    review = _make_review(client, user, token, client.movie_id, 5, "x", "y")
    assert review.status_code == 201, review.text
    review_id = review.json()["id"]
    statuses = []
    last = None
    for _ in range(limit.max_requests + 1):
        last = client.post(f"/api/reviews/{review_id}/like", headers=_auth(token))
        statuses.append(last.status_code)
    assert all(s == 200 for s in statuses[:-1])
    assert statuses[-1] == 429
    assert "Retry-After" in last.headers


# ---------------------------------------------------------------------------
# 9. Migration regression
# ---------------------------------------------------------------------------

def test_review_table_after_migration_has_expected_schema(client):
    with TestingSessionLocal() as db:
        inspector = inspect(db.connection())
        columns = {c["name"]: c for c in inspector.get_columns("reviews")}
        assert "movie_id" in columns
        # movie_id is NOT NULL at the DB level — matches Review.movie_id:
        # Mapped[int] (never Optional[int]).
        assert columns["movie_id"]["nullable"] is False
        assert "updated_at" in columns
        unique = {c["name"] for c in inspector.get_unique_constraints("reviews")}
        assert "uq_review_user_movie" in unique
        # SQLite does not preserve FK constraint names through batch_alter_table;
        # verify the referential relationship by referenced table instead.
        foreign_keys = inspector.get_foreign_keys("reviews")
        fk_tables = {fk["referred_table"] for fk in foreign_keys}
        assert "movies" in fk_tables
        fk_cols = {
            col
            for fk in foreign_keys
            for col in fk["constrained_columns"]
            if fk["referred_table"] == "movies"
        }
        assert "movie_id" in fk_cols
        indexes = {c["name"] for c in inspector.get_indexes("reviews")}
        assert {"ix_reviews_movie_id", "ix_reviews_movie_created_at"} <= indexes


def test_anonymous_listing_contains_no_pii(client):
    user, body = _register(client, "anon")
    token = body["access_token"]
    _hold_confirm(client, user, client.show_id)
    _make_review(client, user, token, client.movie_id, 7, "x", "y")
    response = client.get("/api/reviews")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 1
    # Exactly the public review contract — user_id (int) is included for the
    # frontend owner check; no email / phone / password material ever leaks.
    assert set(payload["items"][0].keys()) == {
        "id", "user_id", "name", "rating", "title", "body", "spoiler",
        "likes", "liked", "created_at", "updated_at",
    }
    assert isinstance(payload["items"][0]["user_id"], int)
    assert all("@" not in item["title"] and "@" not in item["name"] for item in payload["items"])


def test_list_most_liked_uses_vote_count(client):
    user_a, body_a = _register(client, "a")
    token_a = body_a["access_token"]
    _hold_confirm(client, user_a, client.show_id)
    ra = _make_review(client, user_a, token_a, client.movie_id, 5, "a", "b")
    assert ra.status_code == 201, ra.text
    id_a = ra.json()["id"]

    user_b, body_b = _register(client, "b")
    token_b = body_b["access_token"]
    _hold_confirm(client, user_b, client.show_id)
    rb = _make_review(client, user_b, token_b, client.movie_id, 5, "b", "c")
    assert rb.status_code == 201, rb.text
    id_b = rb.json()["id"]

    # Like review A twice (toggle: on→off), then like review B once
    client.post(f"/api/reviews/{id_a}/like", headers=_auth(token_a))
    client.post(f"/api/reviews/{id_a}/like", headers=_auth(token_a))
    client.post(f"/api/reviews/{id_b}/like", headers=_auth(token_b))

    items = client.get("/api/reviews", params={"sort": "most_liked"}).json()["items"]
    # id_a has 0 likes (toggled off), id_b has 1 like → id_b first
    assert items[0]["id"] == id_b
    assert items[0]["likes"] == 1
    assert items[1]["id"] == id_a
    assert items[1]["likes"] == 0


def test_delete_requires_ownership_not_user_id_override(client):
    user, body = _register(client, "del")
    token = body["access_token"]
    _hold_confirm(client, user, client.show_id)
    review = _make_review(client, user, token, client.movie_id, 5, "x", "y")
    assert review.status_code == 201, review.text
    review_id = review.json()["id"]
    # Unauthenticated delete must 401 (not succeed)
    assert client.delete(f"/api/reviews/{review_id}").status_code == 401
    # Owner delete must succeed
    assert client.delete(f"/api/reviews/{review_id}", headers=_auth(token)).status_code == 200
    # Review is gone
    assert client.delete(f"/api/reviews/{review_id}", headers=_auth(token)).status_code == 404


def test_cannot_review_same_movie_twice(client):
    user, body = _register(client, "dup")
    token = body["access_token"]
    _hold_confirm(client, user, client.show_id)
    first = _make_review(client, user, token, client.movie_id, 7, "t", "b")
    assert first.status_code == 201
    second = _make_review(client, user, token, client.movie_id, 8, "t2", "b2")
    assert second.status_code == 409


def test_another_user_cannot_review_owners_movie(client):
    user_a, body_a = _register(client, "owner")
    token_a = body_a["access_token"]
    _hold_confirm(client, user_a, client.show_id)
    _make_review(client, user_a, token_a, client.movie_id, 8, "My film", "Blah")

    user_b, body_b = _register(client, "viewer")
    token_b = body_b["access_token"]
    # user_b only has a HELD booking (not confirmed) → 403
    client.post(
        "/api/bookings/hold",
        json={"user": user_b, "show_id": client.show_id,
              "seat_ids": _silver_seat_ids(client, client.show_id),
              "payment_method": "PENDING"},
    )
    blocked = client.post(
        "/api/reviews",
        headers=_auth(token_b),
        json={"movie_id": client.movie_id, "rating": 8, "title": "x", "body": "y"},
    )
    assert blocked.status_code == 403


# ---------------------------------------------------------------------------
# 10. Owner identity contract (frontend owner controls depend on this)
# ---------------------------------------------------------------------------

def test_listing_and_detail_expose_user_id_for_owner_controls(client):
    """The frontend shows Edit/Delete only when review.user_id === session
    user id — the API must therefore publish user_id (and nothing else
    account-related) on both the anonymous listing and the detail payload."""
    owner, owner_body = _register(client, "owner-ui")
    owner_token = owner_body["access_token"]
    owner_id = owner_body["user"]["id"]
    _hold_confirm(client, owner, client.show_id)
    mine = _make_review(client, owner, owner_token, client.movie_id, 8, "mine", "mine body")
    assert mine.status_code == 201, mine.text

    other, other_body = _register(client, "other-ui")
    _hold_confirm(client, other, client.show_id)
    theirs = _make_review(
        client, other, other_body["access_token"], client.movie_id, 6, "theirs", "their body"
    )
    assert theirs.status_code == 201, theirs.text

    items = client.get("/api/reviews").json()["items"]
    assert len(items) == 2
    by_id = {item["id"]: item for item in items}
    # Each item carries its author's id — exactly what the owner check reads.
    assert by_id[mine.json()["id"]]["user_id"] == owner_id
    assert by_id[theirs.json()["id"]]["user_id"] == other_body["user"]["id"]
    for item in items:
        assert set(item.keys()) == {
            "id", "user_id", "name", "rating", "title", "body", "spoiler",
            "likes", "liked", "created_at", "updated_at",
        }
        # No other account data rides along with user_id.
        assert not ({"email", "phone", "password_hash", "token_version", "is_active"} & set(item.keys()))

    detail = client.get(f"/api/reviews/{mine.json()['id']}")
    assert detail.status_code == 200
    assert detail.json()["user_id"] == owner_id


# ---------------------------------------------------------------------------
# 11. Request validation: movie_id is required (never defaulted to None)
# ---------------------------------------------------------------------------

def test_create_requires_movie_id(client):
    user, body = _register(client, "reqmovie")
    token = body["access_token"]
    _hold_confirm(client, user, client.show_id)
    # Missing field → 422 (schema: movie_id: int = Field(ge=1), no default)
    assert client.post(
        "/api/reviews", headers=_auth(token),
        json={"rating": 5, "title": "t", "body": "b"},
    ).status_code == 422
    # Explicit null → 422 (null is not an int; the column is NOT NULL)
    assert client.post(
        "/api/reviews", headers=_auth(token),
        json={"movie_id": None, "rating": 5, "title": "t", "body": "b"},
    ).status_code == 422
    # Sanity: a valid payload still creates the review
    ok = _make_review(client, user, token, client.movie_id, 6, "t", "b")
    assert ok.status_code == 201, ok.text


# ---------------------------------------------------------------------------
# 12. Likes: concurrent-insert race + error hygiene
# ---------------------------------------------------------------------------

def test_like_race_recovers_to_a_consistent_count(client, monkeypatch):
    user, body = _register(client, "race")
    token = body["access_token"]
    user_id = body["user"]["id"]
    _hold_confirm(client, user, client.show_id)
    review = _make_review(client, user, token, client.movie_id, 7, "x", "y")
    assert review.status_code == 201, review.text
    review_id = review.json()["id"]

    # A concurrent request wins the INSERT race: the like row already exists.
    with TestingSessionLocal() as s:
        s.add(ReviewLike(review_id=review_id, user_id=user_id))
        s.commit()

    # Our request's first SELECT misses that row (stale read), so its INSERT
    # hits uq_review_user_like — exactly the race the endpoint must absorb.
    real_scalar = Session.scalar
    state = {"stale_done": False}

    def stale_first_like_read(self, statement=None, *args, **kwargs):
        if (
            not state["stale_done"]
            and statement is not None
            and "review_likes" in str(statement)
        ):
            state["stale_done"] = True
            return None  # pretend the row isn't there yet
        return real_scalar(self, statement, *args, **kwargs)

    monkeypatch.setattr(Session, "scalar", stale_first_like_read)

    raced = client.post(f"/api/reviews/{review_id}/like", headers=_auth(token))
    assert raced.status_code == 200
    assert raced.json() == {"likes": 1, "liked": True}

    # With the stale read gone, a normal toggle sees exactly one row → off.
    monkeypatch.undo()
    toggled = client.post(f"/api/reviews/{review_id}/like", headers=_auth(token))
    assert toggled.status_code == 200
    assert toggled.json() == {"likes": 0, "liked": False}


def test_like_does_not_mask_unexpected_db_errors(client, monkeypatch):
    from sqlalchemy.exc import OperationalError

    user, body = _register(client, "likeerr")
    token = body["access_token"]
    _hold_confirm(client, user, client.show_id)
    review = _make_review(client, user, token, client.movie_id, 7, "x", "y")
    assert review.status_code == 201, review.text
    review_id = review.json()["id"]

    def explode(self, *args, **kwargs):
        raise OperationalError(
            "INSERT INTO review_likes", {}, Exception("disk I/O error")
        )

    monkeypatch.setattr(Session, "flush", explode)
    # The handler only absorbs the unique-race IntegrityError; any other
    # database failure surfaces instead of masquerading as a successful like.
    with pytest.raises(OperationalError):
        client.post(f"/api/reviews/{review_id}/like", headers=_auth(token))


# ---------------------------------------------------------------------------
# 13. Real Alembic migration runs (fresh temp DBs — never the dev database)
# ---------------------------------------------------------------------------

def _alembic_config(db_path, monkeypatch):
    """Point Alembic's env.py at ``db_path``.

    ``alembic/env.py`` reads ``app.config.settings.database_url``; Settings is
    frozen, so swap the module attribute with a patched copy for the test.
    """
    monkeypatch.setattr(
        app_config,
        "settings",
        dataclasses.replace(
            app_config.settings, database_url=f"sqlite:///{db_path.as_posix()}"
        ),
    )
    return AlembicConfig(str(BACKEND_DIR / "alembic.ini"))


def _reviews_schema(db_path):
    """Schema snapshot straight from SQLite (no reflection caches involved)."""
    con = sqlite3.connect(db_path)
    try:
        columns = {
            row[1]: {"notnull": row[3]}
            for row in con.execute("PRAGMA table_info(reviews)")
        }
        ddl_row = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='reviews'"
        ).fetchone()
        indexes = {row[1] for row in con.execute("PRAGMA index_list(reviews)")}
        fks = con.execute("PRAGMA foreign_key_list(reviews)").fetchall()
        null_movies = (
            con.execute("SELECT COUNT(*) FROM reviews WHERE movie_id IS NULL").fetchone()[0]
            if "movie_id" in columns
            else None
        )
        return {
            "columns": columns,
            "ddl": ddl_row[0] if ddl_row else "",
            "indexes": indexes,
            "fks": fks,
            "null_movies": null_movies,
        }
    finally:
        con.close()


def _insert_legacy_review(db_path, *, with_confirmed_booking):
    """A review that predates ``movie_id`` on the a1d8f6b9c2e1 schema.

    Returns the movie the backfill should resolve to (or None).

    Uses raw SQL with the *historical* column set: the ORM ``User`` model
    now carries Phase 8 columns (``role``) that do not exist at the
    ``a1d8f6b9c2e1`` revision the test database is stamped at. Inserting
    via the ORM would emit those new columns and fail; explicit SQL keeps
    the fixture faithful to the legacy schema.
    """
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with session_factory() as db:
        db.execute(
            text(
                "INSERT INTO users (full_name, email, phone, password_hash, is_active, "
                "token_version, created_at) VALUES (:name, :email, :phone, :hash, 1, 1, CURRENT_TIMESTAMP)"
            ),
            {"name": "Legacy Reviewer", "email": f"legacy-{db_path.stem}@example.com",
             "phone": "9876543210", "hash": "not-a-real-hash"},
        )
        user_id = db.execute(text("SELECT id FROM users WHERE email = :email"),
                             {"email": f"legacy-{db_path.stem}@example.com"}).scalar()
        movie_id = None
        if with_confirmed_booking:
            # Portable (no RETURNING): insert then look up.
            db.execute(text("INSERT INTO movies (title, metadata_json) VALUES ('Legacy Movie', '{}')"))
            movie_id = db.execute(text("SELECT id FROM movies WHERE title = 'Legacy Movie'")).scalar()
            db.execute(text(
                "INSERT INTO cities (name) VALUES ('Legacy City')"
            ))
            city_id = db.execute(text("SELECT id FROM cities WHERE name = 'Legacy City'")).scalar()
            db.execute(text(
                "INSERT INTO theaters (city_id, name, address) VALUES (:city, 'Legacy Theater', 'Addr')"
            ), {"city": city_id})
            theater_id = db.execute(text("SELECT id FROM theaters")).scalar()
            db.execute(text(
                "INSERT INTO screens (theater_id, name) VALUES (:theater, 'Screen 1')"
            ), {"theater": theater_id})
            screen_id = db.execute(text("SELECT id FROM screens")).scalar()
            db.execute(text(
                "INSERT INTO shows (movie_id, screen_id, show_date, show_time, status) "
                "VALUES (:movie, :screen, DATE('now'), '18:30:00', 'ACTIVE')"
            ), {"movie": movie_id, "screen": screen_id})
            show_id = db.execute(text("SELECT id FROM shows")).scalar()
            db.execute(text(
                "INSERT INTO bookings (booking_reference, user_id, show_id, status, total_amount, "
                "payment_method, payment_status, created_at) VALUES (:ref, :uid, :show, 'CONFIRMED', "
                "500, 'MOCK', 'PAID', CURRENT_TIMESTAMP)"
            ), {"ref": f"LEGACY-{db_path.stem}"[:32], "uid": user_id, "show": show_id})
        db.execute(
            text(
                "INSERT INTO reviews (user_id, rating, title, body, spoiler, created_at) "
                "VALUES (:uid, 7, 'Legacy', 'legacy body', 0, CURRENT_TIMESTAMP)"
            ),
            {"uid": user_id},
        )
        db.commit()
    engine.dispose()
    return movie_id


def test_alembic_upgrade_enforces_movie_id_contract(tmp_path, monkeypatch):
    """Fresh-chain upgrade produces the full movie_id contract, and the
    migration's downgrade is a valid inverse (round trip stable)."""
    db_path = tmp_path / "mig_contract.db"
    cfg = _alembic_config(db_path, monkeypatch)

    alembic_command.upgrade(cfg, "head")
    schema = _reviews_schema(db_path)
    assert schema["columns"]["movie_id"]["notnull"] == 1
    assert "updated_at" in schema["columns"]
    assert "CONSTRAINT uq_review_user_movie UNIQUE" in schema["ddl"]
    assert any(fk[2] == "movies" and fk[3] == "movie_id" for fk in schema["fks"])
    assert {"ix_reviews_movie_id", "ix_reviews_movie_created_at"} <= schema["indexes"]
    assert schema["null_movies"] == 0

    # Valid downgrade: everything this migration adds is reversible...
    alembic_command.downgrade(cfg, "a1d8f6b9c2e1")
    down = _reviews_schema(db_path)
    assert "movie_id" not in down["columns"]
    assert "updated_at" not in down["columns"]

    # ...and re-upgrading reproduces the same contract.
    alembic_command.upgrade(cfg, "head")
    again = _reviews_schema(db_path)
    assert again["columns"]["movie_id"]["notnull"] == 1
    assert "CONSTRAINT uq_review_user_movie UNIQUE" in again["ddl"]
    assert {"ix_reviews_movie_id", "ix_reviews_movie_created_at"} <= again["indexes"]


def test_alembic_backfills_resolvable_legacy_review(tmp_path, monkeypatch):
    """A legacy review whose author has exactly one confirmed-booking movie is
    deterministically backfilled before NOT NULL is enforced."""
    db_path = tmp_path / "legacy_backfill.db"
    cfg = _alembic_config(db_path, monkeypatch)
    alembic_command.upgrade(cfg, "a1d8f6b9c2e1")
    expected_movie_id = _insert_legacy_review(db_path, with_confirmed_booking=True)

    alembic_command.upgrade(cfg, "head")

    schema = _reviews_schema(db_path)
    assert schema["null_movies"] == 0
    assert schema["columns"]["movie_id"]["notnull"] == 1
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute("SELECT movie_id FROM reviews").fetchall()
    finally:
        con.close()
    # Deterministic: the single confirmed-booking movie of the reviewer.
    assert [row[0] for row in rows] == [expected_movie_id]


def test_alembic_fails_loudly_on_unresolvable_legacy_review(tmp_path, monkeypatch):
    """A legacy review with no resolvable movie aborts the migration with a
    clear error instead of guessing — and the run stays re-runnable."""
    db_path = tmp_path / "legacy_fail.db"
    cfg = _alembic_config(db_path, monkeypatch)
    alembic_command.upgrade(cfg, "a1d8f6b9c2e1")
    _insert_legacy_review(db_path, with_confirmed_booking=False)

    with pytest.raises(RuntimeError, match="movie_id"):
        alembic_command.upgrade(cfg, "head")

    # The failure fires before the NOT NULL flip: the row is still NULL,
    # nothing was guessed, and the guarded steps make a re-run safe.
    schema = _reviews_schema(db_path)
    assert "movie_id" in schema["columns"]
    assert schema["columns"]["movie_id"]["notnull"] == 0
    assert schema["null_movies"] == 1
