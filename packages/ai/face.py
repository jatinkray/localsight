"""Reference face detection + embedding (PLACEHOLDER — deterministic, shared input space).

Production swaps in a real face detector + embedding model (e.g. InsightFace /
ArcFace) behind the SAME interfaces. The reference embedder exists to exercise
the enroll→recognize loop end-to-end without model weights, so it must be
COHERENT: the vector for a given face must be derivable identically at
enrollment (uploaded image bytes) and at recognition (live frame pixels).

It therefore embeds the FACE CROP PIXELS via a deterministic image hash — not
biometrics, but a real image-content signal: the same face framed the same way
matches; a different person (or a heavily rescaled/re-exposed crop) does not.
Previously enrollment hashed the whole JPEG file while recognition hashed the
bbox coordinate string — two unrelated vector spaces that could NEVER match,
making identity linking dead on arrival.
"""
from __future__ import annotations

import hashlib
from typing import List, Optional

from packages.ai.interfaces import FaceDetector, FaceEmbedder, Frame

# Perceptual-hash grid: the crop is downsampled to this many cells per axis and
# hashed band-wise. Coarse enough to survive minor pixel noise and small
# framing shifts; fine enough to separate faces. 10×10 cells + 28 hash dims
# = exactly 128 (the declared embedding dimension).
_GRID = 10
_BANDS = 4

# Enrollment geometry: a conventional headshot frames one centered person
# (head + upper torso). The reference face detector derives the face box from
# this person assumption EXACTLY as it does from a live detection's person
# box — so enrollment and recognition embed the same facial region geometry.
_CENTERED_PERSON = (0.3, 0.1, 0.4, 0.6)


def _hash_bytes(b: bytes) -> bytes:
    return hashlib.sha256(b).digest()


