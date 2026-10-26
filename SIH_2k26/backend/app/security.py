import hashlib
import hmac
import secrets
from datetime import UTC, datetime

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()  # Argon2id with library defaults

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128

# Verified against when the account does not exist, so response time does not reveal it.
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing-equalisation")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def tokens_match(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def as_utc(dt: datetime) -> datetime:
    """SQLite returns naive datetimes; PostgreSQL returns aware ones. Normalise."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def normalise_email(email: str) -> str:
    return email.strip().lower()
