"""Phase 6 account/profile API and booking-authorization regressions."""

import os

import jwt
import pytest
from sqlalchemy import select

from app.models import User
from app.security import decode_token_payload
from conftest import TestingSessionLocal, make_user, silver_seat_ids


def _register(client, prefix="account"):
    user = make_user(prefix)
    response = client.post("/api/auth/register", json=user)
    assert response.status_code == 201
    return user, response.json()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _stored_user(email):
    with TestingSessionLocal() as db:
        return db.scalar(select(User).where(User.email == email.lower()))


def test_register_returns_safe_user_and_versioned_jwt(client):
    user, body = _register(client)

    assert set(body["user"]) == {"id", "full_name", "email", "phone", "role"}
    assert body["user"]["role"] == "USER"
    assert "password_hash" not in body["user"]
    assert user["password"] not in str(body)
    claims = jwt.decode(body["access_token"], os.environ["JWT_SECRET"], algorithms=["HS256"])
    assert claims["ver"] == 1
    assert decode_token_payload(body["access_token"])["ver"] == 1


def test_inactive_user_cannot_login_and_login_failures_are_uniform(client):
    user, _ = _register(client, "inactive")
    with TestingSessionLocal() as db:
        row = db.scalar(select(User).where(User.email == user["email"]))
        row.is_active = False
        db.commit()

    inactive = client.post(
        "/api/auth/login",
        json={"email": user["email"], "password": user["password"]},
    )
    unknown = client.post(
        "/api/auth/login",
        json={"email": "unknown@example.com", "password": user["password"]},
    )
    wrong_password = client.post(
        "/api/auth/login",
        json={"email": user["email"], "password": "Wrong@1234"},
    )
    assert inactive.status_code == unknown.status_code == wrong_password.status_code == 401
    assert inactive.json() == unknown.json() == wrong_password.json()


def test_me_requires_authentication_and_returns_only_safe_own_profile(client):
    user, body = _register(client, "profile")

    unauthenticated = client.get("/api/me")
    own_profile = client.get("/api/me", headers=_auth(body["access_token"]))

    assert unauthenticated.status_code == 401
    assert set(own_profile.json()) == {"id", "full_name", "email", "phone", "role"}
    assert own_profile.json()["role"] == "USER"
    assert own_profile.json()["email"] == user["email"]
    assert user["password"] not in own_profile.text
    assert all(key not in own_profile.json() for key in ("password_hash", "token_version", "is_active"))


def test_profile_update_accepts_name_phone_and_combination_but_not_email(client):
    user, body = _register(client, "profile-update")
    headers = _auth(body["access_token"])

    name_only = client.patch("/api/me", headers=headers, json={"full_name": "Updated Cinema User"})
    phone_only = client.patch("/api/me", headers=headers, json={"phone": "9123456789"})
    both = client.patch(
        "/api/me",
        headers=headers,
        json={"full_name": "Final Cinema User", "phone": "9876543211"},
    )
    email = client.patch("/api/me", headers=headers, json={"email": "changed@example.com"})

    assert name_only.status_code == phone_only.status_code == both.status_code == 200
    assert both.json()["full_name"] == "Final Cinema User"
    assert both.json()["phone"] == "9876543211"
    assert both.json()["email"] == user["email"]
    assert email.status_code == 422
    assert user["email"] not in email.text
    assert "changed@example.com" not in email.text
    assert client.get("/api/me", headers=headers).json()["email"] == user["email"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"user_id": 999999, "full_name": "Someone Else"},
        {"password_hash": "attacker-controlled", "full_name": "Someone Else"},
        {"is_active": False},
        {"password": "do-not-echo-this", "full_name": "Someone Else"},
    ],
)
def test_profile_update_rejects_empty_or_mass_assignment_fields_without_echoing_values(client, payload):
    user, body = _register(client, "mass-assignment")

    response = client.patch("/api/me", headers=_auth(body["access_token"]), json=payload)

    assert response.status_code == 422
    assert "attacker-controlled" not in response.text
    assert "do-not-echo-this" not in response.text
    assert user["email"] not in response.text


