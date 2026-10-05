"""Password hashing, opaque-token generation and CSRF handling.

Secrets never touch the database in plaintext: sessions, API tokens and worker
credentials are stored as SHA-256 hashes only. Worker and user secrets are
*opaque* random strings rather than JWTs, so they can be revoked server-side
(contract section 9).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from passlib.context import CryptContext

# bcrypt with a high cost factor; 4.0.1 is pinned in pyproject because passlib
# 1.7.4 reads `__about__.__version__` from bcrypt >= 4.1 and breaks.
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)


def hash_password(password: str) -> str:
    """Hash a user password."""
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    return _pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time-ish verification; never raises on a malformed hash."""
    try:
        return _pwd_context.verify(password, password_hash)
    except (ValueError, TypeError):
        return False


def generate_opaque_token(nbytes: int = 32) -> str:
    """Cryptographically random token (URL-safe)."""
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    """SHA-256 hex digest of an opaque token — safe to store and to index."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_token(token: str, token_hash: str) -> bool:
    """Constant-time comparison of a presented token against a stored hash."""
    return hmac.compare_digest(hash_token(token), token_hash)


def token_prefix(token: str, length: int = 8) -> str:
    """Non-secret prefix shown in the UI so a token can be identified later."""
    return token[:length]


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(24)


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)
