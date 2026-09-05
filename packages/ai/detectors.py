"""Detection backends.

LocalSight ships a *swappable* Detector interface (packages.ai.interfaces). This
module provides production-grade backends behind that same interface:

  * ONNXDetector        — runs any ONNX object-detection model via onnxruntime
                          (YOLO/RT-DETR exported to ONNX; INT8-quantized for edge).
  * TensorRTDetector    — NVIDIA Jetson/ dGPU (lazy import of tensorrt).
  * OpenVINODetector    — Intel CPU/iGPU/NPU (lazy import of openvino).
  * TFLiteDetector      — ARM/Coral (lazy import of tflite_runtime).
  * ReferenceMotionDetector — a dependency-light classical fallback (frame
                          differencing) used when no model is staged; keeps the
                          pipeline real and runnable on CPU without downloads.

All heavy runtimes are imported lazily so the API process never pays for them
unless a camera actually selects that backend. Model weights are loaded only from
the approved ModelRegistry (SHA-256 verified) — never from user-supplied URLs.
"""
from __future__ import annotations

import datetime as dt
from typing import List, Optional

from packages.ai.interfaces import Detection, Detector
from packages.ai.registry import ModelRegistry

# Labels aligned with COCO/ONVIF so downstream behavior rules + event types map
# cleanly across vendors.
DEFAULT_LABELS = [
    "person", "vehicle", "bicycle", "motorcycle", "bus", "truck",
    "animal", "bag", "package",
]