def test_password_change_rotates_tokens_and_authenticates_only_new_password(client):
    user, body = _register(client, "password-change")
    old_token = body["access_token"]
    original_hash = _stored_user(user["email"]).password_hash

    changed = client.post(
        "/api/me/password",
        headers=_auth(old_token),
        json={"current_password": user["password"], "new_password": "NewPass@5678"},
    )
    updated_user = _stored_user(user["email"])
    old_token_me = client.get("/api/me", headers=_auth(old_token))
    old_token_bookings = client.get("/api/me/bookings", headers=_auth(old_token))
    new_login = client.post(
        "/api/auth/login",
        json={"email": user["email"], "password": "NewPass@5678"},
    )
    old_password_login = client.post(
        "/api/auth/login",
        json={"email": user["email"], "password": user["password"]},
    )

    assert changed.status_code == 200
    assert updated_user.password_hash != original_hash
    assert updated_user.token_version == 2
    assert old_token_me.status_code == old_token_bookings.status_code == 401
    assert new_login.status_code == 200
    assert old_password_login.status_code == 401
    assert client.get("/api/me", headers=_auth(new_login.json()["access_token"])).status_code == 200


def test_invalid_password_changes_leave_password_and_token_version_unchanged(client):
    user, body = _register(client, "password-invalid")
    headers = _auth(body["access_token"])
    original_hash = _stored_user(user["email"]).password_hash
    requests = [
        {"current_password": "Wrong@1234", "new_password": "NewPass@5678"},
        {"current_password": user["password"], "new_password": user["password"]},
        {"current_password": user["password"], "new_password": "short"},
    ]

    responses = [
        client.post("/api/me/password", headers=headers, json=payload)
        for payload in requests
    ]

    assert [response.status_code for response in responses] == [401, 422, 422]
    assert not responses[0].headers.get("www-authenticate")
    assert user["password"] not in responses[2].text
    unchanged = _stored_user(user["email"])
    assert unchanged.password_hash == original_hash
    assert unchanged.token_version == 1
    assert client.get("/api/me", headers=headers).status_code == 200


def test_inactive_account_cannot_access_profile_or_own_bookings(client):
    user, body = _register(client, "inactive-access")
    with TestingSessionLocal() as db:
        row = db.scalar(select(User).where(User.email == user["email"]))
        row.is_active = False
        db.commit()

    profile = client.get("/api/me", headers=_auth(body["access_token"]))
    bookings = client.get("/api/me/bookings", headers=_auth(body["access_token"]))
    assert profile.status_code == bookings.status_code == 401


def test_bookings_are_scoped_to_authenticated_user_and_reference_capability_still_works(client):
    user_a, auth_a = _register(client, "booking-owner-a")
    _, auth_b = _register(client, "booking-owner-b")
    hold = client.post(
        "/api/bookings/hold",
        json={
            "user": user_a,
            "show_id": client.show_id,
            "seat_ids": silver_seat_ids(client, client.show_id),
            "payment_method": "PENDING",
        },
    )
    assert hold.status_code == 201
    reference = hold.json()["booking_reference"]

    a_bookings = client.get("/api/me/bookings", headers=_auth(auth_a["access_token"]))
    b_bookings = client.get("/api/me/bookings", headers=_auth(auth_b["access_token"]))
    attempted_override = client.get(
        f"/api/me/bookings?user_id={auth_a['user']['id']}",
        headers=_auth(auth_b["access_token"]),
    )
    capability_read = client.get(f"/api/bookings/{reference}")
    capability_confirm = client.post(
        f"/api/bookings/{reference}/confirm",
        json={"payment_method": "UPI / Google Pay / PhonePe"},
    )

    assert len(a_bookings.json()) == 1
    assert b_bookings.json() == []
    assert attempted_override.json() == []
    assert capability_read.status_code == 200
    assert capability_confirm.status_code == 200
    assert capability_confirm.json()["status"] == "CONFIRMED"


def test_guest_booking_hold_and_reference_confirmation_remain_unauthenticated(client):
    hold = client.post(
        "/api/bookings/hold",
        json={
            "user": make_user("guest"),
            "show_id": client.show_id,
            "seat_ids": silver_seat_ids(client, client.show_id),
            "payment_method": "PENDING",
        },
    )
    assert hold.status_code == 201
    reference = hold.json()["booking_reference"]

    confirmed = client.post(
        f"/api/bookings/{reference}/confirm",
        json={"payment_method": "UPI / Google Pay / PhonePe"},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "CONFIRMED"
    assert confirmed.json()["payment_status"] == "PAID"
