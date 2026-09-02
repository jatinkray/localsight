"""Events API: search (paginated, indexed), detail with signed media URLs, and
audited export. No video is ever served via a permanent public URL; downloads
use short-lived signed URLs and every export is audit-logged.
"""
from __future__ import annotations

import csv
import datetime as dt
import io

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from apps.api.audit import write_audit
from apps.api.bootstrap import Runtime
from apps.api.dependencies import get_db, get_runtime, require_permission
from packages.domain.models import Event, VideoSegment
from packages.domain.timeutil import iso

router = APIRouter(prefix="/api/events", tags=["events"])

# Sortable columns (M1/E-1): whitelisted — the client sends a key, never a
# SQL column name. Direction is applied by whitelist too.
_EVENT_SORT = {
    "timestamp": Event.timestamp_start,
    "camera": Event.camera_id,
    "type": Event.event_type,
    "identity": Event.identity_status,
    "confidence": Event.confidence,
    "duration": Event.timestamp_end,
}


def _apply_sort(q, sort: str | None, direction: str | None):
    col = _EVENT_SORT.get(sort or "timestamp", Event.timestamp_start)
    return q.order_by(col.desc() if direction == "desc" else col.asc(),
                      Event.id.asc())  # stable tiebreak for pagination


@router.get("", dependencies=[Depends(require_permission("events:view"))])
def list_events(
    request: Request,
    db: Session = Depends(get_db),
    camera_id: str | None = None,
    identity_id: str | None = None,
    identity_status: str | None = None,
    start: str | None = None,
    end: str | None = None,
    min_confidence: float | None = None,
    sort: str | None = Query(
        None, pattern="^(timestamp|camera|type|identity|confidence|duration)$"),
    direction: str | None = Query(None, pattern="^(asc|desc)$"),
    limit: int = Query(50, le=500, ge=1),
    offset: int = Query(0, ge=0),
):
    q = select(Event)
    if camera_id:
        q = q.where(Event.camera_id == camera_id)
    if identity_id:
        q = q.where(Event.identity_id == identity_id)
    if identity_status:
        q = q.where(Event.identity_status == identity_status)
    if start:
        q = q.where(Event.timestamp_start >= dt.datetime.fromisoformat(start.replace("Z", "+00:00")))
    if end:
        q = q.where(Event.timestamp_end <= dt.datetime.fromisoformat(end.replace("Z", "+00:00")))
    if min_confidence is not None:
        q = q.where(Event.confidence >= min_confidence)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.execute(_apply_sort(q, sort, direction).limit(limit).offset(offset)).scalars().all()
    items = [
        {
            "id": r.id, "camera_id": r.camera_id, "track_id": r.track_id,
            "identity_id": r.identity_id, "identity_status": r.identity_status,
            "event_type": r.event_type,
            "timestamp_start": iso(r.timestamp_start),
            "timestamp_end": iso(r.timestamp_end),
            "confidence": r.confidence, "bbox": r.bbox,
            "has_snapshot": bool(r.snapshot_key_enc),
            "has_video": bool(r.video_segment_key_enc),
        }
        for r in rows
    ]
    return {"items": items, "total": total or 0, "limit": limit, "offset": offset}


@router.get("/export.csv", dependencies=[Depends(require_permission("events:export"))])
def export_events_csv(
    request: Request,
    db: Session = Depends(get_db),
    camera_id: str | None = None,
    identity_status: str | None = None,
    start: str | None = None,
    end: str | None = None,
    min_confidence: float | None = None,
    sort: str | None = Query(
        None, pattern="^(timestamp|camera|type|identity|confidence|duration)$"),
    direction: str | None = Query(None, pattern="^(asc|desc)$"),
):
    """Stream the CURRENT result set (M1/E-2) as CSV — same filters as the
    list, capped at 50k rows, audited like every export. Never invents a
    download link that outlives the request."""
    from fastapi.responses import StreamingResponse

    q = select(Event)
    if camera_id:
        q = q.where(Event.camera_id == camera_id)
    if identity_status:
        q = q.where(Event.identity_status == identity_status)
    if start:
        q = q.where(
            Event.timestamp_start >= dt.datetime.fromisoformat(start.replace("Z", "+00:00")))
    if end:
        q = q.where(
            Event.timestamp_end <= dt.datetime.fromisoformat(end.replace("Z", "+00:00")))
    if min_confidence is not None:
        q = q.where(Event.confidence >= min_confidence)
    rows = db.execute(_apply_sort(q, sort, direction).limit(50_000)).scalars().all()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "camera_id", "event_type", "timestamp_start", "timestamp_end",
                "identity_id", "identity_status", "confidence"])
    for r in rows:
        w.writerow([_csv_cell(x) for x in (
            r.id, r.camera_id, r.event_type,
            iso(r.timestamp_start), iso(r.timestamp_end),
            r.identity_id, r.identity_status, r.confidence)])

    write_audit(db, user=request.state.user, action="events.export_csv",
                request_id=getattr(request.state, "request_id", "-"),
                detail={"rows": len(rows), "filters": {
                    "camera_id": camera_id, "identity_status": identity_status,
                    "start": start, "end": end, "min_confidence": min_confidence}})
    db.commit()
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="events.csv"'},
    )


