"""Admin user management (user:manage). RBAC roles are assigned here; ordinary
operators cannot grant themselves privileged roles because the action requires
user:manage and is audited.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.audit import write_audit
from apps.api.bootstrap import Runtime
from apps.api.dependencies import get_db, get_runtime, require_permission
from packages.domain.models import RefreshToken, Role, User
from packages.domain.timeutil import iso
from packages.security.passwords import hash_password
from packages.security.rbac import VALID_ROLES

router = APIRouter(prefix="/api/users", tags=["users"])


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = ""
    password: str = Field(min_length=12)
    role: str = "VIEWER"


@router.get("", dependencies=[Depends(require_permission("user:manage"))])
def list_users(db: Session = Depends(get_db)):
    rows = db.execute(select(User).order_by(User.email)).scalars().all()
    out = []
    for u in rows:
        role = db.get(Role, u.role_id)
        out.append({"id": u.id, "email": u.email, "full_name": u.full_name,
                    "role": role.name if role else "?", "is_active": u.is_active,
                    "mfa_enabled": u.mfa_enabled})
    return out


@router.post("", dependencies=[Depends(require_permission("user:manage"))])
def create_user(body: UserCreate, request: Request, db: Session = Depends(get_db), rt: Runtime = Depends(get_runtime)):
    if body.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"invalid role; choose from {sorted(VALID_ROLES)}")
    if db.execute(select(User).where(User.email == body.email)).first():
        raise HTTPException(status_code=409, detail="email already exists")
    if len(body.password) < 12:
        raise HTTPException(status_code=400, detail="password must be at least 12 characters")
    role = db.query(Role).filter(Role.name == body.role).first()
    user = User(email=body.email, full_name=body.full_name,
                password_hash=hash_password(body.password), role_id=role.id)
    db.add(user)
    db.flush()
    write_audit(db, user=request.state.user, action="user.create", resource=user.id,
                request_id=getattr(request.state, "request_id", "-"),
                detail={"role": body.role})
    db.commit()
    return {"id": user.id, "email": user.email, "role": body.role}


@router.delete("/{user_id}", dependencies=[Depends(require_permission("user:manage"))])
def delete_user(user_id: str, request: Request, db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="user not found")
    db.delete(user)
    write_audit(db, user=request.state.user, action="user.delete", resource=user_id,
                request_id=getattr(request.state, "request_id", "-"))
    db.commit()
    return {"ok": True}


@router.get("/{user_id}/sessions", dependencies=[Depends(require_permission("user:manage"))])
def user_sessions(user_id: str, request: Request, db: Session = Depends(get_db)):
    """Admin view of a user's active sessions (M2/E-13)."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="user not found")
    rows = db.query(RefreshToken).filter(
        RefreshToken.user_id == user_id,
        RefreshToken.revoked.is_(False),
        RefreshToken.expires_at > dt.datetime.now(dt.UTC),
    ).order_by(RefreshToken.created_at.desc()).all()
    return {"sessions": [
        {"id": r.id, "created_at": iso(r.created_at), "expires_at": iso(r.expires_at)}
        for r in rows
    ]}


@router.post("/{user_id}/sessions/revoke-all",
             dependencies=[Depends(require_permission("user:manage"))])
def revoke_user_sessions(user_id: str, request: Request, db: Session = Depends(get_db)):
    """Admin: revoke every active session of one user (M2/E-13)."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="user not found")
    n = db.query(RefreshToken).filter(
        RefreshToken.user_id == user_id, RefreshToken.revoked.is_(False)
    ).update({"revoked": True})
    write_audit(db, user=request.state.user, action="user.sessions_revoke_all",
                resource=user.email, request_id=getattr(request.state, "request_id", "-"),
                detail={"revoked": n})
    db.commit()
    return {"ok": True, "revoked": n}


@router.post("/{user_id}/mfa-reset", dependencies=[Depends(require_permission("user:manage"))])
def reset_user_mfa(user_id: str, request: Request, db: Session = Depends(get_db),
                   rt: Runtime = Depends(get_runtime)):
    """Admin-initiated MFA reset (M2/E-5): clears the secret so the user can
    re-enroll. Used when a device is lost. Audited — this is a security event."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="user not found")
    if not user.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA is not enabled for this user")
    user.mfa_enabled = False
    user.mfa_secret_enc = None
    write_audit(db, user=request.state.user, action="user.mfa_reset",
                resource=user.email, request_id=getattr(request.state, "request_id", "-"))
    db.commit()
    return {"ok": True}
