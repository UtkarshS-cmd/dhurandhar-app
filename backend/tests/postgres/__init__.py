"""PostgreSQL integration suite (Phase 9.1) — requires a live PostgreSQL.

Run against a throwaway database only, e.g.::

    docker compose --profile postgres up -d
    $env:POSTGRES_TEST_DATABASE_URL = "postgresql+psycopg://dhurandhar:dhurandhar-dev-pw@localhost:5432/dhurandhar_test"
    pytest -q backend/tests/postgres

The suite fails fast with a clear error when POSTGRES_TEST_DATABASE_URL is
missing — it never silently falls back to SQLite (a Postgres test running on
SQLite would prove nothing). The schema always comes from ``alembic upgrade
head`` against the empty test database, never ``Base.metadata.create_all``.
"""
