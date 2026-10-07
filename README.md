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

## Phase 3 — booking engine (transactional correctness)

`backend/app/services/booking.py` is the single authority for booking state:

- **State machine** (centralized `transition_seat` / `transition_booking` /
  `transition_payment`; illegal moves raise `InvalidTransition`):
  `ShowSeat: AVAILABLE -> HELD -> BOOKED`, `HELD -> AVAILABLE` on expiry only;
  `Booking: HELD -> CONFIRMED | CANCELLED` (both terminal — no silent
  backward moves); `Payment: PENDING -> PAID | FAILED`, `FAILED` retryable.
  No other module assigns booking/seat/payment status.
- **Atomic transactions** (`booking_transaction`): write-intent first (SQLite
  single-writer reservation before any availability check), commit on success,
  full rollback on any exception, bounded retry on lock contention only.
- **Concurrency**: `SELECT ... FOR UPDATE` inside the mutating transaction on
  PostgreSQL; deterministic `ORDER BY show_seats.id` lock acquisition (no
  deadlock for reversed seat order); whole-request failure on any conflict
  (no partial holds); duplicate seat ids rejected before any write (422).
- **Expiry (10 min TTL)**: `HELD -> AVAILABLE` + `HELD -> CANCELLED`
  atomically via request-triggered `cleanup_expired_holds` (transactionally
  safe, never touches `CONFIRMED`/`BOOKED`); expired confirms cancel-and-free
  seats then return 409 — an expired hold can never become `CONFIRMED`, even
  when cleanup and confirm race; freed seats are re-holdable; the DB (never
  process memory) is authoritative across restarts.
- **Idempotency**: identical re-hold by the same user returns the existing
  booking; repeat confirm of a `CONFIRMED` booking is a deterministic 409
  (frontend also disables the button + reconciles lost responses via
  `GET /api/bookings/{reference}`).
- **Payment boundary** (Phase 4): provider-agnostic `PaymentService` +
  `PaymentProvider` abstraction (`backend/app/services/payments/`). Mock
  gateway crash rolls back, hold stays `HELD`/`PENDING` (502); explicit
  decline persists `FAILED` without confirming (402, seats stay `HELD`);
  seats become `BOOKED` only on verified success. See the Phase 4 payment
  architecture section below.
- **DB constraints** (no migration needed — all present in the initial
  schema): `users.email`, `bookings.booking_reference`, `uq_show_seat`,
  `uq_booking_seat`, `uq_screen_showtime` are enforced by the database, not
  just Python.
- **SQLite vs PostgreSQL**: SQLite serializes writers via the write-intent
  lock (concurrency tests use real threads + barriers); PostgreSQL uses real
  row locks. Actual PostgreSQL concurrency verification is deferred until a
  PostgreSQL environment is available — no results are faked.

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

`PAYMENT_MODE=mock` / `PAYMENT_PROVIDER=mock` is explicit development behavior. `Pay at Counter` remains payment `PENDING`; other methods use a mock paid response until a real gateway adapter is configured.

## Payment architecture (Phase 4)

The cinema backend **owns** booking/payment state; the external provider is
only an integration boundary:

```
Cinema Application
       │
  PaymentService            backend/app/services/payments/service.py
       │
  PaymentProvider           backend/app/services/payments/base.py
      / \
MockProvider          RazorpayProvider
 (mock.py)               (razorpay.py) ── Razorpay API ── Webhook
```

- **Provider abstraction** — `PaymentProvider` (ABC) exposes only normalized
  dataclasses: `PaymentOrder`, `PaymentVerification`, `WebhookEvent`,
  `PaymentResult`. No Razorpay SDK type ever leaves `razorpay.py`; the rest
  of the backend depends on the abstractions only.
- **Provider selection** — `PAYMENT_PROVIDER=mock|razorpay` (legacy alias:
  `PAYMENT_MODE`). Mock is the default and requires **no credentials**;
  tests and local dev run without any external payment config.
  Selecting `razorpay` **fails fast** unless `RAZORPAY_KEY_ID`,
  `RAZORPAY_KEY_SECRET` and `RAZORPAY_WEBHOOK_SECRET` are all set

### Payment state machine

`payment_attempts.status` (defined centrally in `service.py`):

```
CREATED ──▶ PENDING ──▶ AUTHORIZED ──▶ PAID ──▶ REFUNDED
   │           │             │
   └───────────┴─────────────┴──▶ FAILED ──▶ PENDING   (retry)
                PENDING/CREATED/AUTHORIZED ──▶ CANCELLED   (terminal)
```

Terminal: `PAID` (→ `REFUNDED` only), `CANCELLED`, `REFUNDED`.
`PAID → FAILED`, `PAID → PENDING`, `REFUNDED → PAID`, `CANCELLED → PAID`
are rejected by `transition_attempt()` with `InvalidTransition`.
The legacy `Booking.payment_status` (`PENDING/PAID/FAILED`) is preserved as
a booking-level mirror so Phase 3 readers keep working.

