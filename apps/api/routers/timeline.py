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
from packages.domain.models import Event, Person, VideoSegment
from packages.domain.timeutil import iso

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

    q = select(Event).where(
        Event.timestamp_start >= start, Event.timestamp_start < end,
        Event.event_type == "presence",
    )
    if camera_id:
        q = q.where(Event.camera_id == camera_id)
    rows = db.execute(q.order_by(Event.camera_id, Event.timestamp_start)).scalars().all()

    persons = {p.id: p.label for p in db.execute(select(Person)).scalars().all()}
    out: dict[str, list] = {}
    for ev in rows:
        label = persons.get(ev.identity_id, ev.identity_status)
        key = f"{ev.camera_id}|{label}"
        out.setdefault(key, []).append({
            "start": iso(ev.timestamp_start),
            "end": iso(ev.timestamp_end),
            "confidence": ev.confidence,
            "identity_status": ev.identity_status,
        })
    rec_q = select(VideoSegment).where(
        VideoSegment.start_ts < end, VideoSegment.end_ts > start,
    )
    if camera_id:
        rec_q = rec_q.where(VideoSegment.camera_id == camera_id)
    rec_q = rec_q.order_by(VideoSegment.camera_id, VideoSegment.start_ts).limit(500)
    rec_rows = db.execute(rec_q).scalars().all()
    recording = [
        {
            "camera_id": s.camera_id,
            "start": iso(s.start_ts),
            "end": iso(s.end_ts),
            "duration_sec": s.duration_sec,
        }
        for s in rec_rows
    ]

    mk_q = select(Event).where(
        Event.timestamp_start >= start, Event.timestamp_start < end,
        Event.event_type != "presence",
    )
    if camera_id:
        mk_q = mk_q.where(Event.camera_id == camera_id)
    mk_q = mk_q.order_by(Event.timestamp_start).limit(500)
    mk_rows = db.execute(mk_q).scalars().all()
    markers = [
        {
            "id": e.id, "camera_id": e.camera_id, "event_type": e.event_type,
            "ts": iso(e.timestamp_start), "identity_status": e.identity_status,
        }
        for e in mk_rows
    ]

    return {
        "date": date,
        "camera_id": camera_id,
        "timeline": [
            {"camera_id": k.split("|")[0], "label": k.split("|")[1], "intervals": v}
            for k, v in out.items()
        ],
        "recording": recording,
        "markers": markers,
        "limits": {"recording": 500, "markers": 500},
    }
