
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def _load_dotenv() -> None:
    """Minimal .env loader (stdlib only) so backend/.env actually takes effect."""
    env_path = ROOT / "backend" / ".env"
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value

_load_dotenv()

def _normalize_database_url(url: str) -> str:
    """Resolve relative sqlite paths against backend/ so the DB location
    does not depend on the process working directory."""
    if url.startswith("sqlite:///./") or url.startswith("sqlite:///../"):
        rel = url.split("sqlite:///", 1)[1]
        abs_path = (ROOT / "backend" / rel).resolve()
        return f"sqlite:///{abs_path}"
    return url

@dataclass(frozen=True)
class Settings:
    database_url: str = _normalize_database_url(os.getenv(
        "DATABASE_URL", f"sqlite:///{ROOT / 'backend' / 'dhurandhar.db'}"
    ))
    jwt_secret: str = os.getenv("JWT_SECRET", "dev-only-change-me")
    payment_mode: str = os.getenv("PAYMENT_MODE", "mock")
    cors_origins: tuple[str, ...] = tuple(x.strip() for x in os.getenv(
        "CORS_ORIGINS", "http://localhost:5000,http://127.0.0.1:5000"
    ).split(",") if x.strip())

settings = Settings()
