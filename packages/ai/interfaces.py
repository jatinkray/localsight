"""AI stage interfaces. Bounding boxes are normalized [0,1] (x, y, w, h)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

# A frame is opaque to the core; detectors that need pixels receive it. The
# reference detector is pixel-agnostic, so `frame` may be None. Bytes/numpy
# arrays are passed through unchanged for real models.
Frame = object


@dataclass
class Detection:
    label: str
    confidence: float
    bbox: tuple[float, float, float, float]  # x, y, w, h normalized


@dataclass
class Track:
    track_id: str
    bbox: tuple[float, float, float, float]
    confidence: float
    center: tuple[float, float] = (0.0, 0.0)
    trajectory: List[tuple[float, float]] = field(default_factory=list)


@dataclass
class Recognition:
    person_id: Optional[str]
    similarity: Optional[float]
    status: str  # known | unknown | uncertain


class Detector:
    """Detect objects (person, vehicle, ...) in a frame."""

    def detect(self, frame: Frame, ts) -> List[Detection]:  # pragma: no cover - interface
        raise NotImplementedError


class Tracker:
    """Associate detections across frames into ephemeral track IDs."""

    def update(self, camera_id: str, detections: List[Detection], ts) -> List[Track]:  # pragma: no cover
        raise NotImplementedError


class FaceDetector:
    """Detect a face within an already-detected person bbox."""

    def detect(self, frame: Frame, person_bbox) -> Optional[tuple[float, float, float, float]]:
        raise NotImplementedError


class FaceEmbedder:
    """Map an aligned face crop to a fixed-dimension embedding vector."""

    model_version: str = "ref-v0"
    dimension: int = 128

    def embed(self, frame: Frame, face_bbox) -> List[float]:  # pragma: no cover
        raise NotImplementedError


class IdentityMatcher:
    """Search an embedding against enrolled identities."""

    def search(self, vector: List[float], model_version: str) -> Recognition:
        raise NotImplementedError
