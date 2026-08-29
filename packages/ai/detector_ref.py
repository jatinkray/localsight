"""Reference person detector (PLACEHOLDER — runs without a GPU or model).

This simulates a camera watching a doorway: a "person" appears for a few seconds,
moves across the frame, then leaves, with gaps between appearances. It exists so
the entire ingestion -> detection -> tracking -> event pipeline runs and is
testable on any machine. Replace `AI_DETECTOR` with a real implementation
(e.g. YOLO/ONNX via `packages.ai.detector_onnx`) without changing the API.

Production detectors consume real pixels; this one is pixel-agnostic.
"""
from __future__ import annotations

import math
from typing import List

from packages.ai.interfaces import Detection, Frame


class SyntheticDetector:
    def __init__(
        self,
        fps: int = 5,
        appear_seconds: float = 12.0,
        gap_seconds: float = 40.0,
        confidence: float = 0.9,
    ) -> None:
        self.fps = fps
        self.appear = appear_seconds
        self.gap = gap_seconds
        self.confidence = confidence

    def detect(self, frame: Frame, ts) -> List[Detection]:
        t = ts.timestamp() if hasattr(ts, "timestamp") else float(ts)
        cycle = self.appear + self.gap
        phase = t % cycle
        if phase > self.appear:
            return []  # nobody present during the gap
        # move horizontally across the frame over the appearance window
        frac = phase / self.appear
        x = 0.1 + 0.7 * frac
        y = 0.45 + 0.05 * math.sin(frac * math.pi)
        w, h = 0.08, 0.35
        return [Detection(label="person", confidence=self.confidence, bbox=(x, y, w, h))]
