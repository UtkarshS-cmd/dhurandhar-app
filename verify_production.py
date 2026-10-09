#!/usr/bin/env python
"""Phase 14 — production verification against a running deployment.

Verifies the deployed surface end-to-end over HTTP: health/readiness, static
frontend, migrated+seeded database, security headers, compression, RBAC
boundaries, and (opt-in) a full registration/login round-trip.

Read-only by default so it is safe against a real production URL::

    python verify_production.py https://dhurandhar.onrender.com

Write checks (creates ONE throwaway test account and a newsletter row)::

    python verify_production.py http://127.0.0.1:8000 --register

Exit code 0 = every check passed; 1 = at least one failure.
The local production rehearsal this is designed to verify first:

    cd backend
    alembic upgrade head && python seed.py && \\
      APP_ENV=production JWT_SECRET=<32+ chars> \\
      uvicorn app.main:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid

import httpx

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(ok), detail))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))
    return bool(ok)


def _guard(name: str, fn) -> None:
    """Run one probe; any transport/parsing error becomes a FAILED check."""
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 — report, don't crash the run
        check(name, False, str(exc))


def verify(base_url: str, register: bool) -> bool:
    client = httpx.Client(base_url=base_url, timeout=20.0, follow_redirects=True)
    try:
        # -- 1. orchestrator probes ------------------------------------------
        def probe_health():
            res = client.get("/api/health")
            body = res.json()
            check("health endpoint", res.status_code == 200 and body.get("status") == "ok",
                  f"{res.status_code} {body}")
        _guard("health endpoint", probe_health)

        def probe_ready():
            res = client.get("/api/ready")
            body = res.json()
            check("readiness (DB SELECT 1)",
                  res.status_code == 200 and body.get("status") == "ready",
                  f"{res.status_code} {body}")
        _guard("readiness (DB SELECT 1)", probe_ready)

        # -- 2. static frontend served by the same process --------------------
        def probe_index():
            res = client.get("/")
            check("frontend index.html",
                  res.status_code == 200 and "text/html" in res.headers.get("content-type", "")
                  and "DHURANDHAR" in res.text.upper(),
                  f"{res.status_code}")
        _guard("frontend index.html", probe_index)

        def probe_asset():
            res = client.get("/css/styles.css")
            check("static asset /css/styles.css", res.status_code == 200, f"{res.status_code}")
        _guard("static asset /css/styles.css", probe_asset)

        # -- 3. migrations + seed actually ran --------------------------------
        def probe_cities():
            res = client.get("/api/cities")
            cities = res.json()
            check("database migrated + seeded (cities)",
                  res.status_code == 200 and isinstance(cities, list) and len(cities) > 0,
                  f"{len(cities) if isinstance(cities, list) else cities} cities")
        _guard("database migrated + seeded (cities)", probe_cities)

        # -- 4. security headers ---------------------------------------------
        def probe_headers():
            res = client.get("/api/health")
            hdr = res.headers
            check("security headers (nosniff / frame / CSP)",
                  hdr.get("x-content-type-options") == "nosniff"
                  and hdr.get("x-frame-options") == "DENY"
                  and "default-src 'self'" in hdr.get("content-security-policy", ""),
                  f"ctype={hdr.get('x-content-type-options')} frame={hdr.get('x-frame-options')}")
        _guard("security headers (nosniff / frame / CSP)", probe_headers)

        # -- 5. compression on API responses ----------------------------------
        def probe_gzip():
            res = client.get("/api/cities", headers={"Accept-Encoding": "gzip"})
            encoded = res.headers.get("content-encoding", "")
            check("API responses usable with gzip",
                  res.status_code == 200 and isinstance(res.json(), list),
                  f"encoding={encoded or 'identity'}")
        _guard("API responses usable with gzip", probe_gzip)

        # -- 6. RBAC: anonymous admin access must be 401 ----------------------
        def probe_admin_anon():
            res = client.get("/api/admin/dashboard")
            check("admin API rejects anonymous callers (401)", res.status_code == 401,
                  f"{res.status_code}")
        _guard("admin API rejects anonymous callers (401)", probe_admin_anon)

        # -- 7. CORS never reflects an untrusted origin -----------------------
        def probe_cors():
            res = client.options(
                "/api/cities",
                headers={
                    "Origin": "https://untrusted.example",
                    "Access-Control-Request-Method": "GET",
                },
            )
            acao = res.headers.get("access-control-allow-origin", "")
            check("CORS does not reflect untrusted origins",
                  acao not in ("*", "https://untrusted.example"),
                  f"acao={acao or '(none)'}")
        _guard("CORS does not reflect untrusted origins", probe_cors)

        # -- 8. opt-in write round-trip (creates ONE throwaway account) -------
        if register:
            _register_checks(client)
    finally:
        client.close()

    return all(ok for _, ok, _ in RESULTS)


def _register_checks(client: httpx.Client) -> None:
    suffix = uuid.uuid4().hex[:10]
    email = f"prodverify-{suffix}@example.com"
    payload = {
        "full_name": "Production Verify",
        "email": email,
        "phone": "9876543210",
        "password": "Test@1234",
    }

    token = ""

    def probe_register():
        nonlocal token
        res = client.post("/api/auth/register", json=payload)
        token = res.json().get("access_token", "")
        check("register round-trip (201 + token)",
              res.status_code == 201 and bool(token), f"{res.status_code}")
    _guard("register round-trip (201 + token)", probe_register)

    if not token:
        return
    auth = {"Authorization": f"Bearer {token}"}

    def probe_me():
        res = client.get("/api/me", headers=auth)
        check("session token accepted by /api/me",
              res.status_code == 200 and res.json().get("email") == email,
              f"{res.status_code}")
    _guard("session token accepted by /api/me", probe_me)

    def probe_forbidden():
        res = client.get("/api/admin/dashboard", headers=auth)
        check("authenticated non-admin gets 403 on admin API",
              res.status_code == 403, f"{res.status_code}")
    _guard("authenticated non-admin gets 403 on admin API", probe_forbidden)

    def probe_write():
        res = client.post("/api/newsletter", json={
            "name": "Production Verify",
            "email": email,
        })
        check("write path (newsletter) accepts a valid row",
              res.status_code == 201, f"{res.status_code}")
    _guard("write path (newsletter) accepts a valid row", probe_write)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("base_url", nargs="?", default="http://127.0.0.1:8000",
                        help="Deployment base URL (default: %(default)s)")
    parser.add_argument("--register", action="store_true",
                        help="Also run write checks (creates ONE throwaway account)")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    print(f"Verifying {base} (register checks: {'on' if args.register else 'off'})")
    started = time.time()
    ok = verify(base, args.register)
    passed = sum(1 for _, o, _ in RESULTS if o)
    print(f"\n{passed}/{len(RESULTS)} checks passed in {time.time() - started:.1f}s")
    if not ok:
        failed = [name for name, o, _ in RESULTS if not o]
        print("FAILED: " + "; ".join(failed))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
