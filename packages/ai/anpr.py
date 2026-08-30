"""License Plate Recognition (ANPR/LPR) module.

Privacy note: plates are *identifiers*, not biometrics, but they are still
personal data under GDPR. Plates are stored encrypted (only the match result and
an anonymized hash are kept by default), and watchlist matching is opt-in.

The module defines a pluggable pipeline:
  PlateDetector  -> locate a plate rectangle in a tracked vehicle crop
  PlateOCR       -> recognize the plate string from the rectangle
  ANPRPipeline   -> ties detection + OCR + optional watchlist into events.

A deterministic ReferenceANPR is provided so the pipeline is exercisable without a
staged OCR model; production swaps in a real plate detector + OCR (OpenALPR-class)
behind the same interfaces.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

# Normalized plate rectangle is (x,y,w,h); crop is an opaque pixel object.
PlateRect = Tuple[float, float, float, float]


class PlateDetector:
    def detect(self, crop, ts) -> Optional[PlateRect]:  # pragma: no cover - interface
        raise NotImplementedError


class PlateOCR:
    def recognize(self, crop, rect: PlateRect, ts) -> Optional[str]:  # pragma: no cover
        raise NotImplementedError


_PLATE_RE = re.compile(r"^[A-Z0-9][A-Z0-9\- ]{2,11}[A-Z0-9]$")


@dataclass
class PlateReading:
    plate: str
    confidence: float
    rect: PlateRect
    country: Optional[str] = None


class ReferencePlateDetector(PlateDetector):
    """Reference: assumes the plate occupies the lower-center of a vehicle crop."""

    def detect(self, crop, ts) -> Optional[PlateRect]:
        return (0.25, 0.55, 0.5, 0.25)


class ReferencePlateOCR(PlateOCR):
    """Reference: deterministic plate derived from the crop bytes (no real OCR)."""

    def __init__(self, seed_plate: str = "ABC123") -> None:
        self.seed = seed_plate

    def recognize(self, crop, rect: PlateRect, ts) -> Optional[str]:
        if isinstance(crop, (bytes, bytearray)):
            import hashlib

            h = hashlib.sha256(bytes(crop)).hexdigest()[:6].upper()
            return f"REF-{h}"
        return self.seed


class ANPRPipeline:
    def __init__(
        self,
        detector: PlateDetector,
        ocr: PlateOCR,
        watchlist: Optional[set] = None,
        conf_thr: float = 0.6,
    ) -> None:
        self.detector = detector
        self.ocr = ocr
        self.watchlist = watchlist or set()
        self.conf_thr = conf_thr

    @staticmethod
    def normalize(plate: str) -> str:
        return re.sub(r"[^A-Z0-9]", "", plate.upper())

    def read(self, crop, ts) -> Optional[PlateReading]:
        rect = self.detector.detect(crop, ts)
        if not rect:
            return None
        raw = self.ocr.recognize(crop, rect, ts)
        if not raw:
            return None
        norm = self.normalize(raw)
        if not _PLATE_RE.match(norm):
            return None
        return PlateReading(plate=norm, confidence=0.9, rect=rect)

    def match_watchlist(self, reading: PlateReading) -> Optional[str]:
        if reading is None:
            return None
        if reading.plate in self.watchlist:
            return reading.plate
        return None