### Booking/payment lifecycle

1. **Hold** (Phase 3, unchanged): short DB transaction locks seats, prices
   server-side, creates `Booking(HELD)` + seats `HELD` for 10 minutes.
2. **Create payment order** — `POST /api/payments/orders`: inside a short
   transaction the booking is locked/validated, a
   `PaymentAttempt(CREATED)` row is inserted with the **DB-stored amount**,
   then the transaction commits. **Only after commit** does the service call
   `provider.create_order()` — no DB locks are held across provider I/O.
   Provider failure ⇒ attempt `FAILED(PROVIDER_ERROR)`, booking stays
   `HELD` (502, retryable). Duplicate checkout requests **reuse the active
   attempt** — no uncontrolled duplicates.
3. **Settlement** — `POST /api/payments/webhook/{provider}`: signature
   verified → normalized `WebhookEvent` → durable idempotency claim
   (`payment_webhook_events`, unique `(provider, event_id)`) → one short
   transaction that verifies **all** invariants (order maps to our attempt,
   attempt belongs to the booking, amount/currency match our stored record,
   booking still `HELD` within its hold window, seats still `HELD`) and
   then atomically flips `attempt → PAID`, `booking → CONFIRMED`,
   `seats → BOOKED`, clearing the hold expiry. Any invariant failure ⇒
   nothing is confirmed; the failure is recorded on the attempt/event.
4. **Expiry interaction** — if the hold expires before payment, cleanup
   cancels the booking and frees seats; a later success webhook sees a
   terminal booking, **never resurrects it**, and records
   `FAILED(BOOKING_TERMINAL)` for reconciliation.
5. **Retry** — `POST /api/payments/retry` creates a *new* attempt row for
   the same booking (history immutable: `#1 FAILED → #2 PENDING → …`).

  (`ProviderError` → clear 500, never a silent fallback).


### Idempotency guarantees

- `payment_webhook_events` enforces a DB unique constraint on
  `(provider, event_id)` — redeliveries (double delivery, process restart,
  concurrent workers) return `already_processed` without re-charging,
  re-confirming or duplicating attempts. Never an in-memory set.
- Payment-order creation reuses the active attempt keyed by the booking —
  double-clicks, refreshes and mobile retries cannot create uncontrolled
  duplicate attempts.
- Client recovery polls `GET /api/payments/status/{reference}` (backend
  truth) — the browser never trusts provider client callbacks alone, and
  never marks a booking successful from a client-side callback.

### Webhook security

- Razorpay signatures use the **official scheme**: hex HMAC-SHA256 of the
  raw request body keyed with `RAZORPAY_WEBHOOK_SECRET`
  (`compute_webhook_signature`); checkout callback signatures use
  HMAC-SHA256 of `order_id|payment_id` with `RAZORPAY_KEY_SECRET`.
  Comparison is constant-time (`hmac.compare_digest`).
- Signature failure → `401` before any parsing/persistence; unsigned,
  forged or unparseable payloads are never processed. Webhook amounts,
  statuses and provider ids are verified against our own PaymentAttempt
  rows before anything is confirmed.
- Secrets (`RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`, `JWT_SECRET`)
  are read from the environment only, never logged, never returned to JS —
  only the **public** key id may reach `checkout.key_id`.
- Frontend submits only `booking_reference`; amount, currency, provider ids
  and payment status are server-side only. No application path bypasses
  the payment service/state machine.

### Razorpay configuration (production)

```bash
APP_ENV=production
PAYMENT_PROVIDER=razorpay
RAZORPAY_KEY_ID=rzp_...          # public; safe to expose to checkout
RAZORPAY_KEY_SECRET=...          # server-only — NEVER commit
RAZORPAY_WEBHOOK_SECRET=...      # webhook HMAC secret — NEVER commit
```

Webhook URL: `POST /api/payments/webhook/razorpay` (raw body forwarded
unchanged so signature verification matches).
Phase 4 ships the adapter with deterministic, network-free order creation;
swapping `_synthesized_order_id` for the official SDK call is the only
Phase 5 change — the boundary and webhook verification stay identical.

### Local development & testing

- Mock mode needs nothing: `PAYMENT_PROVIDER=mock` (default; no Razorpay
  credentials required, local startup unchanged).
- Run the full suite (offline — no Razorpay network calls, mocked providers):

```bash
backend/.venv/Scripts/python -m pytest backend/tests -v
backend/.venv/Scripts/python check_assets.py
```

> **Mock payment is for development/testing only. Real Razorpay credentials
> must never be committed. Webhook signature verification is mandatory in
> production.**

## Phase 8 — Admin system & RBAC

### Server-side role model