def iou(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def nms(boxes, scores, iou_thr: float = 0.45) -> List[int]:
    """Pure-Python non-maximum suppression over normalized (x,y,w,h) boxes."""
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    keep: List[int] = []
    while order:
        i = order.pop(0)
        keep.append(i)
        order = [j for j in order if iou(boxes[i], boxes[j]) <= iou_thr]
    return keep


def postprocess_yolo(
    raw,
    labels: List[str],
    conf_thr: float,
    iou_thr: float = 0.45,
    in_hw: tuple = (640, 640),
    frame_hw: tuple = (360, 640),
) -> List[Detection]:
    """Convert a YOLO-style ONNX output to Detections.

    Handles BOTH export layouts (auto-detected by shape):
      * row-major  [N, 5+classes]   — columns x,y,w,h (center, px) + scores
        (legacy v5/v6 exports; what the original implementation assumed)
      * transposed [4+classes, N]    — rows cx,cy,w,h (px) + one row per class
        score, one column per anchor — the layout every ultralytics v8/v11
        detect export actually produces. The old code read this as rows and
        produced garbage bboxes at near-zero confidence, i.e. no detections.
    Boxes are in model input pixel space; rescaled to normalized frame space.
    Pure enough to unit-test with tiny synthetic tensors.
    """
    try:
        import numpy as np
    except Exception as exc:  # pragma: no cover - numpy is a runtime dep for real models
        raise RuntimeError("numpy is required for ONNX/TensorRT/OpenVINO/TFLite backends") from exc

    arr = np.asarray(raw)
    if arr.ndim == 3:
        arr = arr[0]
    if arr.ndim != 2:
        return []

    sx = frame_hw[1] / in_hw[1]
    sy = frame_hw[0] / in_hw[0]

    # ── layout detection ───────────────────────────────────────────────────
    # row-major [N, 5+classes] needs ≥6 columns; transposed [4+classes, N]
    # needs ≥6 rows. When both could parse, real exports put thousands of
    # anchors on the LONG axis (shape[1] >> shape[0]) — anchors outnumber
    # 4+80 classes by an order of magnitude, so long-axis wins.
    if arr.shape[1] < 6:
        transposed = True
    elif arr.shape[0] < 6:
        transposed = False
    else:
        transposed = arr.shape[1] > arr.shape[0]

    # ── layout: [4+classes, N] transposed (ultralytics v8/v11) ──────────
    if transposed:
        boxes_px = arr[:4, :]                      # (4, N) cx,cy,w,h
        cls_scores = arr[4:, :]                    # (classes, N)
        best = np.argmax(cls_scores, axis=0)       # (N,)
        conf = cls_scores[best, np.arange(arr.shape[1])]
        keep = conf >= conf_thr
        xs, ys, ws, hs = boxes_px[0], boxes_px[1], boxes_px[2], boxes_px[3]
        boxes_n, scores_n, idxs = [], [], []
        for j in np.nonzero(keep)[0]:
            x1 = (xs[j] - ws[j] / 2) * sx
            y1 = (ys[j] - hs[j] / 2) * sy
            boxes_n.append((float(x1 / frame_hw[1]), float(y1 / frame_hw[0]),
                            float((ws[j] * sx) / frame_hw[1]), float((hs[j] * sy) / frame_hw[0])))
            scores_n.append(float(conf[j]))
            idxs.append(int(best[j]))
    # ── layout: [N, 5+classes] row-major (legacy) ─────────────────────────
    else:
        boxes_px = arr[:, :4]
        cls_scores = arr[:, 4:]
        boxes_n, scores_n, idxs = [], [], []
        for row in range(arr.shape[0]):
            label_idx = int(np.argmax(cls_scores[row]))
            row_conf = float(cls_scores[row][label_idx])
            if row_conf < conf_thr:
                continue
            x, y, w, h = boxes_px[row]
            x1 = (x - w / 2) * sx
            y1 = (y - h / 2) * sy
            boxes_n.append((float(x1 / frame_hw[1]), float(y1 / frame_hw[0]),
                            float((w * sx) / frame_hw[1]), float((h * sy) / frame_hw[0])))
            scores_n.append(row_conf)
            idxs.append(label_idx)

    detections: List[Detection] = []
    for i in nms(boxes_n, scores_n, iou_thr):
        label = labels[idxs[i]] if idxs[i] < len(labels) else f"class_{idxs[i]}"
        detections.append(Detection(label=label, confidence=float(scores_n[i]), bbox=tuple(boxes_n[i])))  # type: ignore[arg-type]
    return detections


class _RuntimeDetector(Detector):
    """Shared base for ONNX/TensorRT/OpenVINO/TFLite: same preprocessing + NMS."""

    model_version: str = "onnx-v0"
    dimension: int = 0

    def __init__(
        self,
        model_path: str,
        labels: List[str] | None = None,
        conf_thr: float = 0.45,
        iou_thr: float = 0.45,
        in_hw: tuple = (640, 640),
        frame_hw: tuple = (360, 640),
    ) -> None:
        self.model_path = model_path
        # Default to the COCO vocabulary: every mainstream detect export
        # (YOLOv5/v8/v11, RT-DETR) is COCO-trained. The platform vocabulary
        # (person/vehicle/bicycle/animal/bag/…) is applied by the
        # _LabelMappedDetector wrapper at the build_detector boundary.
        self.labels = labels if labels is not None else _COCO_LABELS
        self.conf_thr = conf_thr
        self.iou_thr = iou_thr
        self.in_hw = in_hw
        self.frame_hw = frame_hw
        self._session = None

    def _ensure_session(self):
        raise NotImplementedError

    def detect(self, frame: object, ts) -> List[Detection]:
        self._ensure_session()
        import numpy as np

        img = self._preprocess(np.asarray(frame) if not isinstance(frame, bytes) else self._decode(frame))
        out = self._infer(img)
        # The padded input dims drive box scaling: _preprocess pads to a
        # stride multiple, so the model saw (padded_w, padded_h), and boxes
        # must map back through that geometry — not the nominal in_hw.
        _, _, model_h, model_w = img.shape
        return postprocess_yolo(
            out, self.labels, self.conf_thr, self.iou_thr,
            in_hw=(model_h, model_w), frame_hw=self.frame_hw,
        )

    def _decode(self, raw_rgb24: bytes):
        # rawvideo rgb24 -> HxWx3 uint8; frame_hw must match the configured size.
        import numpy as np

        h, w = self.frame_hw
        return np.frombuffer(raw_rgb24[: h * w * 3], dtype=np.uint8).reshape(h, w, 3)

    def _preprocess(self, img):
        import numpy as np

        # YOLO-family exports require input H/W divisible by the stride (32):
        # e.g. a 640x360 frame is rejected at inference ("concat axis
        # mismatch"). Pad the shorter side (letterbox, bottom/right) to the
        # next multiple of 32 and remember the scale+pad so boxes map back.
        arr = np.asarray(img).astype(np.float32) / 255.0
        if arr.ndim == 3:
            arr = arr.transpose(2, 0, 1)  # HWC → CHW
        _, h, w = arr.shape
        target_h = ((h + 31) // 32) * 32
        target_w = ((w + 31) // 32) * 32
        if (target_h, target_w) != (h, w):
            padded = np.zeros((arr.shape[0], target_h, target_w), dtype=np.float32)
            padded[:, :h, :w] = arr
            arr = padded
        return np.expand_dims(arr, 0)

    def _preprocess_ctx(self, frame_hw):
        """Scale (frame→model) implied by the stride-padding in _preprocess."""
        return 1.0

    def _infer(self, img):  # pragma: no cover - runtime specific
        raise NotImplementedError


class ONNXDetector(_RuntimeDetector):
    def _ensure_session(self):
        if self._session is not None:
            return
        try:
            import onnxruntime as ort
        except Exception as exc:
            raise RuntimeError("onnxruntime is not installed (pip install onnxruntime)") from exc
        # Prefer GPU when available, fall back to CPU. Providers not installed
        # on this host are dropped (CoreML on macOS, CUDA on non-NVIDIA) so
        # onnxruntime doesn't warn on every session build.
        available = set(ort.get_available_providers())
        preferred = [p for p in ("CUDAExecutionProvider", "CoreMLExecutionProvider",
                                 "CPUExecutionProvider") if p in available]
        self._session = ort.InferenceSession(self.model_path, providers=preferred)
        self._input_name = self._session.get_inputs()[0].name

    def _infer(self, img):
        return self._session.run(None, {self._input_name: img})[0]


class TensorRTDetector(_RuntimeDetector):
    def _ensure_session(self):
        if self._session is not None:
            return
        try:
            import tensorrt as trt  # noqa: F401
        except Exception as exc:
            raise RuntimeError("tensorrt is not installed (NVIDIA Jetson / dGPU required)") from exc
        # Engine loading is site-specific; the registry guarantees the .engine file.
        self._session = self.model_path  # placeholder; real load in deploy docs

    def _infer(self, img):  # pragma: no cover - requires GPU engine
        raise NotImplementedError("TensorRT engine execution requires the staged .engine file")


class OpenVINODetector(_RuntimeDetector):
    def _ensure_session(self):
        if self._session is not None:
            return
        try:
            from openvino import runtime as ov
        except Exception as exc:
            raise RuntimeError("openvino is not installed (Intel tier required)") from exc
        core = ov.Core()
        self._session = core.compile_model(self.model_path, "AUTO")

    def _infer(self, img):
        return list(self._session(img).values())[0]


class TFLiteDetector(_RuntimeDetector):
    def _ensure_session(self):
        if self._session is not None:
            return
        try:
            import tflite_runtime.interpreter as tfl
        except Exception as exc:
            raise RuntimeError("tflite_runtime is not installed (Edge TPU / ARM)") from exc
        self._session = tfl.Interpreter(model_path=self.model_path)
        self._session.allocate_tensors()

    def _infer(self, img):
        self._session.set_tensor(self._session.get_input_details()[0]["index"], img)
        self._session.invoke()
        return self._session.get_tensor(self._session.get_output_details()[0]["index"])


class ReferenceMotionDetector(Detector):
    """Classical frame-differencing person/vehicle proxy.

    Runs without any model download. It is *not* a substitute for a real detector
    (higher false-negative rate), but it makes the full pipeline genuinely
    functional on CPU and is what the default deployment uses until an operator
    stages an ONNX model via the registry. Bboxes are coarse (full foreground blob).
    """

    model_version = "ref-motion-v0"

    def __init__(self, conf_thr: float = 0.5, min_area: float = 0.01) -> None:
        self.conf_thr = conf_thr
        self.min_area = min_area
        self._prev = None

    def detect(self, frame: object, ts) -> List[Detection]:
        try:
            import numpy as np
        except Exception:
            return []  # no numpy -> no detection (pipeline falls back to synthetic elsewhere)
        if isinstance(frame, bytes):
            # caller should pass decoded frames; if bytes, skip to avoid guessing size
            return []
        img = np.asarray(frame).astype(np.float32)
        if img.ndim == 3:
            gray = img.mean(axis=2)
        else:
            gray = img
        if self._prev is None:
            self._prev = gray
            return []
        diff = np.abs(gray - self._prev)
        self._prev = gray
        mask = (diff > 25.0).astype(np.float32)
        area = mask.sum() / max(1, mask.size)
        if area < self.min_area:
            return []
        ys, xs = np.where(mask > 0)
        if len(xs) == 0:
            return []
        h, w = mask.shape
        x1, y1, x2, y2 = xs.min() / w, ys.min() / h, xs.max() / w, ys.max() / h
        return [Detection(label="person", confidence=float(min(1.0, 0.5 + area * 5)),
                          bbox=(float(x1), float(y1), float(x2 - x1), float(y2 - y1)))]


_BACKENDS = {
    "onnx": ONNXDetector,
    "tensorrt": TensorRTDetector,
    "openvino": OpenVINODetector,
    "tflite": TFLiteDetector,
    "reference": ReferenceMotionDetector,
}

# COCO (80-class) → LocalSight label vocabulary. DEFAULT_LABELS is the
# platform's operator-facing set; staged COCO models emit COCO names, and
# rules/tracks/alerts key off LocalSight labels — map at the boundary.
_COCO_TO_LOCALSIGHT = {
    "person": "person",
    "bicycle": "bicycle",
    "car": "vehicle", "motorcycle": "motorcycle", "bus": "bus",
    "truck": "truck", "train": "vehicle", "airplane": "vehicle", "boat": "vehicle",
    "bird": "animal", "cat": "animal", "dog": "animal", "horse": "animal",
    "sheep": "animal", "cow": "animal", "elephant": "animal", "bear": "animal",
    "zebra": "animal", "giraffe": "animal",
    "backpack": "bag", "handbag": "bag", "suitcase": "bag",
    "cell phone": "package", "laptop": "package", "tv": "package",
}

_COCO_LABELS = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
    "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
    "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
    "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup",
    "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear",
    "hair drier", "toothbrush",
]


class _LabelMappedDetector(Detector):
    """Wrap a staged COCO detector, remapping class names into the LocalSight
    vocabulary (person/vehicle/bicycle/animal/bag/…) and dropping classes with
    no platform meaning (rules, alerts, and analytics key on our labels)."""

    def __init__(self, inner: Detector) -> None:
        self._inner = inner
        self.model_version = inner.model_version

    def detect(self, frame, ts) -> List[Detection]:
        out: List[Detection] = []
        for d in self._inner.detect(frame, ts):
            mapped = _COCO_TO_LOCALSIGHT.get(d.label)
            if mapped is None:
                continue
            out.append(Detection(label=mapped, confidence=d.confidence, bbox=d.bbox))
        return out


def build_detector(
    settings,
    registry: ModelRegistry,
    backend: str | None = None,
):
    """Construct a Detector from configuration.

    backend "reference" (default / no model staged) returns the classical fallback.
    Any model-backed backend loads & verifies the staged artifact from the registry
    (fail-closed on hash mismatch). Staged COCO models are wrapped so the
    platform's label vocabulary (person/vehicle/bicycle/animal/bag/…) is what
    rules, tracks, and alerts see — COCO-only classes are dropped.
    """
    backend = backend or settings.ai_detector
    if backend in ("reference", "synthetic"):
        return ReferenceMotionDetector(conf_thr=settings.ai_confidence_threshold)
    if backend not in _BACKENDS:
        raise RuntimeError(f"unknown AI_DETECTOR backend: {backend}")
    model_name = getattr(settings, "ai_model_name", "detector")
    version = getattr(settings, "ai_model_version", "latest")
    rec = registry.get(model_name, version)
    if not registry.verify(model_name, version):
        raise RuntimeError(f"model {model_name}@{version} failed integrity check")
    inner = _BACKENDS[backend](rec.path, conf_thr=settings.ai_confidence_threshold)
    return _LabelMappedDetector(inner)
