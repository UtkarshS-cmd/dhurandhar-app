
import base64, hashlib, hmac, os, re, secrets
from datetime import datetime, timedelta, timezone
import jwt
from .config import settings

# The only algorithm tokens are signed with and accepted under. HS256 is kept
# deliberately: there is one shared server secret and no key-rotation infra.
ALGORITHM = "HS256"
TOKEN_TTL = timedelta(hours=12)

# Booking references are public capability identifiers (guest checkout can
# look up/confirm a booking with only the reference), so they carry a lot of
# entropy: 18 random bytes -> 24 url-safe base64 characters (144 bits).
BOOKING_REFERENCE_PREFIX = "DHR-"
BOOKING_REFERENCE_RANDOM_CHARS = 24
BOOKING_REFERENCE_RE = re.compile(
    r"^" + re.escape(BOOKING_REFERENCE_PREFIX) + r"[A-Za-z0-9_-]{" + str(BOOKING_REFERENCE_RANDOM_CHARS) + r"}$"
)
# References written before Phase 2 used 8 hex characters. They remain
# readable (never writable) so existing development records still resolve.
LEGACY_BOOKING_REFERENCE_RE = re.compile(r"^DHR-[0-9A-Fa-f]{8}$")


class AuthenticationError(Exception):
    """Any authentication failure.

    Deliberately carries no details to the API layer: callers translate it
    into a bare ``401 Unauthorized`` so token/database/configuration
    internals are never exposed to clients.
    """


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return "scrypt$" + base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(digest).decode()

def verify_password(password: str, encoded: str) -> bool:
    try:
        _, salt_b64, digest_b64 = encoded.split("$", 2)
        salt = base64.urlsafe_b64decode(salt_b64.encode())
        expected = base64.urlsafe_b64decode(digest_b64.encode())
        actual = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False

def create_token(user_id: int, token_version: int | None = None) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "iat": now, "exp": now + TOKEN_TTL}
    if token_version is not None:
        payload["ver"] = int(token_version)
    return jwt.encode(
        payload,
        settings.jwt_secret,
        algorithm=ALGORITHM,
    )


def decode_token_payload(token: str) -> dict:
    """Validate a bearer token and return the verified payload."""
    if not isinstance(token, str) or not token:
        raise AuthenticationError("empty token")
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[ALGORITHM],
            options={"require": ["sub", "exp"]},
        )
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, ValueError, TypeError, KeyError):
        raise AuthenticationError("invalid token") from None
    if user_id < 1:
        raise AuthenticationError("invalid subject")
    return payload


def decode_token(token: str) -> int:
    """Validate a bearer token and return the user id.

    Raises ``AuthenticationError`` for *any* malformed, expired, tampered,
    wrongly-signed or claim-invalid token. Only HS256 is accepted (algorithm
    confusion is impossible), ``sub``/``exp`` are mandatory and ``sub`` must
    be a positive integer. PyJWT exception types never leave this function.
    """
    payload = decode_token_payload(token)
    user_id = int(payload["sub"])
    if user_id < 1:
        raise AuthenticationError("invalid subject")
    return user_id


def generate_booking_reference() -> str:
    """Cryptographically random, unguessable booking reference."""
    return BOOKING_REFERENCE_PREFIX + secrets.token_urlsafe(
        # token_urlsafe(n) emits ceil(n/3)*4 characters; 18 bytes -> exactly 24.
        BOOKING_REFERENCE_RANDOM_CHARS * 3 // 4
    )

def is_valid_booking_reference(reference: str) -> bool:
    """Shape check before touching the database.

    Uniformly rejects malformed references so probing cannot distinguish
    "badly formed" from "does not exist" (both map to 404 upstream).
    """
    if not isinstance(reference, str):
        return False
    return bool(BOOKING_REFERENCE_RE.match(reference) or LEGACY_BOOKING_REFERENCE_RE.match(reference))
