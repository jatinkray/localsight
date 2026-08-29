"""Audit logging helper. Immutable-style records for security-sensitive actions.

Never logs passwords, tokens, or raw biometric data. Callers pass only the
minimum descriptive detail (action, resource, result, source IP, request ID),
which are written by the API routers after each sensitive operation.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from packages.domain.models import AuditLog, User


def write_audit(
    session: Session,
    *,
    user: User | None = None,
    username: str = "",
    action: str,
    resource: str = "",
    result: str = "success",
    source_ip: str = "",
    request_id: str = "",
    detail: dict | None = None,
) -> AuditLog:
    record = AuditLog(
        user_id=user.id if user else None,
        username=username or (user.email if user else ""),
        action=action,
        resource=str(resource),
        result=result,
        source_ip=source_ip,
        request_id=request_id,
        detail=detail,
    )
    session.add(record)
    return record
