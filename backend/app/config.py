
import os
import warnings
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]

DEV_JWT_DEFAULT = "dev-only-change-me"
# Placeholders that must never be treated as production secrets. The
# ``change-this-in-production`` value ships in ``.env.example``/compose files
# as an obvious placeholder; both values only silence the warning when a real
# JWT_SECRET is provided via environment or ``backend/.env``.
INSECURE_JWT_DEFAULTS = frozenset({"dev-only-change-me", "change-this-in-production"})
_ENV_KEYS = ("DATABASE_URL", "JWT_SECRET", "PAYMENT_MODE", "CORS_ORIGINS")


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

@dataclass(frozen=True)
class Settings:
    database_url: str = _normalize_database_url(os.getenv(
        "DATABASE_URL", _sqlite_url_for_path(DEFAULT_SQLITE_PATH)
    ))
    jwt_secret: str = os.getenv("JWT_SECRET", DEV_JWT_DEFAULT)
    payment_mode: str = os.getenv("PAYMENT_MODE", "mock")
    cors_origins: tuple[str, ...] = tuple(x.strip() for x in os.getenv(
        "CORS_ORIGINS", "http://localhost:5000,http://127.0.0.1:5000"
    ).split(",") if x.strip())

    @property
    def has_dev_jwt_default(self) -> bool:
        return self.jwt_secret in INSECURE_JWT_DEFAULTS


settings = Settings()

if settings.has_dev_jwt_default:
    # Development-only fallback. Production must set JWT_SECRET; the full
    # Phase 2 JWT hardening policy is intentionally out of scope here.
    warnings.warn(
        "JWT_SECRET is using a development placeholder; set JWT_SECRET in "
        "backend/.env or the environment for any shared/production deployment.",
        UserWarning,
        stacklevel=2,
    )
