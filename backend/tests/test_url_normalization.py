"""Unit tests for PostgreSQL / SQLite URL normalization.

Covers the four supported forms and guards against double-nesting the
``postgresql+psycopg://`` driver prefix. SQLite URLs keep their scheme.
"""

from app.config import _normalize_database_url


def _norm(url: str) -> str:
    return _normalize_database_url(url)


def test_sqlite_url_keeps_scheme():
    # SQLite is never turned into a PostgreSQL driver; only the scheme rules apply.
    out = _norm("sqlite:///backend/dhurandhar.db")
    assert out.startswith("sqlite:///")
    assert "+psycopg" not in out


def test_postgres_scheme_is_normalized_to_postgresql():
    # postgres:// -> postgresql:// (SQLAlchemy requirement) -> postgresql+psycopg:// (driver)
    assert _norm("postgres://user:pass@host:5432/dbhurandhar") == "postgresql+psycopg://user:pass@host:5432/dbhurandhar"


def test_postgresql_scheme_runs_psycopg3_driver():
    assert _norm("postgresql://user:pass@host:5432/db") == "postgresql+psycopg://user:pass@host:5432/db"


def test_postgresql_psycopg_url_is_never_double_prefixed():
    url = "postgresql+psycopg://user:pass@host:5432/db"
    assert _norm(url) == url


def test_postgresql_query_string_is_preserved():
    url = "postgresql+psycopg://user:pass@host:5432/db?sslmode=require"
    assert _norm(url) == url
