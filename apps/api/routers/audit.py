"""Audit log read API (audit:view). Immutable-style records; never modified."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from apps.api.bootstrap import Runtime
from apps.api.dependencies import get_db, get_runtime, require_permission
from packages.domain.models import AuditLog

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("", dependencies=[Depends(require_permission("audit:view"))])
def list_audit(
    request: Request,
    db: Session = Depends(get_db),
    action: str | None = None,
    result: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int = Query(100, le=500, ge=1),
    offset: int = Query(0, ge=0),
):
    q = select(AuditLog)
    if action:
        q = q.where(AuditLog.action == action)
    if result:
        q = q.where(AuditLog.result == result)
    if start:
        q = q.where(AuditLog.ts >= dt.datetime.fromisoformat(start.replace("Z", "+00:00")))
    if end:
        q = q.where(AuditLog.ts <= dt.datetime.fromisoformat(end.replace("Z", "+00:00")))
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.execute(q.order_by(AuditLog.ts.desc()).limit(limit).offset(offset)).scalars().all()
    items = [
        {
            "id": r.id, "ts": r.ts.isoformat(), "user_id": r.user_id, "username": r.username,
            "action": r.action, "resource": r.resource, "result": r.result,
            "source_ip": r.source_ip, "request_id": r.request_id, "detail": r.detail,
        }
        for r in rows
    ]
    return {"items": items, "total": total or 0, "limit": limit, "offset": offset}
