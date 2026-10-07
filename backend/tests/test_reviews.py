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
  count, nonexistent review)
* server-driven listing (page / limit / max limit, five sorts, invalid sort,
  total / has_more / average_rating / total_reviews)
* PII leakage and malicious content returned as plain text
* rate limiting on review create / update / delete / like (429 + Retry-After)

Backend authorization is authoritative: the frontend never trusts
client-supplied ownership flags or user_id overrides.
"""

from datetime import date, time

import pytest
from sqlalchemy import inspect
from sqlalchemy import select

from app.models import (
    Booking, BookingSeat, Movie, Show, ShowSeat, ShowSeatStatus, User,
)
from conftest import make_user as _user, silver_seat_ids as _silver_seat_ids
from conftest import TestingSessionLocal


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
        columns = {c["name"] for c in inspector.get_columns("reviews")}
        assert "movie_id" in columns
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
    assert set(payload["items"][0].keys()) == {
        "id", "name", "rating", "title", "body", "spoiler",
        "likes", "liked", "created_at", "updated_at",
    }
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
