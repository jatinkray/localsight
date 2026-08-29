"""Authentication: login (with MFA + lockout), refresh-token rotation, logout,
and TOTP enrollment. Access tokens are short-lived; refresh tokens rotate and
are tracked server-side so a stolen refresh can be revoked.
"""
from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from apps.api.audit import write_audit
from apps.api.bootstrap import Runtime
from apps.api.dependencies import get_current_user, get_db, get_runtime, rate_limit
from packages.domain.models import RefreshToken, Role, User
from packages.security.errors import AuthError
from packages.security.jwt import create_access_token, create_refresh_token, decode_token
from packages.security.mfa import generate_secret, provisioning_uri, verify_code
from packages.security.passwords import hash_password, verify_password
from packages.security.rbac import effective_permissions

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    email: str
    password: str
    mfa_code: str | None = None


class TokenBody(BaseModel):
    refresh_token: str


class MfaSetupOut(BaseModel):
    secret: str
    otpauth_uri: str


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[0].strip() or (request.client.host if request.client else "unknown"))


def _permissions_for(db: Session, user: User) -> list[str]:
    role = db.get(Role, user.role_id)
    return sorted(effective_permissions([role.name]))


@router.post("/login", dependencies=[Depends(rate_limit("login", rate=1.0, capacity=10))])
def login(body: LoginBody, request: Request, db: Session = Depends(get_db), rt: Runtime = Depends(get_runtime)):
    rid = getattr(request.state, "request_id", "-")
    ip = _client_ip(request)
    user = db.query(User).filter(User.email == body.email).first()

    # Always run a password check path to reduce user-enumeration timing. If no
    # user, verify against a dummy hash so timing is similar.
    dummy = hash_password("dummy-password-not-used")
    ok = verify_password(body.password, user.password_hash if user else dummy) if user else False

    if user and user.locked_until is not None:
        lu = user.locked_until
        if lu.tzinfo is None:
            lu = lu.replace(tzinfo=dt.timezone.utc)
        if lu > dt.datetime.now(dt.timezone.utc):
            write_audit(db, username=body.email, action="login", result="failure", source_ip=ip,
                        request_id=rid, detail={"reason": "locked"})
            db.commit()
            raise HTTPException(status_code=status.HTTP_423_LOCKED, detail="account locked; try later")

    if not user or not ok:
        if user:
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= rt.settings.max_login_attempts:
                user.locked_until = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=rt.settings.lockout_minutes)
                user.failed_login_attempts = 0
            db.commit()
        write_audit(db, username=body.email, action="login", result="failure", source_ip=ip,
                    request_id=rid, detail={"reason": "bad_credentials"})
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")

    # MFA step-up
    if user.mfa_enabled:
        if not body.mfa_code or not _verify_user_mfa(rt, user, body.mfa_code):
            write_audit(db, user=user, action="login", result="failure", source_ip=ip,
                        request_id=rid, detail={"reason": "mfa_failed"})
            db.commit()
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid MFA code")
        user.failed_login_attempts = 0
        user.locked_until = None

    perms = _permissions_for(db, user)
    access = create_access_token(user.id, [user.role.name], perms, rt.settings.jwt_secret, rt.settings.access_token_ttl_min)
    refresh = _issue_refresh(db, user, rt)
    write_audit(db, user=user, action="login", result="success", source_ip=ip, request_id=rid)
    db.commit()
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": rt.settings.access_token_ttl_min * 60,
    }


def _verify_user_mfa(rt: Runtime, user: User, code: str) -> bool:
    if not user.mfa_secret_enc:
        return False
    secret = rt.crypto.decrypt_str(user.mfa_secret_enc)
    return verify_code(secret, code)


def _issue_refresh(db: Session, user: User, rt: Runtime) -> str:
    token = create_refresh_token(user.id, rt.settings.jwt_secret, rt.settings.refresh_token_ttl_days)
    claims = decode_token(token, rt.settings.jwt_secret, "refresh")
    db.add(RefreshToken(
        user_id=user.id,
        jti=claims["jti"],
        expires_at=dt.datetime.fromtimestamp(claims["exp"], dt.timezone.utc),
    ))
    db.flush()
    return token


