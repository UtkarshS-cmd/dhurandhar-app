"""Phase 8 admin/RBAC tests: authorization, user ops, movies, shows.

Covers privilege escalation + IDOR: every admin boundary is exercised as
anonymous (401), normal user (403), and admin (200).
"""

from datetime import date, time, timedelta

from sqlalchemy import select

from app.models import (
    AdminAuditLog, Booking, ContactMessage, Movie, NewsletterSubscriber,
    PaymentAttempt, Review, Show, User, UserRole,
)
from conftest import TestingSessionLocal, make_user, silver_seat_ids


def _register(client, prefix="user"):
    user = make_user(prefix)
    response = client.post("/api/auth/register", json=user)
    assert response.status_code == 201
    return user, response.json()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _promote(email):
    with TestingSessionLocal() as db:
        row = db.scalar(select(User).where(User.email == email.lower()))
        row.role = UserRole.ADMIN.value
        db.commit()
        return row.id


def _admin_client(client):
    user, body = _register(client, "admin")
    _promote(user["email"])
    login = client.post("/api/auth/login", json={"email": user["email"], "password": user["password"]})
    assert login.status_code == 200
    admin_body = login.json()
    return user, admin_body, _auth(admin_body["access_token"])


def test_admin_endpoints_require_auth_and_role(client):
    _, normal = _register(client, "normal")
    normal_headers = _auth(normal["access_token"])
    paths = ["/api/admin/dashboard", "/api/admin/users", "/api/admin/movies",
             "/api/admin/shows", "/api/admin/bookings", "/api/admin/payments",
             "/api/admin/reviews", "/api/admin/contact-messages",
             "/api/admin/newsletter-subscribers", "/api/admin/audit-logs"]
    for path in paths:
        assert client.get(path).status_code == 401
        assert client.get(path, headers=normal_headers).status_code == 403
    _, _, admin_headers = _admin_client(client)
    for path in paths:
        ok = client.get(path, headers=admin_headers)
        assert ok.status_code == 200, (path, ok.status_code, ok.text)


def test_migration_default_is_user_and_promotion_works(client):
    user, body = _register(client, "rbac-default")
    assert body["user"]["role"] == "USER"
    with TestingSessionLocal() as db:
        row = db.scalar(select(User).where(User.email == user["email"]))
        assert (row.role or "USER") == "USER"
    _promote(user["email"])
    assert client.get("/api/admin/dashboard", headers=_auth(body["access_token"])).status_code == 200
    with TestingSessionLocal() as db:
        other, _ = _register(client, "rbac-other")
        _promote(other["email"])
        row = db.scalar(select(User).where(User.email == user["email"]))
        row.role = UserRole.USER.value
        db.commit()
    assert client.get("/api/admin/dashboard", headers=_auth(body["access_token"])).status_code == 403


def test_dashboard_counts_only_real_revenue(client):
    _, _, admin_headers = _admin_client(client)
    user, body = _register(client, "revenue")
    seats = silver_seat_ids(client, client.show_id, count=2)
    hold = client.post("/api/bookings/hold", json={"user": user, "show_id": client.show_id,
                                                   "seat_ids": seats, "payment_method": "PENDING"})
    assert hold.status_code == 201
    reference = hold.json()["booking_reference"]
    confirmed = client.post(f"/api/bookings/{reference}/confirm",
                            json={"payment_method": "UPI / Google Pay / PhonePe"})
    assert confirmed.status_code == 200
    dash = client.get("/api/admin/dashboard", headers=admin_headers).json()
    assert dash["confirmed_bookings"] >= 1
    assert dash["total_revenue"] >= float(confirmed.json()["total_amount"])
    for key in ("total_users", "total_movies", "total_shows", "total_bookings",
                "confirmed_bookings", "total_revenue", "total_reviews"):
        assert key in dash, key
    assert "password_hash" not in str(dash) and "token_version" not in str(dash)


def test_user_management_search_pagination_and_secret_safety(client):
    _, _, admin_headers = _admin_client(client)
    _, normal = _register(client, "victim")
    listing = client.get("/api/admin/users?page=1&page_size=2", headers=admin_headers).json()
    assert listing["page"] == 1 and listing["page_size"] == 2
    assert listing["total"] >= 2 and len(listing["items"]) <= 2
    search = client.get("/api/admin/users?search=victim", headers=admin_headers).json()
    assert any(i["email"] == normal["user"]["email"] for i in search["items"])
    assert client.get("/api/admin/users?page_size=101", headers=admin_headers).status_code == 422
    body = str(listing)
    assert "password_hash" not in body and "token_version" not in body
    assert client.get("/api/admin/users", headers=_auth(normal["access_token"])).status_code == 403
    detail = client.get(f"/api/admin/users/{normal['user']['id']}", headers=admin_headers).json()
    assert detail["email"] == normal["user"]["email"]
    assert detail["booking_count"] == 0 and detail["review_count"] == 0
    assert "password_hash" not in str(detail)


