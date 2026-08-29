"""Daily timeline: presence intervals grouped by camera and identity.

Returns, for a given UTC day, the list of [start, end] windows per
(camera, identity_status/identity), which the UI renders as a 24h bar.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.bootstrap import Runtime
from apps.api.dependencies import get_db, get_runtime, require_permission
from packages.domain.models import Event, Person

router = APIRouter(prefix="/api/timeline", tags=["timeline"])


@router.get("", dependencies=[Depends(require_permission("events:view"))])
def timeline(
    request: Request,
    db: Session = Depends(get_db),
    rt: Runtime = Depends(get_runtime),
    date: str = Query(..., description="UTC date YYYY-MM-DD"),
    camera_id: str | None = None,
):
    try:
        day = dt.datetime.fromisoformat(date).replace(tzinfo=dt.timezone.utc)
    except ValueError:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=400, detail="bad date format; use YYYY-MM-DD")
    start = day
    end = day + dt.timedelta(days=1)

    q = select(Event).where(Event.timestamp_start >= start, Event.timestamp_start < end)
    if camera_id:
        q = q.where(Event.camera_id == camera_id)
    rows = db.execute(q.order_by(Event.camera_id, Event.timestamp_start)).scalars().all()

    persons = {p.id: p.label for p in db.execute(select(Person)).scalars().all()}
    out: dict[str, list] = {}
    for ev in rows:
        label = persons.get(ev.identity_id, ev.identity_status)
        key = f"{ev.camera_id}|{label}"
        out.setdefault(key, []).append({
            "start": ev.timestamp_start.isoformat(),
            "end": ev.timestamp_end.isoformat(),
            "confidence": ev.confidence,
            "identity_status": ev.identity_status,
        })
    return {
        "date": date,
        "camera_id": camera_id,
        "timeline": [
            {"camera_id": k.split("|")[0], "label": k.split("|")[1], "intervals": v}
            for k, v in out.items()
        ],
    }
