"""Phase 2 security tests: configuration policy, JWT/authentication edge
cases, authorization boundaries, security headers, CORS and input
validation/normalization."""
import base64
import json
import warnings
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.config import (
    INSECURE_JWT_DEFAULTS,
    MIN_JWT_SECRET_LENGTH,
    Settings,
    settings,
)
from app.main import CONTENT_SECURITY_POLICY
from app.security import ALGORITHM, AuthenticationError, decode_token

from conftest import make_user, silver_seat_ids

STRONG_SECRET = "phase2-unit-test-secret-0123456789abcdef"  # 46 chars


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


# ---------------------------------------------------------------------------
# Configuration policy (production must fail fast on insecure secrets)
# ---------------------------------------------------------------------------

def test_production_missing_secret_fails(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        Settings()


def test_production_placeholder_secret_fails(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    for placeholder in sorted(INSECURE_JWT_DEFAULTS):
        monkeypatch.setenv("JWT_SECRET", placeholder)
        with pytest.raises(RuntimeError, match="JWT_SECRET"):
            Settings()


def test_production_short_secret_fails(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", "too-short")
    with pytest.raises(RuntimeError, match="at least"):
        Settings()


def test_production_valid_secret_accepted(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", STRONG_SECRET)
    resolved = Settings()
    assert resolved.is_production
    assert resolved.jwt_secret == STRONG_SECRET


def test_development_missing_secret_generates_ephemeral(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.warns(UserWarning, match="development-only"):
        resolved = Settings()
    assert resolved.jwt_secret not in INSECURE_JWT_DEFAULTS
    assert len(resolved.jwt_secret) >= MIN_JWT_SECRET_LENGTH
    assert not resolved.is_production


def test_development_ignores_placeholder_secret(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("JWT_SECRET", "dev-only-change-me")
    with pytest.warns(UserWarning, match="development-only"):
        resolved = Settings()
    assert resolved.jwt_secret != "dev-only-change-me"
    assert len(resolved.jwt_secret) >= MIN_JWT_SECRET_LENGTH


def test_development_strong_secret_used_verbatim(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("JWT_SECRET", STRONG_SECRET)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        resolved = Settings()
    assert resolved.jwt_secret == STRONG_SECRET


def test_invalid_app_env_fails(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    with pytest.raises(RuntimeError, match="APP_ENV"):
        Settings()


def test_cors_wildcard_rejected(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("CORS_ORIGINS", "*")
    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        Settings()


def test_cors_origin_without_scheme_rejected(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("CORS_ORIGINS", "localhost:5000")
    with pytest.raises(RuntimeError, match="Invalid CORS origin"):
        Settings()


def test_cors_production_default_is_same_origin_only(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", STRONG_SECRET)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    assert Settings().cors_origins == ()


def test_cors_development_default_keeps_local_workflow(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    assert "http://localhost:5000" in Settings().cors_origins


# ---------------------------------------------------------------------------
# Authentication edge cases (uniform 401s, no internal leakage)
# ---------------------------------------------------------------------------

def _register(client, prefix="auth"):
    user = make_user(prefix)
    response = client.post("/api/auth/register", json=user)
    assert response.status_code == 201
    return user, response.json()


def _assert_no_internals(response):
    text = response.text.lower()
    for needle in ("jwt", "decode", "traceback", "sqlalchemy", "scrypt", "exception", "hs256"):
        assert needle not in text, f"response leaks {needle!r}: {response.text}"
    assert settings.jwt_secret not in response.text


def test_missing_authorization_header(client):
    response = client.get("/api/me/bookings")
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"
    assert response.json()["detail"] == "Authentication required"


def test_wrong_auth_scheme(client):
    response = client.get(
        "/api/me/bookings", headers={"Authorization": "Basic dXNlcjpwYXNz"}
    )
    assert response.status_code == 401
    _assert_no_internals(response)


def test_malformed_token(client):
    for bad in ("not-a-jwt", "a.b.c", "Bearer", "eyJhbGciOiJIUzI1NiJ9"):
        response = client.get(
            "/api/me/bookings", headers={"Authorization": f"Bearer {bad}"}
        )
        assert response.status_code == 401, bad
        _assert_no_internals(response)


def test_expired_token_rejected(client):
    _, body = _register(client, "expired")
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(body["user"]["id"]),
        "iat": now - timedelta(hours=13),
        "exp": now - timedelta(hours=1),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)
    response = client.get("/api/me/bookings", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    _assert_no_internals(response)


def test_token_with_non_integer_subject_rejected(client):
    now = datetime.now(timezone.utc)
    for sub in ("abc", "1.5", "", " "):
        token = jwt.encode(
            {"sub": sub, "iat": now, "exp": now + timedelta(hours=1)},
            settings.jwt_secret, algorithm=ALGORITHM,
        )
        response = client.get("/api/me/bookings", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401, sub


def test_token_missing_sub_or_exp_rejected(client):
    now = datetime.now(timezone.utc)
    tokens = [
        jwt.encode({"exp": now + timedelta(hours=1)}, settings.jwt_secret, algorithm=ALGORITHM),
        jwt.encode({"sub": "1"}, settings.jwt_secret, algorithm=ALGORITHM),
    ]
    for token in tokens:
        response = client.get("/api/me/bookings", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401


def test_token_signed_with_wrong_secret_rejected(client):
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {"sub": "1", "iat": now, "exp": now + timedelta(hours=1)},
        "attacker-controlled-secret-that-is-long-enough",
        algorithm=ALGORITHM,
    )
    response = client.get("/api/me/bookings", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    _assert_no_internals(response)


def test_token_with_none_algorithm_rejected(client):
    # Hand-crafted unsigned JWT: header explicitly claims alg=none.
    future = int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())
    token = (
        f"{_b64url(json.dumps({'alg': 'none', 'typ': 'JWT'}).encode())}."
        f"{_b64url(json.dumps({'sub': '1', 'exp': future}).encode())}."
    )
    response = client.get("/api/me/bookings", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    _assert_no_internals(response)


def test_token_referencing_deleted_user_rejected(client):
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {"sub": "999999", "iat": now, "exp": now + timedelta(hours=1)},
        settings.jwt_secret, algorithm=ALGORITHM,
    )
    response = client.get("/api/me/bookings", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or expired session"


def test_decode_token_raises_authentication_error():
    with pytest.raises(AuthenticationError):
        decode_token("garbage")
    with pytest.raises(AuthenticationError):
        decode_token("")


def test_valid_token_accesses_own_bookings(client):
    _, body = _register(client, "valid")
    response = client.get(
        "/api/me/bookings",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# Login: no user enumeration, validation
# ---------------------------------------------------------------------------

def test_login_failures_indistinguishable(client):
    known = make_user("known")
    assert client.post("/api/auth/register", json=known).status_code == 201
    wrong = client.post("/api/auth/login", json={"email": known["email"], "password": "WrongPw123"})
    missing = client.post("/api/auth/login", json={"email": "nobody-here@example.com", "password": "WrongPw123"})
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json() == missing.json()


def test_login_malformed_email_rejected(client):
    response = client.post("/api/auth/login", json={"email": "not-an-email", "password": "x"})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Registration/input validation and normalization
# ---------------------------------------------------------------------------

def test_duplicate_email_conflict(client):
    user = make_user("dup")
    assert client.post("/api/auth/register", json=user).status_code == 201
    response = client.post("/api/auth/register", json=user)
    assert response.status_code == 409


def test_duplicate_email_case_insensitive(client):
    user = make_user("case")
    assert client.post("/api/auth/register", json=user).status_code == 201
    mixed = dict(user, email=user["email"].upper())
    response = client.post("/api/auth/register", json=mixed)
    assert response.status_code == 409


def test_register_malformed_email(client):
    user = make_user("bademail")
    user["email"] = "not-an-email"
    assert client.post("/api/auth/register", json=user).status_code == 422


def test_register_invalid_phone(client):
    for phone in ("12345", "abcdefghij", "987654321012345"):
        user = make_user("phone")
        user["phone"] = phone
        assert client.post("/api/auth/register", json=user).status_code == 422, phone


def test_register_short_password(client):
    user = make_user("short")
    user["password"] = "Ab1!"
    assert client.post("/api/auth/register", json=user).status_code == 422


def test_register_excessive_password(client):
    user = make_user("long")
    user["password"] = "A1!" + "x" * 200
    assert client.post("/api/auth/register", json=user).status_code == 422


def test_register_missing_fields(client):
    user = make_user("missing")
    del user["phone"]
    assert client.post("/api/auth/register", json=user).status_code == 422


def test_register_strips_name_and_normalizes_email(client):
    user = make_user("strip")
    user["full_name"] = "  Ada Lovelace  "
    user["email"] = f"  {user['email'].upper()}  "
    response = client.post("/api/auth/register", json=user)
    assert response.status_code == 201
    assert response.json()["user"]["full_name"] == "Ada Lovelace"
    assert response.json()["user"]["email"] == user["email"].strip().lower()


def test_validation_errors_do_not_echo_submitted_values(client):
    user = make_user("echo")
    user["password"] = "Sh0rtPw"  # 7 chars -> 422
    response = client.post("/api/auth/register", json=user)
    assert response.status_code == 422
    assert "Sh0rtPw" not in response.text
    assert '"input"' not in response.text
    detail = response.json()["detail"]
    assert isinstance(detail, list) and detail
    assert {"loc", "msg", "type"} <= set(detail[0].keys())


# ---------------------------------------------------------------------------
# Authorization boundary: /api/me/bookings is own-bookings only
# ---------------------------------------------------------------------------

def test_me_bookings_isolation_between_users(client):
    user_a = make_user("alice")
    user_b = make_user("bob")
    token_a = client.post("/api/auth/register", json=user_a).json()["access_token"]
    token_b = client.post("/api/auth/register", json=user_b).json()["access_token"]
    seats = silver_seat_ids(client, client.show_id, count=2)
    hold = client.post(
        "/api/bookings/hold",
        json={"user": user_b, "show_id": client.show_id, "seat_ids": seats,
              "payment_method": "PENDING"},
    )
    assert hold.status_code == 201
    alice_bookings = client.get(
        "/api/me/bookings", headers={"Authorization": f"Bearer {token_a}"}
    )
    bob_bookings = client.get(
        "/api/me/bookings", headers={"Authorization": f"Bearer {token_b}"}
    )
    assert alice_bookings.status_code == bob_bookings.status_code == 200
    assert alice_bookings.json() == []  # no cross-user leakage (IDOR guard)
    assert len(bob_bookings.json()) == 1


# ---------------------------------------------------------------------------
# Security headers and CORS
# ---------------------------------------------------------------------------

def test_security_headers_present(client):
    response = client.get("/api/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    csp = response.headers["Content-Security-Policy"]
    assert csp == CONTENT_SECURITY_POLICY
    for directive in ("default-src 'self'", "script-src 'self'", "object-src 'none'",
                      "frame-ancestors 'none'", "base-uri 'self'"):
        assert directive in csp


def test_security_headers_present_on_error_responses(client):
    responses = (
        client.get("/api/does-not-exist"),
        client.post("/api/auth/login", json={"email": "x@y.z", "password": ""}),
    )
    for response in responses:
        assert response.status_code in (404, 422)
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert "Content-Security-Policy" in response.headers


def test_cors_allows_configured_origin(client):
    response = client.options(
        "/api/health",
        headers={"Origin": "http://localhost:5000",
                 "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5000"


def test_cors_rejects_unknown_origin(client):
    response = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
