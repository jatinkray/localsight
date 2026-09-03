"""JWT access/refresh tokens with refresh-token rotation.

Design:
  * Access tokens are short-lived (default 15 min) and carry the user's roles
    and the *effective* permission set so the API can enforce RBAC without a
    per-request DB join for permissions.
  * Refresh tokens are longer-lived (default 7 days), carry a `jti`, and are
    subject to rotation: every refresh issues a brand-new pair and revokes the
    previously issued refresh token (replay of an old refresh is rejected).
  * HS256 by default. For production, rotate to RS256 with a short-lived
    signing key and a pinned public key (see docs/security).
"""
from __future__ import annotations

import datetime as dt
import uuid

import jwt

from packages.security.errors import AuthError

ALGORITHM = "HS256"
ISSUER = "localsight"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def create_access_token(
    subject: str,
    roles: list[str],
    permissions: list[str],
    secret: str,
    ttl_minutes: int = 15,
    jti: str | None = None,
) -> str:
    now = _now()
    payload = {
        "sub": subject,
        "type": "access",
        "jti": jti or uuid.uuid4().hex,
        "roles": roles,
        "permissions": permissions,
        "iat": int(now.timestamp()),
        "exp": int((now + dt.timedelta(minutes=ttl_minutes)).timestamp()),
        "iss": ISSUER,
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def create_refresh_token(
    subject: str,
    secret: str,
    ttl_days: int = 7,
    jti: str | None = None,
) -> str:
    now = _now()
    payload = {
        "sub": subject,
        "type": "refresh",
        "jti": jti or uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + dt.timedelta(days=ttl_days)).timestamp()),
        "iss": ISSUER,
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def decode_token(token: str, secret: str, expected_type: str) -> dict:
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            issuer=ISSUER,
            options={"require": ["exp", "sub", "type", "jti"]},
        )
    except jwt.PyJWTError as exc:  # expired, invalid signature, bad Issuer, etc.
        raise AuthError(f"invalid token: {exc}") from exc
    if claims.get("type") != expected_type:
        raise AuthError(f"expected {expected_type} token")
    return claims