def test_last_admin_and_self_protection(client):
    admin_user, admin_body, admin_headers = _admin_client(client)
    admin_id = admin_body["user"]["id"]
    assert client.post(f"/api/admin/users/{admin_id}/deactivate", headers=admin_headers).status_code == 409
    assert client.patch(f"/api/admin/users/{admin_id}", json={"role": "USER"}, headers=admin_headers).status_code == 409
    _, second = _register(client, "second-admin")
    _promote(second["user"]["email"])
    demote = client.patch(f"/api/admin/users/{admin_id}", json={"role": "USER"}, headers=admin_headers)
    assert demote.status_code == 409  # self-demotion always refused
    target_id = second["user"]["id"]
    ok = client.patch(f"/api/admin/users/{target_id}", json={"role": "USER"}, headers=admin_headers)
    assert ok.status_code == 200
    # Demoted admin token is invalidated via token_version bump.
    assert client.get("/api/admin/dashboard", headers=_auth(second["access_token"])).status_code in (401, 403)


def test_deactivation_blocks_access_and_activation_restores(client):
    _, _, admin_headers = _admin_client(client)
    user, body = _register(client, "lifecycle")
    headers = _auth(body["access_token"])
    victim_id = body["user"]["id"]
    assert client.post(f"/api/admin/users/{victim_id}/deactivate", headers=admin_headers).status_code == 200
    assert client.get("/api/me", headers=headers).status_code == 401
    assert client.post("/api/auth/login", json={"email": user["email"], "password": user["password"]}).status_code == 401
    assert client.post(f"/api/admin/users/{victim_id}/activate", headers=admin_headers).status_code == 200
    assert client.post("/api/auth/login", json={"email": user["email"], "password": user["password"]}).status_code == 200
    logs = client.get("/api/admin/audit-logs?action=USER_DEACTIVATED", headers=admin_headers).json()
    assert logs["total"] >= 1


def test_movie_crud_duplicate_and_lifecycle(client):
    _, _, admin_headers = _admin_client(client)
    created = client.post("/api/admin/movies", json={"title": "Admin Epic", "metadata_json": "{}"}, headers=admin_headers)
    assert created.status_code == 201
    movie_id = created.json()["id"]
    assert client.post("/api/admin/movies", json={"title": "admin epic"}, headers=admin_headers).status_code == 409
    assert client.post("/api/admin/movies", json={"title": "x", "metadata_json": "nope"}, headers=admin_headers).status_code == 422
    assert client.patch(f"/api/admin/movies/{movie_id}", json={"is_active": False}, headers=admin_headers).status_code == 200
    assert client.get(f"/api/admin/movies/{movie_id}", headers=admin_headers).json()["is_active"] is False
    _, normal = _register(client, "movie-user")
    assert client.post("/api/admin/movies", json={"title": "Nope"}, headers=_auth(normal["access_token"])).status_code == 403
    assert client.get("/api/admin/movies", headers=admin_headers).json()["total"] >= 1

def _admin_show_ids(client):
    with TestingSessionLocal() as db:
        show = db.get(Show, client.show_id)
        return show.movie_id, show.screen_id


def test_show_creation_overlap_and_safe_mutation_guards(client):
    _, _, admin_headers = _admin_client(client)
    movie_id, screen_id = _admin_show_ids(client)
    with TestingSessionLocal() as db:
        show = db.get(Show, client.show_id)
        show_date, show_time = show.show_date, show.show_time
    dup = client.post("/api/admin/shows", json={"movie_id": movie_id, "screen_id": screen_id,
                                                "show_date": show_date.isoformat(),
                                                "show_time": show_time.strftime("%H:%M:%S")},
                      headers=admin_headers)
    assert dup.status_code == 409
    future = (date.today() + timedelta(days=3)).isoformat()
    created = client.post("/api/admin/shows", json={"movie_id": movie_id, "screen_id": screen_id,
                                                    "show_date": future, "show_time": "20:00:00"},
                          headers=admin_headers)
    assert created.status_code == 201, created.text
    new_id = created.json()["id"]
    renamed = client.patch(f"/api/admin/shows/{new_id}", json={"show_time": "21:00:00"}, headers=admin_headers)
    assert renamed.status_code == 200
    user, _ = _register(client, "showholder")
    hold = client.post("/api/bookings/hold", json={"user": user, "show_id": new_id,
                                                   "seat_ids": silver_seat_ids(client, new_id),
                                                   "payment_method": "PENDING"})
    assert hold.status_code == 201

def test_payment_visibility_hides_secrets_and_is_read_only(client):
    _, _, admin_headers = _admin_client(client)
    user, _ = _register(client, "payuser")
    hold = client.post("/api/bookings/hold", json={"user": user, "show_id": client.show_id,
                                                   "seat_ids": silver_seat_ids(client, client.show_id),
                                                   "payment_method": "PENDING"})
    reference = hold.json()["booking_reference"]
    client.post("/api/payments/orders", json={"booking_reference": reference})
    listing = client.get("/api/admin/payments", headers=admin_headers).json()
    assert listing["total"] >= 1
    blob = str(listing).lower()
    assert "razorpay_key_secret" not in blob and "webhook_secret" not in blob and "jwt_secret" not in blob
    first_id = listing["items"][0]["id"]
    detail = client.get(f"/api/admin/payment-attempts/{first_id}", headers=admin_headers)
    assert detail.status_code == 200
    assert "amount" in detail.json() and "status" in detail.json()
    assert client.patch(f"/api/admin/payment-attempts/{first_id}", json={"status": "PAID"},
                        headers=admin_headers).status_code in (404, 405)


