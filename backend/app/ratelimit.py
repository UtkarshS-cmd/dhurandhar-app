"""Lightweight fixed-window rate limiting for sensitive public endpoints.

Design constraints for the current architecture:

* **In-memory and single-process.** The app runs as one uvicorn process with
  local SQLite; there is no Redis infrastructure and this phase must not add
  one. Counters are therefore *not* shared between processes/instances — this
  is development and simple-deployment protection, not distributed
  production protection.
* **Replaceable seam.** Endpoints only depend on the ``rate_limit``
  dependency, which delegates to :class:`RateLimiter.check`. A Redis-backed
  limiter can be swapped in later by replacing that one object/dependency.
* **Deterministic tests.** The clock is injectable and state is resettable,
  so tests never sleep on wall-clock windows.
"""
from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import HTTPException, Request


@dataclass(frozen=True)
class RateLimit:
    """A fixed window: at most ``max_requests`` per ``window_seconds``."""

    max_requests: int
    window_seconds: int


# Named limits applied to sensitive public endpoints. Values are deliberately
# generous enough for normal users (a handful of attempts) while stopping
# unlimited password guessing, registration flooding, guest seat-hold abuse
# and booking-reference enumeration.
RATE_LIMITS: dict[str, RateLimit] = {
    # Per IP + submitted email: stops focused password guessing.
    "login": RateLimit(max_requests=10, window_seconds=60),
    # Per IP: stops spraying many accounts from one source.
    "login_ip": RateLimit(max_requests=30, window_seconds=60),
    # Per IP: stops trivial account flooding.
    "register": RateLimit(max_requests=8, window_seconds=3600),
    # Per IP: stops unlimited guest seat-hold creation.
    "hold": RateLimit(max_requests=10, window_seconds=600),
    # Per IP + booking email: stops one account/IP churning holds.
    "hold_identity": RateLimit(max_requests=6, window_seconds=600),
    # Per IP: lookup + confirm of booking references (anti-enumeration).
    "booking_ref": RateLimit(max_requests=30, window_seconds=60),
    # Per IP: review creation/update/delete is a public abuse surface.
    "review_create": RateLimit(max_requests=15, window_seconds=600),
    "review_update_delete": RateLimit(max_requests=20, window_seconds=600),
    "review_like": RateLimit(max_requests=30, window_seconds=60),
    # Per IP: abuse protection for public form endpoints.
    "newsletter": RateLimit(max_requests=5, window_seconds=3600),
    "contact": RateLimit(max_requests=5, window_seconds=3600),
}

# Defensive bound so a long-running process cannot accumulate unbounded keys.
_MAX_TRACKED_KEYS = 10_000


class RateLimiter:
    """Thread-safe fixed-window counter store.

    ``time_fn`` is injectable so tests can simulate window rollover without
    sleeping; production uses :func:`time.monotonic`.
    """

    def __init__(self, time_fn: Callable[[], float] = time.monotonic) -> None:
        self._time_fn = time_fn
        self._lock = threading.Lock()
        # key -> (requests counted in the current window, window start time)
        self._windows: dict[str, tuple[int, float]] = {}

    def check(self, key: str, limit: RateLimit) -> tuple[bool, int]:
        """Consume one request against ``limit``.

        Returns ``(allowed, retry_after_seconds)``; ``retry_after_seconds``
        is only meaningful when ``allowed`` is False.
        """
        now = self._time_fn()
        with self._lock:
            count, start = self._windows.get(key, (0.0, now))
            if now - start >= limit.window_seconds:
                count, start = 0, now
            if count >= limit.max_requests:
                retry_after = max(1, math.ceil(limit.window_seconds - (now - start)))
                return False, retry_after
            self._windows[key] = (count + 1, start)
            if len(self._windows) > _MAX_TRACKED_KEYS:
                self._prune(now)
            return True, 0

    def _prune(self, now: float) -> None:
        """Drop stale keys (called with the lock held)."""
        oldest_window = max(r.window_seconds for r in RATE_LIMITS.values())
        stale = [k for k, (_, start) in self._windows.items() if now - start > oldest_window]
        for key in stale:
            del self._windows[key]

    def reset(self) -> None:
        """Clear all counters (used between tests and available at runtime)."""
        with self._lock:
            self._windows.clear()


# Process-wide limiter. Tests call ``limiter.reset()`` for determinism.
limiter = RateLimiter()


def _client_ip(request: Request) -> str:
    # Direct connections (uvicorn/dev). When a reverse proxy is introduced,
    # resolve the real client from trusted forwarded headers *here* only.
    return request.client.host if request.client else "unknown"


async def _body_email(request: Request) -> str:
    """Best-effort email extraction for identity-keyed limits.

    Unreadable/malformed bodies fall back to an empty string; the request
    itself will be validated (and likely rejected) by the endpoint.
    """
    try:
        raw = await request.body()
    except Exception:
        return ""
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except ValueError:
        return ""
    if isinstance(data, dict):
        email = data.get("email")
        # Booking payloads nest the contact email under ``user``.
        if not isinstance(email, str) and isinstance(data.get("user"), dict):
            email = data["user"].get("email")
        if isinstance(email, str):
            return email.strip().lower()
    return ""


def rate_limit(name: str, key: str = "ip"):
    """FastAPI dependency factory enforcing a named :data:`RATE_LIMITS` entry.

    ``key`` is either ``"ip"`` (per client address) or ``"identity"``
    (per client address + email from the JSON body). Raises
    ``429 Too Many Requests`` with a ``Retry-After`` header when exceeded.
    """
    if name not in RATE_LIMITS:
        raise KeyError(f"Unknown rate limit {name!r}")
    if key not in ("ip", "identity"):
        raise KeyError(f"Unknown rate limit key strategy {key!r}")
    limit = RATE_LIMITS[name]

    async def dependency(request: Request) -> None:
        client = _client_ip(request)
        if key == "identity":
            limiter_key = f"{name}|{client}|{await _body_email(request)}"
        else:
            limiter_key = f"{name}|{client}"
        allowed, retry_after = limiter.check(limiter_key, limit)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="Too many requests",
                headers={"Retry-After": str(retry_after)},
            )

    return dependency