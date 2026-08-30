"""Analytics / BI API.

Exposes the aggregated metrics buyers use for retail/transit/operations ROI:
people counting, occupancy trends, dwell time, event-type breakdown, and trajectory
heatmaps. All endpoints require analytics:view and run on the existing indexed
event/track tables (SQLite in dev, PostgreSQL in prod).
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from apps.api.bootstrap import Runtime
from apps.api.dependencies import get_db, get_runtime, require_permission
from packages.domain.analytics import (
    dwell_time,
    event_type_breakdown,
    heatmap_grid,
    occupancy_trend,
    people_counting,
)

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