def _grid_from_bytes(raw: bytes, w: int, h: int) -> List[int]:
    """Average raw luminance-ish bytes into a _GRID x _GRID cell grid."""
    cells: List[int] = [0] * (_GRID * _GRID)
    if not raw or w <= 0 or h <= 0:
        return cells
    # Stride sampling: 4 samples per cell keeps this O(GRID²) regardless of
    # the crop size (a real 640px face crop never dominates frame time).
    samples = 0
    for gy in range(_GRID):
        for gx in range(_GRID):
            x0 = int(gx * w / _GRID)
            x1 = max(x0 + 1, int((gx + 1) * w / _GRID))
            y0 = int(gy * h / _GRID)
            y1 = max(y0 + 1, int((gy + 1) * h / _GRID))
            acc = 0
            n = 0
            for y in range(y0, y1, max(1, (y1 - y0) // 2)):
                base = y * w
                for x in range(x0, x1, max(1, (x1 - x0) // 2)):
                    idx = base + x
                    if idx < len(raw):
                        acc += raw[idx]
                        n += 1
            if n:
                cells[gy * _GRID + gx] = acc // n
                samples += 1
    _ = samples
    return cells


def _vector_from_cells(cells: List[int]) -> List[float]:
    """Deterministic unit vector from the cell grid: mean-centered brightness
    pattern (+ a small structural hash band), trimmed to exactly 128 dims.

    Value correlation, NOT edge structure: cosine over mean-centered cell
    values measures "how alike are these two brightness patterns", so the
    same face pixels score ~1.0 and different faces score low. (A gradient
    vector was tried first and is WRONG for cosine — two different faces
    with the same edge layout produce the same sparse support and score 1.0
    because cosine ignores magnitude.)
    """
    m = sum(cells) / max(1, len(cells))
    vec: List[float] = [(c - m) / 255.0 for c in cells]
    # Structural band, low weight: separates patterns that average out alike.
    h = _hash_bytes(bytes(cells))
    n_hash = max(0, 128 - len(vec))
    vec.extend(0.1 * (b / 255.0 - 0.5) for b in h[:n_hash])
    vec = vec[:128]
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return [v / norm for v in vec]


def _decode_image_bytes(data: bytes) -> tuple[bytes, int, int]:
    """Decode an uploaded headshot (JPEG/PNG/…) into raw rgb24 bytes via ffmpeg.

    ffmpeg is already a hard runtime dependency of the video path — reusing
    it keeps the embedder stdlib-only while giving enrollment and recognition
    the SAME pixel space. Failure is fatal: an unreadable enrollment image
    must not silently produce a garbage vector that can never match (the old
    behavior).
    """
    import os as _os
    import subprocess
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".img", delete=False) as fh:
        fh.write(data)
        path = fh.name
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
             "-i", path, "-vf", "scale=192:192", "-f", "rawvideo",
             "-pix_fmt", "rgb24", "-"],
            capture_output=True, timeout=10,
        )
        if proc.returncode != 0 or len(proc.stdout) < 192 * 192 * 3:
            raise ValueError("could not decode reference image")
        return proc.stdout, 192, 192
    finally:
        try:
            _os.unlink(path)
        except OSError:
            pass


def _frame_to_pixels(frame: Frame, face_bbox) -> tuple[bytes, int, int]:
    """Resolve any supported frame type into (face_crop_bytes, w, h).

    COHERENCE CONTRACT (the whole point of this embedder): enrollment and
    recognition must apply the SAME geometry. Both crop the FACE BOX (as
    ReferenceFaceDetector defines it) and the grid normalizes size — a
    3,000px headshot and a 40px live face crop produce comparable grids.

    ndarray + face_bbox (recognition): crop the face box from the frame.
    bytes (enrollment, no bbox): decode the image and assume a CONVENTIONAL
    CENTERED FACE — headshots frame the face centrally, which is the same
    assumption ReferenceFaceDetector makes about the person box.
    """
    if hasattr(frame, "shape") and getattr(frame, "ndim", 0) == 3:
        import numpy as np

        arr = np.asarray(frame)
        fh, fw = arr.shape[:2]
        if face_bbox is None:
            # Centered-person assumption for ndarray input without a bbox
            # (used by direct embed calls in tests): same detect step as
            # enrollment, so geometry stays coherent.
            face_bbox = ReferenceFaceDetector().detect(arr, _CENTERED_PERSON)
        x, y, w, h = face_bbox
        x0 = int(max(0.0, min(0.999, x)) * fw)
        y0 = int(max(0.0, min(0.999, y)) * fh)
        x1 = int(min(1.0, x + max(0.0, w)) * fw)
        y1 = int(min(1.0, y + max(0.0, h)) * fh)
        crop = arr[y0:max(y0 + 1, y1), x0:max(x0 + 1, x1)]
        if crop.size == 0:
            crop = arr[:1, :1]
        ch, cw = crop.shape[:2]
        raw = (crop.astype(np.float32) @ np.array([0.299, 0.587, 0.114],
                                                  dtype=np.float32)).astype(np.uint8)
        return raw.tobytes(), cw, ch
    if isinstance(frame, (bytes, bytearray)) and frame:
        # Enrollment: decode, then run the SAME detect→crop→embed chain
        # recognition uses. The uploaded image is treated as a full frame
        # containing one centered person (headshot convention); the reference
        # face detector derives the face box exactly as it does from live
        # person boxes — both paths then embed the SAME geometry.
        import numpy as np

        pixels, pw, ph = _decode_image_bytes(bytes(frame))
        arr = np.frombuffer(pixels, np.uint8).reshape(ph, pw, 3)
        face_box = ReferenceFaceDetector().detect(arr, _CENTERED_PERSON)
        return _frame_to_pixels(arr, face_box)
    # No pixels (synthetic pipeline): deterministic placeholder bytes.
    b = bytes(frame) if frame is not None else b"\x00"
    w = int(max(1.0, (face_bbox[2] if face_bbox else 1.0) * 128))
    h = int(max(1.0, (face_bbox[3] if face_bbox else 1.0) * 128))
    return b, w, h


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
    model_version = "ref-v1"
    dimension = 128

    def embed(self, frame: Frame, face_bbox) -> List[float]:
        """Deterministic, input-coherent embedding.

        Same face (same pixels, same framing) → same vector at enrollment
        and recognition. This is a placeholder image-hash, NOT biometrics —
        but it closes the enroll→recognize loop so the whole chain (enroll,
        cache, match, event linkage, dashboard chips) is exercisable.
        """
        raw, w, h = _frame_to_pixels(frame, face_bbox)
        cells = _grid_from_bytes(raw, w, h)
        return _vector_from_cells(cells)
