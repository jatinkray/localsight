"""Audit log read API (audit:view). Immutable-style records; never modified.

M1 (E-1/E-2/E-14): sortable (whitelisted columns), filterable by user,
action, result and time window, paginated, and exportable as a
CSV-injection-safe stream. Every export is itself audited.
"""
from __future__ import annotations

import csv
import datetime as dt
import io

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from apps.api.audit import write_audit
from apps.api.dependencies import get_db, require_permission
from packages.domain.models import AuditLog
from packages.domain.timeutil import iso

router = APIRouter(prefix="/api/audit", tags=["audit"])

# Sortable columns (whitelist — the client sends a key, never a column name).
_AUDIT_SORT = {
    "ts": AuditLog.ts,
    "user": AuditLog.username,
    "action": AuditLog.action,
    "resource": AuditLog.resource,
    "result": AuditLog.result,
    "ip": AuditLog.source_ip,
}


def _audit_query(
    *,
    username: str | None = None,
    action: str | None = None,
    result: str | None = None,
    start: str | None = None,
    end: str | None = None,
):
    q = select(AuditLog)
    if username:
        q = q.where(AuditLog.username.ilike(f"%{username}%"))
    if action:
        q = q.where(AuditLog.action == action)
    if result:
        q = q.where(AuditLog.result == result)
    if start:
        q = q.where(AuditLog.ts >= dt.datetime.fromisoformat(start.replace("Z", "+00:00")))
    if end:
        q = q.where(AuditLog.ts <= dt.datetime.fromisoformat(end.replace("Z", "+00:00")))
    return q


def _apply_sort(q, sort: str | None, direction: str | None):
    col = _AUDIT_SORT.get(sort or "ts", AuditLog.ts)
    return q.order_by(col.desc() if direction == "desc" else col.asc(),
                      AuditLog.id.desc())  # stable tiebreak


@router.get("", dependencies=[Depends(require_permission("audit:view"))])
def list_audit(
    request: Request,
    db: Session = Depends(get_db),
    username: str | None = None,
    action: str | None = None,
    result: str | None = None,
    start: str | None = None,
    end: str | None = None,
    sort: str | None = Query(None, pattern="^(ts|user|action|resource|result|ip)$"),
    direction: str | None = Query(None, pattern="^(asc|desc)$"),
    limit: int = Query(100, le=500, ge=1),
    offset: int = Query(0, ge=0),
):
    q = _audit_query(username=username, action=action, result=result,
                     start=start, end=end)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.execute(_apply_sort(q, sort, direction).limit(limit).offset(offset)).scalars().all()
    items = [
        {
            "id": r.id, "ts": iso(r.ts), "user_id": r.user_id, "username": r.username,
            "action": r.action, "resource": r.resource, "result": r.result,
            "source_ip": r.source_ip, "request_id": r.request_id, "detail": r.detail,
        }
        for r in rows
    ]
    return {"items": items, "total": total or 0, "limit": limit, "offset": offset}


def _csv_cell(value) -> str:
    """CSV-injection-safe cell: prefix formula-triggering leading chars."""
    s = "" if value is None else str(value)
    if s[:1] in ("=", "+", "-", "@"):
        return " " + s
    return s


@router.get("/export.csv", dependencies=[Depends(require_permission("audit:view"))])
def export_audit_csv(
    request: Request,
    db: Session = Depends(get_db),
    username: str | None = None,
    action: str | None = None,
    result: str | None = None,
    start: str | None = None,
    end: str | None = None,
    sort: str | None = Query(None, pattern="^(ts|user|action|resource|result|ip)$"),
    direction: str | None = Query(None, pattern="^(asc|desc)$"),
):
    """Stream the CURRENT filtered audit set as CSV (M1/E-2). The export of
    the audit trail is itself audited — compliance expects the meta-trail."""
    q = _audit_query(username=username, action=action, result=result,
                     start=start, end=end)
    rows = db.execute(_apply_sort(q, sort, direction).limit(50_000)).scalars().all()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ts", "username", "action", "resource", "result", "source_ip", "detail"])
    for r in rows:
        w.writerow([_csv_cell(x) for x in (
            iso(r.ts), r.username, r.action, r.resource, r.result,
            r.source_ip, r.detail.get("reason") if isinstance(r.detail, dict) else "")])

    write_audit(db, user=request.state.user, action="audit.export_csv",
                request_id=getattr(request.state, "request_id", "-"),
                detail={"rows": len(rows), "filters": {
                    "username": username, "action": action, "result": result}})
    db.commit()
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="audit.csv"'},
    )
