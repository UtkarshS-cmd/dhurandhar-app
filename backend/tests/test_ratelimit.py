"""Deterministic rate-limiter tests.

Unit tests exercise :class:`RateLimiter` with an injectable clock (no
wall-clock sleeping); endpoint tests prove under-limit requests succeed and
over-limit requests return 429 with ``Retry-After``. The shared ``client``
fixture resets limiter state before every test.
"""
from app.ratelimit import RATE_LIMITS, RateLimit, RateLimiter

from conftest import make_user, silver_seat_ids


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


# ---------------------------------------------------------------------------
# Unit behaviour (fully deterministic via injected clock)
# ---------------------------------------------------------------------------

def test_requests_under_limit_allowed():
    rl = RateLimiter(time_fn=FakeClock())
    limit = RateLimit(max_requests=3, window_seconds=60)
    for i in range(3):
        allowed, retry_after = rl.check("key", limit)
        assert allowed is True
        assert retry_after == 0, i


def test_requests_over_limit_blocked_with_retry_after():
    clock = FakeClock()
    rl = RateLimiter(time_fn=clock)
    limit = RateLimit(max_requests=3, window_seconds=60)
    for _ in range(3):
        assert rl.check("key", limit)[0] is True
    allowed, retry_after = rl.check("key", limit)
    assert allowed is False
    assert retry_after == 60  # frozen clock -> full window remaining
    clock.advance(25)
    allowed, retry_after = rl.check("key", limit)
    assert allowed is False
    assert 1 <= retry_after <= 35


def test_window_rollover_allows_requests_again():
    clock = FakeClock()
    rl = RateLimiter(time_fn=clock)
    limit = RateLimit(max_requests=2, window_seconds=60)
    assert rl.check("key", limit)[0] is True
    assert rl.check("key", limit)[0] is True
    assert rl.check("key", limit)[0] is False
    clock.advance(60)
    assert rl.check("key", limit)[0] is True


def test_reset_clears_all_keys():
    rl = RateLimiter(time_fn=FakeClock())
    limit = RateLimit(max_requests=1, window_seconds=600)
    assert rl.check("a", limit)[0] is True
    assert rl.check("a", limit)[0] is False
    rl.reset()
    assert rl.check("a", limit)[0] is True


def test_keys_are_independent():
    rl = RateLimiter(time_fn=FakeClock())
    limit = RateLimit(max_requests=1, window_seconds=60)
    assert rl.check("ip-1", limit)[0] is True
    assert rl.check("ip-1", limit)[0] is False
    assert rl.check("ip-2", limit)[0] is True


def test_named_limits_exist_for_sensitive_endpoints():
    for name in ("login", "login_ip", "register", "hold", "hold_identity",
                 "booking_ref", "newsletter", "contact"):
        assert name in RATE_LIMITS
        limit = RATE_LIMITS[name]
        assert 0 < limit.max_requests <= 60
        assert limit.window_seconds > 0


# ---------------------------------------------------------------------------
# Endpoint behaviour (fixed limits; no sleeping, fixture resets state)
# ---------------------------------------------------------------------------

def test_login_under_limit_allowed_over_limit_429(client):
    user = make_user("brute")
    assert client.post("/api/auth/register", json=user).status_code == 201
    attempt = {
        "email": user["email"],
        "password": "definitely-wrong-password",
    }
    limit = RATE_LIMITS["login"].max_requests
    statuses = []
    last = None
    for _ in range(limit + 1):
        last = client.post("/api/auth/login", json=attempt)
        statuses.append(last.status_code)
    # Under the limit: normal authentication failures (401), never 429.
    assert statuses[:-1] == [401] * limit
    # Over the limit: 429 with Retry-After.
    assert statuses[-1] == 429
    assert last.json()["detail"] == "Too many requests"
    retry_after = int(last.headers["Retry-After"])
    assert 1 <= retry_after <= RATE_LIMITS["login"].window_seconds


def test_register_flood_returns_429(client):
    limit = RATE_LIMITS["register"].max_requests
    statuses = []
    for _ in range(limit + 1):
        statuses.append(client.post("/api/auth/register", json=make_user("flood")).status_code)
    assert statuses[:-1] == [201] * limit
    assert statuses[-1] == 429


def test_booking_hold_flood_returns_429(client):
    limit = RATE_LIMITS["hold"].max_requests
    seat_ids = silver_seat_ids(client, client.show_id, count=1)
    statuses = []
    last = None
    for i in range(limit + 1):
        # Distinct emails keep the per-identity bucket quiet so the per-IP
        # bucket is the one being proven.
        last = client.post(
            "/api/bookings/hold",
            json={"user": make_user(f"hold{i}"), "show_id": client.show_id,
                  "seat_ids": seat_ids, "payment_method": "PENDING"},
        )
        statuses.append(last.status_code)
    assert all(s != 429 for s in statuses[:-1])
    assert statuses[0] == 201          # first hold succeeds
    assert statuses[1:limit] == [409] * (limit - 1)  # seat conflict, still counted
    assert statuses[-1] == 429
    assert "Retry-After" in last.headers


def test_booking_reference_enumeration_returns_429(client):
    limit = RATE_LIMITS["booking_ref"].max_requests
    # Well-formed but nonexistent reference: probing cannot tell this apart
    # from a real miss, and the counter still applies.
    reference = "DHR-" + "A" * 24
    responses = [client.get(f"/api/bookings/{reference}") for _ in range(limit + 1)]
    assert all(r.status_code == 404 for r in responses[:-1])
    assert responses[-1].status_code == 429
    assert "Retry-After" in responses[-1].headers


def test_newsletter_flood_returns_429(client):
    limit = RATE_LIMITS["newsletter"].max_requests
    payload = {"name": "Rate Limit", "email": "rate-limit@example.com"}
    statuses = [client.post("/api/newsletter", json=payload).status_code
                for _ in range(limit + 1)]
    assert statuses[:-1] == [201] * limit
    assert statuses[-1] == 429
