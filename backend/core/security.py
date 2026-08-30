"""Password and session-token cryptography.

Passwords are stored as Argon2id hashes (never plaintext, never reversible).
Login session tokens are kept as SHA-256 digests, so a database leak does not
expose the live tokens themselves.

Behaviors worth knowing while debugging:
    * ``verify_password`` swallows every exception and returns ``False`` (a
      hash with a bad format or an algorithm mismatch must read as "wrong
      password", not crash the login endpoint).
    * ``new_session_token`` is the ONLY place a raw token is created; it is
      returned to the client once and only its hash is persisted.
"""

from __future__ import annotations

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Hash a plaintext password into its Argon2id string representation."""
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Return True only if the password matches the stored Argon2id hash."""
    try:
        return _password_hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        # Never let a malformed hash take the login endpoint down.
        return False


def new_session_token() -> str:
    """Generate a fresh, unguessable login token for the client to keep."""
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    """Hash a raw session token so only the digest is stored in the database."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()