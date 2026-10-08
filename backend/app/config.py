
import os
import secrets
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]

# Known placeholder secrets that must never be accepted anywhere. Development
# *ignores* them (an ephemeral secret is generated instead) and production
# refuses to start. This set is a denylist, never a usable value.
INSECURE_JWT_DEFAULTS = frozenset({
    "dev-only-change-me",
    "change-this-in-production",
})
# HS256 signs with HMAC-SHA256 (32-byte digest); accept nothing shorter than
# 32 characters of secret material. Generate real secrets with:
#   python -c "import secrets; print(secrets.token_urlsafe(48))"
MIN_JWT_SECRET_LENGTH = 32
ALLOWED_APP_ENVS = frozenset({"development", "production", "test"})
DEFAULT_DEV_CORS_ORIGINS = ("http://localhost:5000", "http://127.0.0.1:5000")
_ENV_KEYS = ("DATABASE_URL", "JWT_SECRET", "PAYMENT_MODE", "PAYMENT_PROVIDER", "CORS_ORIGINS", "APP_ENV",
             "RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET")


def _load_dotenv() -> None:
    """Minimal .env loader (stdlib only) so backend/.env actually takes effect.

    Real environment variables always win: keys already present in
    ``os.environ`` are never overwritten by ``backend/.env``.
    """
    env_path = ROOT / "backend" / ".env"
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key in _ENV_KEYS and key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()

DEFAULT_SQLITE_PATH = (ROOT / "backend" / "dhurandhar.db").resolve()


def _sqlite_url_for_path(path: Path) -> str:
    # ``Path.as_uri()`` yields ``file:///…`` which SQLAlchemy does not accept;
    # construct ``sqlite:///<abs path>`` with forward slashes instead so both
    # POSIX and Windows paths work.
    return f"sqlite:///{path.as_posix()}"


def _normalize_database_url(url: str) -> str:
    """Resolve sqlite paths portably; pass PostgreSQL URLs through unchanged."""
    url = (url or "").strip()
    if not url:
        return _sqlite_url_for_path(DEFAULT_SQLITE_PATH)
    if url.startswith("postgres://"):
        # SQLAlchemy ≥1.4 requires the ``postgresql://`` scheme; Render and
        # some providers still emit ``postgres://``.
        url = "postgresql://" + url[len("postgres://"):]
    # SQLAlchemy 2.0 / psycopg3 driver: prefix must carry +psycopg.
    if url.startswith("postgresql://") and "+psycopg" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("sqlite:"):
        # Bare ``sqlite:///relative.db``, ``sqlite:///./x.db`` and absolute
        # paths (POSIX or Windows drive) are all accepted.
        prefix = "sqlite:///"
        rest = url[len(prefix):] if url.startswith(prefix) else url[len("sqlite:"):]
        rest = rest.strip()
        if rest.startswith("./") or rest.startswith("../"):
            abs_path = (ROOT / "backend" / unquote(rest)).resolve()
            return _sqlite_url_for_path(abs_path)
        if rest.startswith("/") or (len(rest) >= 2 and rest[1] == ":") or rest.startswith("\\"):
            return f"sqlite:///{Path(unquote(rest)).as_posix()}"
        abs_path = (ROOT / "backend" / unquote(rest or "dhurandhar.db")).resolve()
        return _sqlite_url_for_path(abs_path)
    return url

def _resolve_app_env(raw: str | None) -> str:
    value = (raw or "development").strip().lower()
    if value not in ALLOWED_APP_ENVS:
        raise RuntimeError(
            f"Invalid APP_ENV {value!r}; expected one of: "
            + ", ".join(sorted(ALLOWED_APP_ENVS))
        )
    return value


def _resolve_jwt_secret(app_env: str, raw: str | None) -> str:
    """Apply the JWT secret policy for the configured environment.

    * development/test + missing/insecure/short secret -> an ephemeral random
      secret is generated for this process (sessions do not survive a
      restart) and a loud warning identifies it as development-only.
    * production + missing/insecure/short secret -> ``RuntimeError`` at
      configuration time so the application fails fast instead of silently
      signing tokens with a publicly known value.
    """
    candidate = (raw or "").strip()
    missing = not candidate
    placeholder = candidate in INSECURE_JWT_DEFAULTS
    too_short = len(candidate) < MIN_JWT_SECRET_LENGTH
    if app_env == "production":
        if missing:
            raise RuntimeError(
                "APP_ENV=production requires JWT_SECRET to be set. Generate one with: "
                "python -c \"import secrets; print(secrets.token_urlsafe(48))\""
            )
        if placeholder:
            raise RuntimeError(
                "APP_ENV=production refuses a known placeholder JWT_SECRET. Generate a "
                "real secret: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
            )
        if too_short:
            raise RuntimeError(
                f"APP_ENV=production requires JWT_SECRET to be at least "
                f"{MIN_JWT_SECRET_LENGTH} characters long."
            )
        return candidate
    if not (missing or placeholder or too_short):
        return candidate
    warnings.warn(
        "JWT_SECRET is missing or insecure; using a development-only ephemeral "
        "secret for this process (sessions reset on restart). Set a real JWT_SECRET "
        "of at least 32 characters for any shared/production deployment; generate "
        "one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\"",
        UserWarning,
        stacklevel=2,
    )
    return secrets.token_urlsafe(48)