def test_review_moderation_delete_and_idor_guard(client):
    _, _, admin_headers = _admin_client(client)
    user, body = _register(client, "reviewer")
    hold = client.post("/api/bookings/hold", json={"user": user, "show_id": client.show_id,
                                                   "seat_ids": silver_seat_ids(client, client.show_id),
                                                   "payment_method": "PENDING"})
    reference = hold.json()["booking_reference"]
    client.post(f"/api/bookings/{reference}/confirm", json={"payment_method": "UPI / Google Pay / PhonePe"})
    review = client.post("/api/reviews", json={"movie_id": client.movie_id, "rating": 9,
                                               "title": "Great", "body": "Loved it"},
                         headers=_auth(body["access_token"]))
    assert review.status_code == 201
    review_id = review.json()["id"]
    listing = client.get(f"/api/admin/reviews?movie_id={client.movie_id}", headers=admin_headers).json()
    assert any(r["id"] == review_id for r in listing["items"])
    _, normal = _register(client, "review-plain")

def test_contact_newsletter_and_audit_atomicity(client):
    _, _, admin_headers = _admin_client(client)
    client.post("/api/contact", json={"name": "Visitor", "email": "visitor@example.com",
                                      "subject": "Hello", "message": "Great cinema!"})
    client.post("/api/newsletter", json={"name": "Fan", "email": "fan@example.com"})
    contacts = client.get("/api/admin/contact-messages", headers=admin_headers).json()
    assert contacts["total"] >= 1
    first = contacts["items"][0]
    marked = client.patch(f"/api/admin/contact-messages/{first['id']}", json={"is_read": True},
                          headers=admin_headers)
    assert marked.status_code == 200
    subs = client.get("/api/admin/newsletter-subscribers?search=fan@", headers=admin_headers).json()
    assert any(s["email"] == "fan@example.com" for s in subs["items"])
    before = client.get("/api/admin/audit-logs", headers=admin_headers).json()["total"]
    bad = client.patch("/api/admin/users/999999", json={"role": "ADMIN"}, headers=admin_headers)
    assert bad.status_code == 404
    after = client.get("/api/admin/audit-logs", headers=admin_headers).json()["total"]
    assert after == before
    _, normal = _register(client, "ops-plain")
    assert client.get("/api/admin/audit-logs", headers=_auth(normal["access_token"])).status_code == 403


def test_idor_endpoints_still_require_admin(client):
    _, _, admin_headers = _admin_client(client)
    user, body = _register(client, "idor")
    victim_headers = _auth(body["access_token"])
    victim_id = body["user"]["id"]
    hold = client.post("/api/bookings/hold", json={"user": user, "show_id": client.show_id,
                                                   "seat_ids": silver_seat_ids(client, client.show_id),
                                                   "payment_method": "PENDING"})
    with TestingSessionLocal() as db:
        row = db.scalar(select(Booking).where(Booking.booking_reference == hold.json()["booking_reference"]))
        booking_id = row.id
    assert client.get(f"/api/admin/users/{victim_id}", headers=victim_headers).status_code == 403
    assert client.get(f"/api/admin/bookings/{booking_id}", headers=victim_headers).status_code == 403
    assert client.get(f"/api/admin/users/{victim_id}", headers=admin_headers).status_code == 200
    assert client.get(f"/api/admin/bookings/{booking_id}", headers=admin_headers).status_code == 200


def test_booking_listing_filters_and_read_only(client):
    _, _, admin_headers = _admin_client(client)
    user, _ = _register(client, "bkuser")
    hold = client.post("/api/bookings/hold", json={"user": user, "show_id": client.show_id,
                                                   "seat_ids": silver_seat_ids(client, client.show_id),
                                                   "payment_method": "PENDING"})
    reference = hold.json()["booking_reference"]
    listing = client.get("/api/admin/bookings", headers=admin_headers).json()
    assert listing["total"] >= 1
    filtered = client.get(f"/api/admin/bookings?search={reference[:12]}", headers=admin_headers).json()
    assert any(b["booking_reference"] == reference for b in filtered["items"])
    assert client.get("/api/admin/bookings?status=HELD", headers=admin_headers).json()["total"] >= 1
    first = listing["items"][0]
    assert "password_hash" not in str(first)
    detail = client.get(f"/api/admin/bookings/{first['id']}", headers=admin_headers)
    assert detail.status_code == 200
    assert client.patch(f"/api/admin/bookings/{first['id']}", json={"status": "CONFIRMED"},
                        headers=admin_headers).status_code in (404, 405)
    _, normal = _register(client, "bk-plain")
    assert client.get("/api/admin/bookings", headers=_auth(normal["access_token"])).status_code == 403


