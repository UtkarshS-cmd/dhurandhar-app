# Dhurandhar Cinema — single-image full-stack deployment.
# Serves the static frontend (index.html, css/, js/, assets/) and the
# FastAPI backend from one process. Works locally, on Docker, Render, Fly, etc.
FROM python:3.12-slim

# Production configuration policy: APP_ENV=production makes the app fail
# fast at startup unless a real JWT_SECRET (>= 32 chars) is provided at run
# time. Generate one with:
#   python -c "import secrets; print(secrets.token_urlsafe(48))"
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ENV=production

WORKDIR /app

# Install dependencies first so Docker layer caching skips it when only
# application code changes.
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

# Application source: backend/ + frontend files at the repo root.
COPY . .

WORKDIR /app/backend

EXPOSE 5000

# Migrate, seed (idempotent), then serve. $PORT is respected for cloud hosts.
CMD ["sh", "-c", "alembic upgrade head && python seed.py && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-5000}"]