@router.get("/{event_id}", dependencies=[Depends(require_permission("events:view"))])
def get_event(event_id: str, request: Request, db: Session = Depends(get_db), rt: Runtime = Depends(get_runtime)):
    ev = db.get(Event, event_id)
    if not ev:
        raise HTTPException(status_code=404, detail="event not found")
    snapshot_url = None
    video_url = None
    if ev.snapshot_key_enc:
        key = rt.crypto.decrypt_str(ev.snapshot_key_enc)
        snapshot_url = rt.storage.sign_get_url(key, expires_sec=300)
    if ev.video_segment_key_enc:
        key = rt.crypto.decrypt_str(ev.video_segment_key_enc)
        video_url = rt.storage.sign_get_url(key, expires_sec=300)
    return {
        "id": ev.id, "camera_id": ev.camera_id, "track_id": ev.track_id,
        "identity_id": ev.identity_id, "identity_status": ev.identity_status,
        "event_type": ev.event_type,
        "timestamp_start": iso(ev.timestamp_start),
        "timestamp_end": iso(ev.timestamp_end),
        "confidence": ev.confidence, "bbox": ev.bbox,
        "snapshot_url": snapshot_url, "video_url": video_url,
    }


@router.get("/{event_id}/export", dependencies=[Depends(require_permission("events:export"))])
def export_event(event_id: str, request: Request, db: Session = Depends(get_db), rt: Runtime = Depends(get_runtime)):
    """Issue a short-lived signed URL for the event's video clip and audit the
    export. Watermarking / masking is a deployment option recorded in `detail`."""
    ev = db.get(Event, event_id)
    if not ev or not ev.video_segment_key_enc:
        raise HTTPException(status_code=404, detail="no exportable video for event")
    key = rt.crypto.decrypt_str(ev.video_segment_key_enc)
    url = rt.storage.sign_get_url(key, expires_sec=300)
    write_audit(db, user=request.state.user, action="video.export", resource=event_id,
                request_id=getattr(request.state, "request_id", "-"),
                detail={"watermark": False, "camera_id": ev.camera_id})
    db.commit()
    return {"url": url, "expires_in": 300}


@router.get("/{event_id}/clip", dependencies=[Depends(require_permission("events:export"))])
def event_clip(event_id: str, request: Request, db: Session = Depends(get_db), rt: Runtime = Depends(get_runtime)):
    """Return a signed, expiring manifest of the VideoSegment rows that overlap
    this event's time window so an operator can download a full clip.

    Segments are returned in start-time order. Each carries a short-lived signed
    download URL produced by the storage layer (works for both local and S3
    backends). Every export is recorded in the immutable audit log.
    """
    ev = db.get(Event, event_id)
    if not ev:
        raise HTTPException(status_code=404, detail="event not found")
    if ev.timestamp_end < ev.timestamp_start:
        raise HTTPException(status_code=400, detail="invalid event window")
    q = (
        select(VideoSegment)
        .where(VideoSegment.camera_id == ev.camera_id)
        .where(VideoSegment.start_ts < ev.timestamp_end)
        .where(VideoSegment.end_ts > ev.timestamp_start)
        .order_by(VideoSegment.start_ts.asc())
        .limit(200)
    )
    rows = db.execute(q).scalars().all()
    if not rows:
        raise HTTPException(status_code=404, detail="no recording segments for this event")
    expires = 300
    segments = [
        {
            "id": s.id,
            "start_ts": iso(s.start_ts),
            "end_ts": iso(s.end_ts),
            "duration_sec": s.duration_sec,
            "size_bytes": s.size_bytes,
            "url": rt.storage.sign_get_url(s.storage_key, expires_sec=expires),
        }
        for s in rows
    ]
    write_audit(
        db, user=request.state.user, action="video.clip.assemble",
        resource=event_id, request_id=getattr(request.state, "request_id", "-"),
        detail={"camera_id": ev.camera_id, "segment_count": len(segments)},
    )
    db.commit()
    return {
        "event_id": ev.id,
        "camera_id": ev.camera_id,
        "start_ts": iso(ev.timestamp_start),
        "end_ts": iso(ev.timestamp_end),
        "segment_count": len(segments),
        "total_size_bytes": sum(s.size_bytes for s in rows),
        "segments": segments,
        "expires_in": expires,
    }


def _csv_cell(value) -> str:
    """CSV-injection-safe cell (M1/E-2): leading =, +, -, @ would execute as a
    formula in Excel/Sheets; prefix a space so the cell stays text."""
    s = "" if value is None else str(value)
    if s[:1] in ("=", "+", "-", "@"):
        return " " + s
    return s


