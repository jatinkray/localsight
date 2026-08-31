"""Multi-object tracker with motion prediction (SORT-style).

Pure-IOU association loses tracks under occlusion or fast motion. This reference
tracker adds constant-velocity *motion prediction*: each track's next position is
forecast from its recent velocity, then detections are associated to the predicted
boxes by IoU (greedy). That keeps cross-frame identity stable across brief
occlusions without requiring a ReID embedding model.

For appearance-based ReID (ByteTrack/BoT-SORT), stage an embedding model and extend
`associate` to score cosine similarity between track appearance and detection
crops — the interface here is unchanged, so the worker needs no edits.
"""
from __future__ import annotations

from typing import List, Tuple

from packages.ai.interfaces import Detection, Track, Tracker


def _iou(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _center(b: Tuple[float, float, float, float]) -> Tuple[float, float]:
    return (b[0] + b[2] / 2, b[1] + b[3] / 2)


class IouTracker(Tracker):
    def __init__(self, iou_threshold: float = 0.5, max_age: int = 30, vel_smooth: float = 0.5):
        self._iou = iou_threshold
        self._max_age = max_age
        self._vel_smooth = vel_smooth
        self._state: dict[str, dict] = {}  # track_id -> state
        self._counters: dict[str, int] = {}

    def _next_id(self, camera_id: str) -> str:
        n = self._counters.get(camera_id, 0) + 1
        self._counters[camera_id] = n
        return f"{camera_id}-track-{n}"

    def _predict(self, tid: str) -> Tuple[float, float, float, float]:
        st = self._state[tid]
        if st["age"] > 0:  # predict forward only for tracks not seen last frame
            bx, by, bw, bh = st["bbox"]
            vx, vy, vw, vh = st["vel"]
            return (bx + vx, by + vy, max(0.0, bw + vw), max(0.0, bh + vh))
        return st["bbox"]

    def update(self, camera_id: str, detections: List[Detection], ts) -> List[Track]:
        out: List[Track] = []
        predictions = {tid: self._predict(tid) for tid in self._state}
        used: set[str] = set()

        # Greedy association: each detection to its best predicted track by IoU.
        order = sorted(detections, key=lambda d: d.confidence, reverse=True)
        for det in order:
            best_id, best_iou = None, 0.0
            for tid, pred in predictions.items():
                if tid in used:
                    continue
                score = _iou(det.bbox, pred)
                if score > best_iou:
                    best_iou, best_id = score, tid
            if best_id and best_iou >= self._iou:
                st = self._state[best_id]
                old = st["bbox"]
                vel = (
                    (det.bbox[0] - old[0]) * self._vel_smooth + st["vel"][0] * (1 - self._vel_smooth),
                    (det.bbox[1] - old[1]) * self._vel_smooth + st["vel"][1] * (1 - self._vel_smooth),
                    (det.bbox[2] - old[2]) * self._vel_smooth + st["vel"][2] * (1 - self._vel_smooth),
                    (det.bbox[3] - old[3]) * self._vel_smooth + st["vel"][3] * (1 - self._vel_smooth),
                )
                st["bbox"] = det.bbox
                st["vel"] = vel
                st["age"] = 0
                st["label"] = det.label
                cx, cy = _center(det.bbox)
                st["traj"].append((round(cx, 3), round(cy, 3)))
                if len(st["traj"]) > 20:
                    st["traj"].pop(0)
                used.add(best_id)
                out.append(Track(best_id, det.bbox, det.confidence, (cx, cy), st["traj"][-20:], det.label))
            else:
                tid = self._next_id(camera_id)
                cx, cy = _center(det.bbox)
                self._state[tid] = {
                    "bbox": det.bbox,
                    "vel": (0.0, 0.0, 0.0, 0.0),
                    "age": 0,
                    "label": det.label,
                    "traj": [(round(cx, 3), round(cy, 3))],
                }
                out.append(Track(tid, det.bbox, det.confidence, (cx, cy), self._state[tid]["traj"], det.label))

        # Age unmatched tracks; drop when too old.
        for tid in list(self._state):
            if tid not in used:
                self._state[tid]["age"] += 1
                if self._state[tid]["age"] > self._max_age:
                    del self._state[tid]
        return out
