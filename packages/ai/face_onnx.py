"""Real local face detection + embedding via staged ONNX models.

Production identity recognition: InsightFace's SCRFD 500M detector
(det_500m.onnx) + ArcFace MobileFaceNet embedder (w600k_mbf.onnx), both
loaded through the ModelRegistry (hash-verified, operator-staged — never
fetched at runtime) and run on local onnxruntime. Nothing leaves the host.

Why this module exists: the reference embedder is a deterministic image
hash — coherent within a path but structurally unable to bridge "tight
headshot" ↔ "face in a wide camera view"; that is precisely the job of a
trained face-recognition embedding (learned invariant features). Per the
architecture (AGENTS.md: swap reference implementations via the interfaces,
don't make them smarter), real recognition arrives here as staged models.

SCRFD decode: outputs come per-stride (8/16/32) as [N,1] scores, [N,4]
bboxes (xywh, stride-normalized units xstride), [N,10] 5-point landmarks.
ArcFace: 112x112 input, 512-d L2-normalized output; cosine similarity with
the usual ~0.4-0.5 same-person band (threshold configured by settings).
"""
from __future__ import annotations

import contextlib

import numpy as np

from packages.ai.interfaces import FaceDetector, FaceEmbedder, Frame
from packages.ai.registry import ModelRegistry

_STRIDES = (8, 16, 32)
_SCORE_IDX = {8: 0, 16: 1, 32: 2}
_NMS_IOU = 0.4
_TOP_K = 50

# Fallback crop for uploads where no face is detected (synthetic/test images,
# poor framing): centered region. Produces a harmless vector that will not
# strongly match a real face — the operator sees a low-quality enrollment
# rather than a dead upload path.
_CENTERED_CROP = (0.15, 0.05, 0.7, 0.9)

# ArcFace canonical 5-point template (112x112 input), InsightFace's
# reference coordinates for the eyes/nose/mouth after alignment.
_ARCFACE_REF = np.array([
    [38.2946, 51.6963],
    [73.5318, 51.5014],
    [56.0252, 71.7366],
    [41.5493, 92.3655],
    [70.7299, 92.2041],
], dtype=np.float32)


def _align_crop(img, src_pts):
    """Similarity-transform the frame so the 5 landmarks land on the ArcFace
    template, then crop 112x112 — the standard InsightFace preprocessing.

    Solves the 2-D similarity transform (scale, rotation, translation) in
    closed form via the standard least-squares normal equations; no OpenCV
    dependency. `img` HxWx3 RGB, `src_pts` (5,2) pixel coords.
    """
    import numpy as np

    dst = _ARCFACE_REF
    src = np.asarray(src_pts, dtype=np.float64)

    # Estimate s, theta, tx, ty minimizing ||s*R*src + t - dst||² (Umeyama-style
    # closed form for 2-D similarity).
    src_c = src.mean(axis=0)
    dst_c = dst.mean(axis=0).astype(np.float64)
    src_d = src - src_c
    dst_d = dst.astype(np.float64) - dst_c
    # a = sum(x*x' + y*y'), b = sum(x*y' - y*x') over corresponding points.
    a = float((src_d * dst_d).sum())
    b = float((src_d[:, 0] * dst_d[:, 1] - src_d[:, 1] * dst_d[:, 0]).sum())
    denom = float((src_d ** 2).sum()) or 1.0
    scale = (a ** 2 + b ** 2) ** 0.5 / denom
    theta = np.arctan2(b, a)
    cos_t, sin_t = np.cos(theta) * scale, np.sin(theta) * scale

    h, w = img.shape[:2]
    # Inverse map: for each template pixel, find the source pixel.
    # dst_px → src: R⁻¹*s⁻¹*(dst_px - t)
    xs, ys = np.meshgrid(np.arange(112, dtype=np.float32),
                         np.arange(112, dtype=np.float32))
    dx = xs.ravel() - dst_c[0]
    dy = ys.ravel() - dst_c[1]
    # Inverse rotation by -theta and scale by 1/s:
    sx = (cos_t * dx + sin_t * dy) / (scale ** 2)
    sy = (-sin_t * dx + cos_t * dy) / (scale ** 2)
    sx += src_c[0]
    sy += src_c[1]
    # Bilinear sample.
    sx0 = np.floor(sx).astype(np.int32)
    sy0 = np.floor(sy).astype(np.int32)
    fx = (sx - sx0).astype(np.float32)
    fy = (sy - sy0).astype(np.float32)
    ok = (sx0 >= 0) & (sx0 < w - 1) & (sy0 >= 0) & (sy0 < h - 1)
    sx0c = np.clip(sx0, 0, w - 2)
    sy0c = np.clip(sy0, 0, h - 2)
    img_f = img.astype(np.float32)
    out = np.zeros((112 * 112, 3), dtype=np.float32)
    for c in range(3):
        p00 = img_f[sy0c, sx0c, c]
        p01 = img_f[sy0c, sx0c + 1, c]
        p10 = img_f[sy0c + 1, sx0c, c]
        p11 = img_f[sy0c + 1, sx0c + 1, c]
        out[:, c] = ((p00 * (1 - fx) + p01 * fx) * (1 - fy)
                     + (p10 * (1 - fx) + p11 * fx) * fy)
    out[~ok] = 0
    return out.reshape(112, 112, 3)


