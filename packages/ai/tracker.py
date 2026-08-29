"""Reference multi-object tracker (IOU association).

Production can replace this with DeepSORT/BotSORT; the interface is unchanged.
Track IDs are ephemeral and must never be confused with a real identity.
"""
from __future__ import annotations

import itertools
from typing import List

from packages.ai.interfaces import Detection, Track, Tracker


def _iou(a: tuple, b: tuple) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


class IouTracker(Tracker):
    def __init__(self, iou_threshold: float = 0.5, max_age: int = 30):
        self._iou = iou_threshold
        self._max_age = max_age
        self._state: dict[str, dict] = {}  # track_id -> {bbox, age, counter}
        self._counters: dict[str, int] = {}

    def _next_id(self, camera_id: str) -> str:
        n = self._counters.get(camera_id, 0) + 1
        self._counters[camera_id] = n
        return f"{camera_id}-track-{n}"

    def update(self, camera_id: str, detections: List[Detection], ts) -> List[Track]:
        out: List[Track] = []
        used = set()
        # match detections to existing tracks by best IOU
        for det in detections:
            best_id, best_iou = None, 0.0
            for tid, st in self._state.items():
                if tid in used:
                    continue
                score = _iou(det.bbox, st["bbox"])
                if score > best_iou:
                    best_iou, best_id = score, tid
            if best_id and best_iou >= self._iou:
                st = self._state[best_id]
                st["bbox"] = det.bbox
                st["age"] = 0
                used.add(best_id)
                cx = det.bbox[0] + det.bbox[2] / 2
                cy = det.bbox[1] + det.bbox[3] / 2
                st["traj"].append((round(cx, 3), round(cy, 3)))
                out.append(Track(best_id, det.bbox, det.confidence, (cx, cy), st["traj"][-20:]))
            else:
                tid = self._next_id(camera_id)
                cx = det.bbox[0] + det.bbox[2] / 2
                cy = det.bbox[1] + det.bbox[3] / 2
                self._state[tid] = {
                    "bbox": det.bbox,
                    "age": 0,
                    "traj": [(round(cx, 3), round(cy, 3))],
                }
                out.append(Track(tid, det.bbox, det.confidence, (cx, cy), self._state[tid]["traj"]))
        # age unmatched tracks
        for tid in list(self._state):
            if tid not in used:
                self._state[tid]["age"] += 1
                if self._state[tid]["age"] > self._max_age:
                    del self._state[tid]
        return out
