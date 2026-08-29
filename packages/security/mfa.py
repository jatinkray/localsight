"""TOTP multi-factor authentication (RFC 6238), implemented with stdlib only.

We avoid an extra dependency by implementing HMAC-based OTP directly. Secret is
a base32-encoded 20-byte value. Operators enroll by scanning the otpauth URI and
then must supply a valid code at login when MFA is enabled.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
import urllib.parse

_T0 = 0
_STEP = 30
_DIGITS = 6


def generate_secret() -> str:
    """Return a base32 (no padding) secret suitable for TOTP."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _int_to_b32(secret_b32: str) -> bytes:
    # tolerate missing padding
    pad = "=" * (-len(secret_b32) % 8)
    return base64.b32decode(secret_b32 + pad)


def _hotp(secret_bytes: bytes, counter: int) -> int:
    msg = struct.pack(">Q", counter)
    h = hmac.new(secret_bytes, msg, hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    binary = struct.unpack(">I", h[offset : offset + 4])[0] & 0x7FFFFFFF
    return binary % (10**_DIGITS)


def verify_code(secret_b32: str, code: str, *, window: int = 1) -> bool:
    secret_bytes = _int_to_b32(secret_b32)
    counter = int((time.time() - _T0) / _STEP)
    code = (code or "").strip()
    if not code.isdigit() or len(code) != _DIGITS:
        return False
    for c in range(counter - window, counter + window + 1):
        if hmac.compare_digest(str(_hotp(secret_bytes, c)).zfill(_DIGITS), code):
            return True
    return False


def current_code(secret_b32: str, when: float | None = None) -> str:
    """Return the current valid TOTP code (used by clients / tests)."""
    secret_bytes = _int_to_b32(secret_b32)
    counter = int(((when or time.time()) - _T0) / _STEP)
    return str(_hotp(secret_bytes, counter)).zfill(_DIGITS)


def provisioning_uri(secret_b32: str, account: str, issuer: str = "LocalVision") -> str:
    label = urllib.parse.quote(f"{issuer}:{account}")
    return (
        f"otpauth://totp/{label}"
        f"?secret={secret_b32}&issuer={urllib.parse.quote(issuer)}&period={_STEP}&digits={_DIGITS}"
    )