def _resolve_cors_origins(app_env: str, raw: str | None) -> tuple[str, ...]:
    """Resolve allowed browser origins.

    Unset -> localhost origins in development/test (the local workflow keeps
    working), and no cross-origin access at all in production (the frontend
    is served by this same process, so CORS is only needed when a separate
    frontend origin is configured explicitly).
    """
    if raw is None:
        return () if app_env == "production" else DEFAULT_DEV_CORS_ORIGINS
    origins = tuple(part.strip() for part in raw.split(",") if part.strip())
    if any(origin == "*" for origin in origins):
        raise RuntimeError(
            "CORS_ORIGINS must not contain '*' because credentialed CORS is enabled; "
            "list explicit origins instead."
        )
    for origin in origins:
        rest = origin.split("://", 1)[1] if "://" in origin else ""
        if not origin.startswith(("http://", "https://")) or " " in origin or "/" in rest or "?" in rest or "#" in rest:
            raise RuntimeError(
                f"Invalid CORS origin {origin!r}; expected scheme://host[:port] "
                "with no path (e.g. https://example.com)."
            )
    return origins


def _resolve_payment_provider(raw: str | None, legacy_mode: str | None) -> str:
    """Resolve the active payment provider name.

    ``PAYMENT_PROVIDER`` is authoritative; ``PAYMENT_MODE`` is kept as a
    legacy alias so existing ``PAYMENT_MODE=mock`` deployments keep working.
    """
    value = (raw or "").strip().lower()
    if not value:
        value = (legacy_mode or "mock").strip().lower()
    if value not in ("mock", "razorpay"):
        raise RuntimeError(
            f"Unknown payment provider {value!r}; expected 'mock' or 'razorpay'."
        )
    return value


@dataclass(frozen=True)
class Settings:
    # Fields resolve from the environment at instantiation time so the policy
    # above can be exercised directly in tests via ``Settings()``.
    app_env: str = field(default_factory=lambda: _resolve_app_env(os.getenv("APP_ENV")))
    database_url: str = field(default_factory=lambda: _normalize_database_url(os.getenv(
        "DATABASE_URL", _sqlite_url_for_path(DEFAULT_SQLITE_PATH)
    )))
    payment_mode: str = field(default_factory=lambda: os.getenv("PAYMENT_MODE", "mock"))
    payment_provider: str = field(default_factory=lambda: _resolve_payment_provider(
        os.getenv("PAYMENT_PROVIDER"), os.getenv("PAYMENT_MODE", "mock")
    ))
    razorpay_key_id: str = field(default_factory=lambda: (os.getenv("RAZORPAY_KEY_ID", "") or "").strip())
    razorpay_key_secret: str = field(default_factory=lambda: (os.getenv("RAZORPAY_KEY_SECRET", "") or "").strip())
    razorpay_webhook_secret: str = field(default_factory=lambda: (os.getenv("RAZORPAY_WEBHOOK_SECRET", "") or "").strip())
    cors_origins: tuple[str, ...] = field(default_factory=lambda: _resolve_cors_origins(
        _resolve_app_env(os.getenv("APP_ENV")), os.getenv("CORS_ORIGINS")
    ))
    jwt_secret: str = field(default_factory=lambda: _resolve_jwt_secret(
        _resolve_app_env(os.getenv("APP_ENV")), os.getenv("JWT_SECRET")
    ))

    # ---------------------------------------------------------------------------
    # PostgreSQL connection pooling (conservative; SQLite always bypasses these).
    #
    # Read from the environment at Settings instantiation time so each deployment
    # (Render, Docker, local dev) can override them. ``db_pool_size`` and
    # ``db_max_overflow`` bound the total simultaneous connections; Render
    # free-tier Postgres commonly exposes only a handful of connections, so the
    # defaults are deliberately small. ``db_connect_timeout`` guards the initial
    # handshake, and ``db_pool_recycle`` closes long-lived connections before
    # they become stale or are rejected by the database (rotation, idle limits).
    # ---------------------------------------------------------------------------
    db_pool_size: int = field(default_factory=lambda: int(os.getenv("DB_POOL_SIZE", "5")))
    db_max_overflow: int = field(default_factory=lambda: int(os.getenv("DB_MAX_OVERFLOW", "5")))
    db_pool_timeout: int = field(default_factory=lambda: int(os.getenv("DB_POOL_TIMEOUT", "30")))
    db_pool_recycle: int = field(default_factory=lambda: int(os.getenv("DB_POOL_RECYCLE", "1800")))
    db_connect_timeout: int = field(default_factory=lambda: int(os.getenv("DB_CONNECT_TIMEOUT", "10")))

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


settings = Settings()