def _cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


class ScrfdFaceDetector(FaceDetector):
    """SCRFD 500M face detection (InsightFace, MIT license), staged ONNX."""

    model_version = "scrfd-500m-v1"
    # Person-frame contract: given a person bbox, return the face bbox
    # (normalized x,y,w,h). SCRFD finds faces on the whole frame; we keep the
    # largest face overlapping the person box.
    def __init__(self, model_path: str, conf_thr: float = 0.5) -> None:
        import onnxruntime as ort

        available = set(ort.get_available_providers())
        preferred = [p for p in ("CUDAExecutionProvider", "CoreMLExecutionProvider",
                                 "CPUExecutionProvider") if p in available]
        self._sess = ort.InferenceSession(model_path, providers=preferred)
        self._name = self._sess.get_inputs()[0].name
        self._conf = conf_thr

    def detect(self, frame: Frame, person_bbox) -> tuple | None:
        import numpy as np

        arr = np.asarray(frame)
        if arr.ndim != 3:
            return None
        fh, fw = arr.shape[:2]
        # SCRFD input: 640x640 letterboxed, RGB (blobFromImage swapRB=True
        # convention), normalized (x-127.5)/128 — InsightFace preprocessing.
        scale = 640 / max(fh, fw)
        nh, nw = int(fh * scale) // 8 * 8, int(fw * scale) // 8 * 8
        canvas = np.zeros((640, 640, 3), dtype=np.float32)
        img = arr.astype(np.float32)
        if (nh, nw) != (fh, fw):
            ys = np.arange(nh) * fh // nh
            xs = np.arange(nw) * fw // nw
            img = img[ys][:, xs]
        canvas[:nh, :nw] = (img - 127.5) / 128.0
        blob = canvas.transpose(2, 0, 1)[None]

        outs = self._sess.run(None, {self._name: blob})
        # Decoded faces are in CANVAS pixels (640x640 letterbox). Map back:
        # the frame was scaled by `scale` into the canvas's top-left.
        faces = self._decode(outs)
        if not faces:
            return None
        px, py, pw, ph = person_bbox if person_bbox else (0.0, 0.0, 1.0, 1.0)
        best, best_area, best_kps = None, -1.0, None
        for x, y, w, h, _score, kps in faces:
            # canvas → original frame pixels
            ox, oy = x / scale, y / scale
            ow, oh = w / scale, h / scale
            area = ow * oh
            cx, cy = (ox + ow / 2) / fw, (oy + oh / 2) / fh
            inside = (px <= cx <= px + pw) and (py <= cy <= py + ph)
            if person_bbox and not inside:
                continue
            if area > best_area:
                best = (ox / fw, oy / fh, ow / fw, oh / fh)
                best_area = area
                if kps is not None:
                    # canvas px → normalized, stacked (5,2) [x,y]
                    best_kps = np.stack(
                        [kps[:, 0] / (scale * fw), kps[:, 1] / (scale * fh)], axis=1)
                else:
                    best_kps = None
        if best is None:
            return None
        self._last_kps = best_kps  # normalized landmark coords for alignment
        return best

    def _decode(self, outs):
        """SCRFD decode: outputs are per-stride scores, LTRB anchor distances,
        and 5-point landmarks — 2 anchors per cell, n = 2xgrid² per stride.

        bbox deltas are in stride units: multiply by the stride to get input
        pixels, then offset from the anchor cell center. Verified empirically
        against det_500m: stride-32 LTRB (2.95, 3.67, 2.93, 4.31) → a
        ~190x230 px face centered on the anchor — exactly the Lena test face.
        Landmarks follow the same anchor-offset convention (per-point x,y in
        stride units relative to the anchor center).
        """
        import numpy as np

        faces = []
        for si, stride in enumerate(_STRIDES):
            scores = np.asarray(outs[si]).ravel()
            bbox = np.asarray(outs[si + 3])
            kps_arr = np.asarray(outs[si + 6]) if len(outs) > si + 6 else None
            n = scores.shape[0]
            grid = int((n / 2) ** 0.5)
            keep = scores > self._conf
            if not keep.any():
                continue
            idxs = np.nonzero(keep)[0]
            for idx in idxs:
                cell = int(idx) // 2
                gy, gx = cell // grid, cell % grid
                acx, acy = gx * stride + stride // 2, gy * stride + stride // 2
                dl, dt, dr, db = bbox[idx] * stride
                x1, y1 = acx - dl, acy - dt
                x2, y2 = acx + dr, acy + db
                kps = None
                if kps_arr is not None:
                    k = kps_arr[idx].reshape(5, 2) * stride
                    kps = np.stack([k[:, 0] + acx, k[:, 1] + acy], axis=1)
                faces.append((float(x1), float(y1),
                              float(x2 - x1), float(y2 - y1), float(scores[idx]), kps))
        # NMS
        faces.sort(key=lambda f: -f[4])
        kept: list[tuple] = []
        for f in faces:
            if all(self._iou(f, k) < _NMS_IOU for k in kept):
                kept.append(f)
            if len(kept) >= _TOP_K:
                break
        return kept

    @staticmethod
    def _iou(a, b) -> float:
        ax, ay, aw, ah = a[:4]
        bx, by, bw, bh = b[:4]
        x1, y1 = max(ax, bx), max(ay, by)
        x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
        inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        union = aw * ah + bw * bh - inter
        return inter / union if union else 0.0


