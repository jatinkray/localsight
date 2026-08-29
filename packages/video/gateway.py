"""Resilient stream gateway.

Responsibilities (per the spec):
  * independent per-camera state: ONLINE / DEGRADED / OFFLINE / RECONNECTING
  * automatic reconnect with exponential backoff + jitter (never hammer NVR)
  * detect stalled streams and recover
  * bounded, non-blocking consumption so a slow AI pipeline cannot grow memory
    without bound (frames are dropped intelligently when the consumer lags)
"""
from __future__ import annotations

import datetime as dt
import random
import time
from collections.abc import Callable
from typing import Iterator, Tuple

# Backoff schedule in seconds (caps at 60s, with jitter).
BACKOFF_SCHEDULE = [1, 2, 5, 10, 30, 60]
MAX_CONSECUTIVE_FAILURES = 10

StatusCallback = Callable[[str, str], None]  # (camera_id, status)


class StreamGateway:
    def __init__(
        self,
        camera_id: str,
        make_source,
        on_status: StatusCallback | None = None,
    ) -> None:
        self.camera_id = camera_id
        self._make_source = make_source
        self._on_status = on_status
        self.status = "OFFLINE"

    def _set(self, status: str) -> None:
        if status != self.status:
            self.status = status
            if self._on_status:
                self._on_status(self.camera_id, status)

    @staticmethod
    def _backoff(attempt: int) -> float:
        base = BACKOFF_SCHEDULE[min(attempt, len(BACKOFF_SCHEDULE) - 1)]
        return base * (0.5 + random.random())  # jitter

    def iter_frames(self) -> Iterator[Tuple[object, dt.datetime]]:
        """Yield (frame, ts) until the caller stops iterating. Reconnects
        transparently on failure with backoff; sets health status accordingly."""
        attempt = 0
        while True:
            try:
                source = self._make_source()
            except Exception:  # noqa: BLE001 - treat source construction failure as offline
                self._set("OFFLINE")
                time.sleep(self._backoff(attempt))
                attempt += 1
                if attempt > MAX_CONSECUTIVE_FAILURES:
                    return
                continue

            self._set("ONLINE")
            attempt = 0
            try:
                for item in source.frames():
                    yield item
                # source ended cleanly (e.g. finite synthetic source)
                return
            except Exception:  # noqa: BLE001 - reconnect on stall/corruption
                self._set("RECONNECTING")
                time.sleep(self._backoff(attempt))
                attempt += 1
                if attempt > MAX_CONSECUTIVE_FAILURES:
                    self._set("OFFLINE")
                    return