@router.post("/refresh", dependencies=[Depends(rate_limit("refresh", rate=2.0, capacity=20))])
def refresh(body: TokenBody, request: Request, db: Session = Depends(get_db), rt: Runtime = Depends(get_runtime)):
    rid = getattr(request.state, "request_id", "-")
    ip = _client_ip(request)
    try:
        claims = decode_token(body.refresh_token, rt.settings.jwt_secret, "refresh")
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    record = db.query(RefreshToken).filter(RefreshToken.jti == claims["jti"]).first()
    if not record or record.revoked or record.replaced_by:
        write_audit(db, username=claims.get("sub", ""), action="token_refresh", result="failure",
                    source_ip=ip, request_id=rid, detail={"reason": "revoked_or_replay"})
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="refresh token invalid")

    user = db.get(User, claims["sub"])
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user inactive")

    # Rotate: revoke the presented refresh and issue a new pair.
    new_refresh = create_refresh_token(user.id, rt.settings.jwt_secret, rt.settings.refresh_token_ttl_days)
    new_claims = decode_token(new_refresh, rt.settings.jwt_secret, "refresh")
    record.revoked = True
    record.replaced_by = new_claims["jti"]
    db.add(RefreshToken(user_id=user.id, jti=new_claims["jti"],
                        expires_at=dt.datetime.fromtimestamp(new_claims["exp"], dt.timezone.utc)))
    perms = _permissions_for(db, user)
    access = create_access_token(user.id, [user.role.name], perms, rt.settings.jwt_secret, rt.settings.access_token_ttl_min)
    write_audit(db, user=user, action="token_refresh", result="success", source_ip=ip, request_id=rid)
    db.commit()
    return {
        "access_token": access,
        "refresh_token": new_refresh,
        "token_type": "bearer",
        "expires_in": rt.settings.access_token_ttl_min * 60,
    }


@router.post("/logout")
def logout(body: TokenBody, request: Request, db: Session = Depends(get_db), rt: Runtime = Depends(get_runtime)):
    rid = getattr(request.state, "request_id", "-")
    ip = _client_ip(request)
    try:
        claims = decode_token(body.refresh_token, rt.settings.jwt_secret, "refresh")
    except AuthError:
        return {"ok": True}
    record = db.query(RefreshToken).filter(RefreshToken.jti == claims["jti"]).first()
    if record:
        record.revoked = True
    write_audit(db, username=claims.get("sub", ""), action="logout", result="success", source_ip=ip, request_id=rid)
    db.commit()
    return {"ok": True}


@router.get("/me")
def me(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    role = db.get(Role, user.role_id)
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": role.name,
        "is_active": user.is_active,
        "mfa_enabled": user.mfa_enabled,
        "permissions": sorted(effective_permissions([role.name])),
    }


@router.post("/mfa/setup", response_model=MfaSetupOut)
def mfa_setup(request: Request, db: Session = Depends(get_db), rt: Runtime = Depends(get_runtime), user: User = Depends(get_current_user)):
    secret = generate_secret()
    user.mfa_secret_enc = rt.crypto.encrypt_str(secret)
    user.mfa_enabled = False  # enabled only after a successful verify
    db.commit()
    return MfaSetupOut(secret=secret, otpauth_uri=provisioning_uri(secret, user.email))


@router.post("/mfa/verify")
def mfa_verify(body: dict, request: Request, db: Session = Depends(get_db), rt: Runtime = Depends(get_runtime), user: User = Depends(get_current_user)):
    code = (body or {}).get("code")
    if not user.mfa_secret_enc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MFA not initialized")
    secret = rt.crypto.decrypt_str(user.mfa_secret_enc)
    if not verify_code(secret, code or ""):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid code")
    user.mfa_enabled = True
    db.commit()
    return {"ok": True, "mfa_enabled": True}
