"""Analytics / BI aggregation.

Turns the event + track archive into the business metrics buyers pay for: people
counting, occupancy trends, dwell time, and trajectory heatmaps (retail/transit
ROI per MarketIntelo). All queries are indexed (camera_id + timestamp) and operate
purely on the existing ORM models, so they run on SQLite in dev and PostgreSQL in
prod without change.
"""
from __future__ import annotations

import datetime as dt
from typing import List, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from packages.domain.models import Event, Track


def people_counting(session: Session, camera_id: str, start: dt.datetime, end: dt.datetime) -> int:
    """Distinct tracks (people) seen on a camera in a window."""
    q = select(func.count(func.distinct(Track.id))).where(
        Track.camera_id == camera_id,
        Track.first_seen >= start,
        Track.last_seen <= end,
        Track.identity_status != "object",
    )
    return int(session.scalar(q) or 0)


def occupancy_trend(session: Session, camera_id: str, start: dt.datetime, end: dt.datetime,
                    bucket_min: int = 60) -> List[Tuple[dt.datetime, int]]:
    """Occupancy (distinct active tracks) per time bucket."""
    buckets: List[Tuple[dt.datetime, int]] = []
    step = dt.timedelta(minutes=bucket_min)
    cur = start
    while cur < end:
        nxt = cur + step
        q = select(func.count(func.distinct(Track.id))).where(
            Track.camera_id == camera_id,
            Track.last_seen >= cur,
            Track.first_seen < nxt,
        )
        buckets.append((cur, int(session.scalar(q) or 0)))
        cur = nxt
    return buckets


def dwell_time(session: Session, camera_id: str, start: dt.datetime, end: dt.datetime) -> float:
    """Average presence duration (seconds) across events in a window.

    Computed in Python over the rows so it is correct on both SQLite (dev) and
    PostgreSQL (prod) without DB-specific EXTRACT/julian functions.
    """
    rows = session.execute(
        select(Event.timestamp_start, Event.timestamp_end).where(
            Event.camera_id == camera_id, Event.event_type == "presence",
            Event.timestamp_start >= start, Event.timestamp_end <= end)
    ).all()
    if not rows:
        return 0.0
    total = sum((e[1] - e[0]).total_seconds() for e in rows)
    return total / len(rows)


def event_type_breakdown(session: Session, camera_id: str | None, start: dt.datetime,
                         end: dt.datetime) -> List[Tuple[str, int]]:
    stmt = select(Event.event_type, func.count(Event.id)).where(
        Event.timestamp_start >= start, Event.timestamp_end <= end)
    if camera_id:
        stmt = stmt.where(Event.camera_id == camera_id)
    stmt = stmt.group_by(Event.event_type)
    return [(r[0], int(r[1])) for r in session.execute(stmt).all()]


def heatmap_grid(session: Session, camera_id: str, start: dt.datetime, end: dt.datetime,
                 grid: Tuple[int, int] = (10, 10)) -> List[List[int]]:
    """2D histogram of track-center positions sampled from Track.trajectory.

    Returns a `grid[0] x grid[1]` matrix (rows=y, cols=x) of visit counts. Useful
    for retail footfall / dwell heatmaps.
    """
    rows, cols = grid
    matrix = [[0] * cols for _ in range(rows)]
    tracks = session.execute(
        select(Track.trajectory).where(
            Track.camera_id == camera_id,
            Track.last_seen >= start, Track.first_seen <= end)
    ).all()
    for (traj,) in tracks:
        if not traj:
            continue
        for pt in traj:
            try:
                cx, cy = float(pt[0]), float(pt[1])
            except (TypeError, ValueError, IndexError):
                continue
            ix = min(cols - 1, max(0, int(cx * cols)))
            iy = min(rows - 1, max(0, int(cy * rows)))
            matrix[iy][ix] += 1
    return matrix