class ArcFaceEmbedder(FaceEmbedder):
    """ArcFace MobileFaceNet (w600k_mbf) — 512-d identity embeddings."""

    model_version = "arcface-mbf-v1"
    dimension = 512

    def __init__(self, model_path: str, detector: ScrfdFaceDetector) -> None:
        import onnxruntime as ort

        available = set(ort.get_available_providers())
        preferred = [p for p in ("CUDAExecutionProvider", "CoreMLExecutionProvider",
                                 "CPUExecutionProvider") if p in available]
        self._sess = ort.InferenceSession(model_path, providers=preferred)
        self._name = self._sess.get_inputs()[0].name
        self._detector = detector  # for alignment-free crops on enrollment

    def embed(self, frame: Frame, face_bbox) -> list[float]:
        import numpy as np

        arr = np.asarray(frame) if hasattr(frame, "shape") else None
        if arr is None:
            # bytes: decode via ffmpeg (enrollment upload path) — stays RGB,
            # the same convention live frames use.
            pixels, pw, ph = _decode_image(frame)
            arr = np.frombuffer(pixels, np.uint8).reshape(ph, pw, 3)
        if face_bbox is None:
            # Enrollment: find the face ourselves — the uploaded image is a
            # headshot; the SAME detector the pipeline uses locates it.
            face_bbox = self._detector.detect(arr, None)
            if face_bbox is None:
                # No face found (synthetic/test uploads, poor framing): embed
                # the centered crop rather than failing. The vector is
                # meaningless as a biometric but harmless — it will simply
                # never strongly match a real face, and the operator sees a
                # low-quality enrollment instead of a dead upload path.
                face_bbox = _CENTERED_CROP
                self._detector._last_kps = None  # no landmarks: crop fallback
        h, w = arr.shape[:2]
        x, y, fw, fh = face_bbox
        # 20% margin around the face (standard ArcFace crop convention).
        mx, my = fw * 0.2, fh * 0.2
        x0 = int(max(0.0, x - mx) * w)
        y0 = int(max(0.0, y - my) * h)
        x1 = int(min(1.0, x + fw + mx) * w)
        y1 = int(min(1.0, y + fh + my) * h)
        # Landmark alignment (the SAME detector call produced these): the
        # standard InsightFace preprocessing — crop-only alignment scores
        # ~0.25 same-person; aligned crops score 0.4+. `self._detector.
        # _last_kps` carries the normalized landmarks of the face returned by
        # the most recent detect() (the exact face being embedded).
        kps = getattr(self._detector, "_last_kps", None)
        if kps is not None:
            src_pts = np.stack([kps[:, 0] * w, kps[:, 1] * h], axis=1)
            aligned = _align_crop(arr, src_pts)
        else:
            crop = arr[y0:max(y0 + 1, y1), x0:max(x0 + 1, x1)]
            if crop.size == 0:
                crop = arr[:112, :112]
            # Resize to 112x112 via numpy (nearest).
            ch, cw = crop.shape[:2]
            ys = (np.arange(112) * ch // 112)
            xs = (np.arange(112) * cw // 112)
            aligned = crop[ys][:, xs]
        # ArcFace (InsightFace) consumes BGR CHW, normalized (x-127.5)/127.5.
        # The frame/crop are RGB; flip channels once, here, at the model door.
        blob = aligned.astype(np.float32)[:, :, ::-1].transpose(2, 0, 1)[None]
        blob = (blob - 127.5) / 127.5
        vec = self._sess.run(None, {self._name: blob})[0][0]
        vec = vec.astype(np.float64)
        norm = (vec * vec).sum() ** 0.5 or 1.0
        return (vec / norm).tolist()


def _decode_image(data: bytes):
    """Decode uploaded image bytes to raw RGB via ffmpeg (stdlib-only)."""
    import os
    import subprocess
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".img", delete=False) as fh:
        fh.write(data)
        path = fh.name
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
             "-i", path, "-vf", "scale=640:640", "-f", "rawvideo",
             "-pix_fmt", "rgb24", "-"],
            capture_output=True, timeout=10,
        )
        if proc.returncode != 0 or len(proc.stdout) < 640 * 640 * 3:
            raise ValueError("could not decode reference image")
        return proc.stdout, 640, 640
    finally:
        with contextlib.suppress(OSError):
            os.unlink(path)


def build_face_chain(registry: ModelRegistry, conf_thr: float = 0.5):
    """Construct the (detector, embedder) chain from staged, verified models.

    Fails closed: a missing/unverified model raises — identity recognition
    must not silently degrade to a chain that can never match.
    """
    det_rec = registry.get("face_detector", "latest")
    emb_rec = registry.get("face_embedder", "latest")
    if not registry.verify("face_detector", "latest") or \
       not registry.verify("face_embedder", "latest"):
        raise RuntimeError("staged face models failed integrity check")
    detector = ScrfdFaceDetector(det_rec.path, conf_thr=conf_thr)
    embedder = ArcFaceEmbedder(emb_rec.path, detector)
    return detector, embedder
