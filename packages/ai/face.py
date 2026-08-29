"""Reference face detection + embedding (PLACEHOLDER).

Production swaps in a real face detector + embedding model (e.g. InsightFace /
ArcFace). The reference embedder is *deterministic*: it hashes the bytes of the
face crop into a fixed-dimension unit vector, so enrollment and recognition are
reproducible in tests without any model weights. It does NOT produce meaningful
biometrics — it exists to exercise the pipeline end-to-end.
"""
from __future__ import annotations

import hashlib
from typing import List, Optional

from packages.ai.interfaces import FaceDetector, FaceEmbedder, Frame


def _hash_bytes(b: bytes) -> bytes:
    return hashlib.sha256(b).digest()


class ReferenceFaceDetector(FaceDetector):
    def detect(self, frame: Frame, person_bbox) -> Optional[tuple[float, float, float, float]]:
        # In reference mode we assume a face occupies the upper-center of the
        # person bbox. Real models would run a face net on the cropped region.
        x, y, w, h = person_bbox
        fw, fh = w * 0.5, h * 0.4
        fx = x + (w - fw) / 2
        fy = y + h * 0.05
        return (fx, fy, fw, fh)


class ReferenceEmbedder(FaceEmbedder):
    model_version = "ref-v0"
    dimension = 128

    def embed(self, frame: Frame, face_bbox) -> List[float]:
        # Deterministically derive a vector from the crop bytes (or bbox if no
        # pixels are available, as in the synthetic pipeline).
        if isinstance(frame, (bytes, bytearray)):
            seed = _hash_bytes(bytes(frame))
        else:
            seed = _hash_bytes(str(face_bbox).encode())
        vec = []
        buf = seed
        while len(vec) < self.dimension:
            buf = _hash_bytes(buf)
            vec.extend(b / 255.0 - 0.5 for b in buf)
        vec = vec[: self.dimension]
        # L2-normalize so cosine similarity is well-defined.
        norm = sum(v * v for v in vec) ** 0.5 or 1.0
        return [v / norm for v in vec]
