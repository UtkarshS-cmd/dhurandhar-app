# Dhurandhar Cinema — Refactored Full-Stack App

## Run locally (Windows — easiest)

Just double-click **`run.bat`** in the project root (or run it from a terminal):

```bat
run.bat
```

On first run it will automatically:

1. Find Python on your PATH (`py -3`, falling back to `python`).
2. Create `backend\.venv` if it doesn't exist.
3. Install `backend\requirements.txt` into the venv (first run only).
4. Create `backend\.env` from `.env.example` if missing.
5. Apply database migrations (`alembic upgrade head`) and run `seed.py`.
6. Start Uvicorn on port 5000 and open `http://localhost:5000` in your browser.

Press `Ctrl+C` in the window to stop the server. Subsequent runs skip the
already-completed setup steps and start the server directly.

> If port 5000 is already occupied by another process, the script warns you —
> stop that process first, otherwise uvicorn will fail to bind.

## Run locally (manual — macOS/Linux/Windows)

```bash
cd backend
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env   # Windows
# cp .env.example .env  # macOS/Linux
PYTHONPATH=. alembic upgrade head
PYTHONPATH=. python seed.py
PYTHONPATH=. uvicorn app.main:app --host 0.0.0.0 --port 5000
```

Open `http://localhost:5000`.

The local development database defaults to SQLite because PostgreSQL is not assumed to be installed. Set `DATABASE_URL` in `.env` to a PostgreSQL SQLAlchemy URL for deployment. Real environment variables always take precedence over `backend/.env`.

## Security configuration (Phase 2)

Configuration is selected with `APP_ENV` (read by `Settings` in `backend/app/config.py`):

| `APP_ENV` | `JWT_SECRET` behavior |
| --- | --- |
| `development` (default) | Optional. Missing, placeholder or short values are ignored and an **ephemeral development-only secret** is generated per process (sessions reset on restart) with a startup warning. |
| `production` | **Required.** Startup fails fast if the secret is missing, a known placeholder (`dev-only-change-me`, `change-this-in-production`), or shorter than 32 characters. |

Generate a real secret (never commit it):

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Set `JWT_SECRET` (and `APP_ENV=production` for shared deployments) via environment variables, `backend/.env`, or your host's secret store. `docker compose up` and the Docker image run with `APP_ENV=production` and therefore refuse to start without a real secret; `render.yaml` generates one automatically.

Implemented hardening:

- **JWT**: HS256 only, `sub`/`exp` required, `sub` must be a positive integer; every failure (malformed, expired, tampered, `alg=none`, unknown user) returns a uniform `401` with `WWW-Authenticate: Bearer` and never exposes token/database/config internals.
- **Authorization**: `/api/me/bookings` returns only the authenticated user's bookings. Guest checkout is preserved: a booking reference is a *capability* with 144 bits of entropy (`DHR-` + 24 url-safe characters), shape-checked before database access (uniform 404s), and rate-limited — while booking responses contain no user PII.
- **Rate limiting** (in-memory, single-process; replaceable seam for a future Redis-backed limiter): login 10/min per IP+email (30/min per IP), register 8/hour per IP, booking hold 10/10 min per IP + 6/10 min per IP+email, booking-reference lookup/confirm 30/min per IP, newsletter/contact 5/hour per IP. Exceeding a limit returns `429` with `Retry-After`.
- **Input validation**: Pydantic schemas are authoritative — bounded lengths, `show_id`/`seat_ids` ≥ 1, 1–6 seats, duplicate seats rejected by the engine (422), payment method restricted to the known values, emails trimmed/lowercased once at the edge, names stripped.
- **Errors**: validation failures return `422` without echoing submitted values (passwords/PII are never reflected); unexpected server errors stay generic `500`s.
- **CORS**: environment-driven explicit origins (localhost in development, same-origin only in production unless configured); `*` is rejected while credentials are enabled; methods/headers restricted to what the frontend uses.
- **Security headers**: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin` and a `Content-Security-Policy` compatible with the current frontend (`script-src 'self'`, local media, Google Fonts stylesheets; the inline `onerror` attributes were moved into a delegated listener in `js/app.js` so no inline script is required).

Known limitations are documented under *Deferred findings* below.

## Testing

Run the automated suite from the repository root (no manually running server required; tests use an isolated temporary SQLite database and reset rate-limiter state per test):

```bash
backend/.venv/Scripts/python -m pytest backend/tests -v   # Windows
# ./backend/.venv/bin/python -m pytest backend/tests -v  # macOS/Linux
```

The suite covers the Phase 1 baseline plus Phase 2 security tests: JWT/auth edge cases, configuration policy, security headers, CORS, rate limiting, booking-reference strength and authorization boundaries.

## Asset validation

```bash
backend/.venv/Scripts/python check_assets.py   # Windows
# ./backend/.venv/bin/python check_assets.py  # macOS/Linux
```

## Architecture

- `index.html` — semantic page markup only
- `css/` — core, booking, responsive styles
- `js/` — API, navigation, animation, video, music, booking, app/auth logic
- `assets/` — extracted video, poster, images and audio
- `backend/app/` — FastAPI, SQLAlchemy models, schemas, auth/security and payment abstraction
- `backend/alembic/` — database migration
- `backend/seed.py` — date-aware development seed data

## Booking flow

1. User details are validated in the browser.
2. City/theater/show data is loaded from the API.
3. Seat availability is fetched from the database.
4. A 10-minute server-side hold is created for the selected seats.
5. The backend calculates the authoritative total from seat records.
6. Confirmation transitions held seats to booked atomically.
7. The backend generates the booking reference.
8. Failed/double bookings return structured errors and the UI refreshes seat availability.

`PAYMENT_MODE=mock` is explicit development behavior. `Pay at Counter` remains payment `PENDING`; other methods use a mock paid response until a real gateway adapter is configured.

## Deferred findings (intentionally left for later phases)

Issues identified during the Phase 2 security review that belong to later phases:

- **Registration reveals account existence** (`409` on duplicate email) — inherent to the current sign-up UX; login itself is uniform. A future auth phase could move to e-mail-verification flows.
- **Guest hold embeds account credentials** — `POST /api/bookings/hold` doubles as an implicit password check when the e-mail already exists (bounded by the hold rate limit). A future booking/auth redesign should separate guest checkout from account creation.
- **Rate limiter is per-process** — in-memory counters do not coordinate across multiple workers/instances and reset on restart; the `RateLimiter` seam is designed for a Redis-backed replacement in the production infrastructure phase.
- **No global request-body size cap** — individual Pydantic fields are bounded, but a reverse proxy should cap total body size in production.
- **Proxy-aware client IP** — rate limits key on the direct connection address; wire trusted forwarded headers when a reverse proxy is introduced.
- **Payment gateway, webhooks, admin/RBAC, review moderation, cancellation redesign, Redis, PostgreSQL tuning, CI/CD** — out of scope by design (later phases).
