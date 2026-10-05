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

The local development database defaults to SQLite because PostgreSQL is not assumed to be installed. Set `DATABASE_URL` in `.env` to a PostgreSQL SQLAlchemy URL for deployment. Real environment variables always take precedence over `backend/.env`; the `JWT_SECRET` in `.env.example` is a local-development placeholder only and must be replaced for any shared/production deployment.

## Testing

Run the automated baseline suite from the repository root (no manually running server required; tests use an isolated temporary SQLite database):

```bash
backend/.venv/Scripts/python -m pytest backend/tests/test_api.py -v   # Windows
# ./backend/.venv/bin/python -m pytest backend/tests/test_api.py -v  # macOS/Linux
```

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
