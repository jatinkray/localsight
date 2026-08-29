"""Event aggregation and identity classification (core business logic).

Two responsibilities, kept pure and unit-tested:

1. Identity classification — map a similarity score to KNOWN / UNCERTAIN /
   UNKNOWN. We deliberately refuse to assert an identity below threshold and
   keep an explicit UNCERTAIN band to avoid false positives (safety > recall).
2. Event aggregation — merge a stream of per-frame detections for one tracked
   object into a single presence interval using a configurable gap tolerance, so
   a person visible for five minutes is one event, not thousands.
"""
from __future__ import annotations

import datetime as dt
from typing import Iterable


def classify_identity(
    similarity: float | None,
    threshold: float,
    uncertain_delta: float = 0.05,
) -> str:
    """Return 'known' | 'uncertain' | 'unknown'.

    similarity is the best cosine similarity for the candidate, or None when no
    face was available. Below (threshold - uncertain_delta) we do not claim
    anything; within the band we mark UNCERTAIN rather than forcing a label.
    """
    if similarity is None:
        return "unknown"
    if similarity >= threshold:
        return "known"
    if similarity >= threshold - uncertain_delta:
        return "uncertain"
    return "unknown"


def merge_intervals(
    intervals: Iterable[tuple[dt.datetime, dt.datetime]],
    gap: float,
) -> list[tuple[dt.datetime, dt.datetime]]:
    """Merge overlapping/near intervals separated by at most `gap` seconds.

    Used to collapse repeated detections into a single presence window.
    Intervals are (start, end) with start <= end.
    """
    ivs = sorted(intervals, key=lambda x: x[0])
    merged: list[tuple[dt.datetime, dt.datetime]] = []
    for start, end in ivs:
        if start > end:
            start, end = end, start
        if merged and (start - merged[-1][1]).total_seconds() <= gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def aggregate_track(
    first_seen: dt.datetime,
    last_seen: dt.datetime,
    detections: list[dt.datetime],
    merge_gap_seconds: float,
) -> list[tuple[dt.datetime, dt.datetime]]:
    """Aggregate point detections of a single track into presence windows."""
    intervals = [(t, t) for t in detections] or [(first_seen, last_seen)]
    return merge_intervals(intervals, merge_gap_seconds)
