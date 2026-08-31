"""Analytics / BI API.

Exposes the aggregated metrics buyers use for retail/transit/operations ROI:
people counting, occupancy trends, dwell time, event-type breakdown, and trajectory
heatmaps. All endpoints require analytics:view and run on the existing indexed
event/track tables (SQLite in dev, PostgreSQL in prod).
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.bootstrap import Runtime
from apps.api.dependencies import get_db, get_runtime, require_permission
from packages.ai.vlm import ReferenceSceneEmbedder, SemanticSearch
from packages.domain.analytics import (
    dwell_time,
    event_type_breakdown,
    heatmap_grid,
    occupancy_trend,
    people_counting,
)
from packages.domain.models import Event

router = APIRouter(prefix="/api", tags=["analytics"])


def _parse(value: str) -> dt.datetime:
    try:
        d = dt.datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="bad datetime (use ISO 8601)")
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d


@router.get("/analytics/people-count", dependencies=[Depends(require_permission("analytics:view"))])
def people_count(camera_id: str, start: str, end: str, db: Session = Depends(get_db)):
    return {"camera_id": camera_id, "count": people_counting(db, camera_id, _parse(start), _parse(end))}


@router.get("/analytics/occupancy", dependencies=[Depends(require_permission("analytics:view"))])
def occupancy(camera_id: str, start: str, end: str, bucket_min: int = 60, db: Session = Depends(get_db)):
    return {"camera_id": camera_id,
            "buckets": [{"ts": t.isoformat(), "count": c}
                        for t, c in occupancy_trend(db, camera_id, _parse(start), _parse(end), bucket_min)]}


@router.get("/analytics/dwell", dependencies=[Depends(require_permission("analytics:view"))])
def dwell(camera_id: str, start: str, end: str, db: Session = Depends(get_db)):
    return {"camera_id": camera_id, "avg_dwell_sec": round(dwell_time(db, camera_id, _parse(start), _parse(end)), 2)}


@router.get("/analytics/breakdown", dependencies=[Depends(require_permission("analytics:view"))])
def breakdown(camera_id: str | None = None, start: str = "", end: str = "", db: Session = Depends(get_db)):
    s = _parse(start) if start else dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc)
    e = _parse(end) if end else dt.datetime(2100, 1, 1, tzinfo=dt.timezone.utc)
    return {"rows": [{"event_type": t, "count": c} for t, c in event_type_breakdown(db, camera_id, s, e)]}


@router.get("/analytics/heatmap", dependencies=[Depends(require_permission("analytics:view"))])
def heatmap(camera_id: str, start: str, end: str, grid_x: int = 10, grid_y: int = 10,
            db: Session = Depends(get_db)):
    return {"camera_id": camera_id, "grid": heatmap_grid(db, camera_id, _parse(start), _parse(end), (grid_y, grid_x))}


@router.get("/analytics/search", dependencies=[Depends(require_permission("analytics:view"))])
def semantic_search(q: str, camera_id: str | None = None, start: str = "", end: str = "",
                    top_k: int = 10, db: Session = Depends(get_db)):
    """Natural-language forensic search over archived events (Gen-3/4 VLM gap).

    Events in range are embedded (ReferenceSceneEmbedder) and ranked by cosine
    similarity to the query; a real CLIP/VLM backend drops in behind the same
    interface. Capped to the most recent 2000 events in range for latency.
    """
    s = _parse(start) if start else dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc)
    e = _parse(end) if end else dt.datetime(2100, 1, 1, tzinfo=dt.timezone.utc)
    stmt = select(Event).where(Event.timestamp_start >= s, Event.timestamp_end <= e)
    if camera_id:
        stmt = stmt.where(Event.camera_id == camera_id)
    events = db.execute(stmt.limit(2000)).scalars().all()
    if not events:
        return {"query": q, "results": []}
    idx = SemanticSearch(ReferenceSceneEmbedder())
    for ev in events:
        idx.index(ev.id, f"{ev.event_type} {ev.detail or ''}")
    ranked = idx.search(q, top_k=top_k)
    ids = [r[0] for r in ranked]
    rows = {ev.id: ev for ev in db.execute(select(Event).where(Event.id.in_(ids))).scalars().all()}
    return {"query": q, "results": [
        {"id": ev.id, "event_type": ev.event_type, "camera_id": ev.camera_id,
         "ts": ev.timestamp_start.isoformat() if ev.timestamp_start else None,
         "score": round(score, 4)}
        for ev_id, score in ranked if (ev := rows.get(ev_id)) is not None
    ]}
