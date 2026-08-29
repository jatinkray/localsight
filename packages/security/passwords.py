"""Password hashing with Argon2id (the current OWASP-recommended default).

We use argon2-cffi's PasswordHasher, which defaults to Argon2id and handles
salt, parallelism, memory and iteration tuning for us. Plaintext passwords are
never stored or logged.
"""
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

# One hasher instance; defaults: Argon2id, m=2**16 KiB, t=2, p=1 (OWASP-aligned).
_ph = PasswordHasher()


def hash_password(plain: str) -> str:
    return _ph.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, plain)
    except (VerifyMismatchError, InvalidHashError, TypeError):
        return False


def needs_rehash(hashed: str) -> bool:
    """True when the hash parameters are stale and should be upgraded."""
    try:
        return _ph.check_needs_rehash(hashed)
    except InvalidHashError:
        return True
