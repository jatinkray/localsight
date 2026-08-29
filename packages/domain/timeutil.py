"""Time utilities. All timestamps are stored in UTC internally."""
from __future__ import annotations

import datetime as dt


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def to_utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        # Naive -> treat as UTC (we always write tz-aware UTC).
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def iso(value: dt.datetime) -> str:
    return to_utc(value).isoformat()


def parse_iso(value: str) -> dt.datetime:
    return to_utc(dt.datetime.fromisoformat(value.replace("Z", "+00:00")))


def overlaps(a_start: dt.datetime, a_end: dt.datetime, b_start: dt.datetime, b_end: dt.datetime) -> bool:
    return a_start <= b_end and b_start <= a_end