- `users.role` is a DB-authoritative `USER | ADMIN` enum (migration
  `f3a91c2d7e44_phase8_admin_rbac`). The role is **not** in the JWT — every
  request re-reads it from the database, so demotions take effect immediately
  (demotion/deactivation also bumps `token_version`, invalidating all existing
  tokens for that user).
- `app/deps.py::require_admin` gates every `/api/admin/*` route:
  `401` for anonymous callers, `403` for authenticated non-admins.
  The frontend hides the Admin nav button unless `/api/me` reports
  `role == "ADMIN"` — that check is UX-only; the API never trusts it.

### Admin API namespace (all under `/api/admin`)

| Router | Endpoints |
| --- | --- |
| `admin/dashboard.py` | `GET /dashboard` — COUNT/SUM aggregations (users, shows, bookings, revenue, unread messages…) |
| `admin/users.py` | list/detail, `POST …/activate`, `POST …/deactivate`, `PATCH …/{id}` (role change) |
| `admin/movies*.py` | list/create/detail/patch (incl. `is_active` publish toggle) |
| `admin/venues*.py` | `GET/POST /cities`, `GET/POST/PATCH /theaters` |
| `admin/shows*.py` | list/create/detail/patch, `POST …/{id}/cancel` |
| `admin/bookings.py` | read-only list/detail (no mutation endpoints by design) |
| `admin/payments.py` | read-only `GET /payments`, `GET /payment-attempts/{id}` (webhook events; **no secrets/PII**) |
| `admin/reviews.py` | list + `DELETE /{id}` (moderation, audited) |
| `admin/ops.py` | contact messages (list/read-toggle), newsletter subscribers |
| `admin/audit_logs.py` | read-only, paginated `GET /audit-logs` |

Guard rails enforced server-side:

- **Last-admin protection** — the final active ADMIN cannot be demoted or
  deactivated; admins cannot demote/deactivate themselves.
- **Role/activation writes bump `token_version`** so the target's JWT dies at once.
- **Show mutation 409 guards** — shows with held/booked seats or active
  bookings reject update/cancel.
- **Pagination everywhere** — `page` (≥1) + `page_size` (≤100) with `items/total`.
- **Audit logging** — every mutating admin action stages an append-only row in
  `admin_audit_logs` in the *same* transaction as the mutation (a rolled-back
  mutation never leaves a false audit trail); secret-looking metadata keys are
  stripped by `admin/common.py::audit`.
- **Rate limits** — `admin_read` / `admin_write` / `admin_dashboard` buckets
  on top of the existing auth-limiter infrastructure.

### Bootstrap (no admin exists on a fresh DB)

```bash
# promote an existing user by e-mail (idempotent):
backend/.venv/Scripts/python backend/admin_bootstrap.py --email you@example.com
# or via env var:
ADMIN_EMAIL=you@example.com backend/.venv/Scripts/python backend/admin_bootstrap.py
```

The script never creates users or prints secrets; running it twice is safe.
Migration backfill assigns `USER` to all pre-existing accounts.

### Frontend console

- `js/features/admin.js` — tabbed Admin Console modal (dashboard cards,
  users table with search/pagination/activate-deactivate, movies publish
  toggle + create, shows cancel, bookings search, payments detail, review
  moderation, contact read-toggles, audit log).
- `js/api.js` exposes typed `admin*` wrappers over the single API door
  (`core/api-client.js`) — errors surface through `friendlyMessage()` like
  every other feature (401/403/409/422/429 all handled, double-submit
  guarded by `runExclusive`).
- All rendering uses `textContent`/`el()` (no `innerHTML` with API data);
  nav visibility flips from `onSessionChange` + a `/api/me` refresh.
- `css/admin.css` mirrors the existing profile/bookings modal pattern.



Issues identified during the Phase 2 security review that belong to later phases:

- **Registration reveals account existence** (`409` on duplicate email) — inherent to the current sign-up UX; login itself is uniform. A future auth phase could move to e-mail-verification flows.
- **Guest hold embeds account credentials** — `POST /api/bookings/hold` doubles as an implicit password check when the e-mail already exists (bounded by the hold rate limit). A future booking/auth redesign should separate guest checkout from account creation.
- **Rate limiter is per-process** — in-memory counters do not coordinate across multiple workers/instances and reset on restart; the `RateLimiter` seam is designed for a Redis-backed replacement in the production infrastructure phase.
- **No global request-body size cap** — individual Pydantic fields are bounded, but a reverse proxy should cap total body size in production.
- **Proxy-aware client IP** — rate limits key on the direct connection address; wire trusted forwarded headers when a reverse proxy is introduced.
- **Refunds UI, cancellation redesign, Redis, PostgreSQL tuning, CI/CD** — out of scope by design (later phases). Live Razorpay SDK order creation, refunds execution are Phase 5+; Phase 4 ships the provider boundary, webhook settlement and idempotency. **Admin/RBAC and review moderation shipped in Phase 8** (see above).
