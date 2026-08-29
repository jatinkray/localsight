"""Per-camera AI pipeline orchestrator.

Flow (configurable, per spec):
  RTSP -> decode -> [motion gate] -> frame sampling -> person detection ->
  tracking -> face detection (tracked people only) -> quality filter ->
  embedding -> identity search (throttled per track) -> event aggregation.

The pipeline is the *only* place that owns per-track state and turns a stream of
frames into deduplicated presence events. It is intentionally decoupled from the
transport (FFmpeg/Synthetic) and the models (swappable interfaces).

Recognition is optional and throttled (default every 2s per active track), never
run on every frame. Embeddings are decrypted from the DB only for the matcher and
never logged.
"""
from __future__ import annotations

import datetime as dt
import json
from typing import List, Optional, Tuple

from packages.ai.interfaces import (
    Detector,
    FaceDetector,
    FaceEmbedder,
    IdentityMatcher,
    Track,
)
from packages.domain.events import classify_identity
from packages.domain.models import (
    Detection as DetectionRow,
    Event as EventRow,
    PersonEmbedding,
    Snapshot,
    Track as TrackRow,
)
from packages.security.crypto import CryptoBox
from packages.storage.base import StorageProvider

_ENROLLED_TTL = 30.0  # seconds before re-reading enrolled embeddings


class _TrackState:
    __slots__ = ("first_seen", "last_seen", "confidence", "bbox", "trajectory",
                 "seen_this_frame", "last_recognized", "recognition")

    def __init__(self, ts, bbox, confidence, trajectory):
        self.first_seen = ts
        self.last_seen = ts
        self.confidence = confidence
        self.bbox = bbox
        self.trajectory = trajectory
        self.seen_this_frame = True
        self.last_recognized: Optional[dt.datetime] = None
        self.recognition: Optional[Tuple[Optional[str], Optional[float], str]] = None


class CameraPipeline:
    def __init__(
        self,
        camera_id: str,
        detector: Detector,
        tracker,
        face_chain: Optional[Tuple[FaceDetector, FaceEmbedder]],
        matcher: Optional[IdentityMatcher],
        session_factory,
        storage: StorageProvider,
        crypto: CryptoBox,
        *,
        confidence_threshold: float = 0.45,
        merge_gap_seconds: float = 10.0,
        recognize_interval_sec: float = 2.0,
        model_version: str = "ref-v0",
        identity_recognition_enabled: bool = False,
    ) -> None:
        self.camera_id = camera_id
        self.detector = detector
        self.tracker = tracker
        self.face_detector, self.embedder = face_chain or (None, None)
        self.matcher = matcher
        self.session_factory = session_factory
        self.storage = storage
        self.crypto = crypto
        self.confidence = confidence_threshold
        self.merge_gap = merge_gap_seconds
        self.recognize_interval = recognize_interval_sec
        self.model_version = model_version
        self.recognition_enabled = bool(identity_recognition_enabled and face_chain and matcher)

        self._active: dict[str, _TrackState] = {}
        self._enrolled: List[Tuple[str, List[float], str]] = []
        self._enrolled_at: float = 0.0

    # ── enrolled embeddings (decrypted, cached) ─────────────────────────────
    def _refresh_enrolled(self, session) -> None:
        rows = session.query(PersonEmbedding).all()
        enrolled = []
        for r in rows:
            try:
                vec = self.crypto.decrypt_json(r.embedding_enc)
            except Exception:  # noqa: BLE001 - skip unreadable embeddings
                continue
            enrolled.append((r.person_id, vec, r.model_version))
        self._enrolled = enrolled
        self._enrolled_at = dt.datetime.now(dt.timezone.utc).timestamp()

    # ── core frame processing (testable) ───────────────────────────────────
    def process_frame(self, session, frame, ts: dt.datetime) -> List[EventRow]:
        if dt.datetime.now(dt.timezone.utc).timestamp() - self._enrolled_at > _ENROLLED_TTL:
            self._refresh_enrolled(session)

        for st in self._active.values():
            st.seen_this_frame = False

        raw = self.detector.detect(frame, ts)
        detections = [d for d in raw if d.confidence >= self.confidence]
        tracks = self.tracker.update(self.camera_id, detections, ts)
        closed: List[EventRow] = []

        for tr in tracks:
            st = self._active.get(tr.track_id)
            if st is None:
                st = _TrackState(ts, tr.bbox, tr.confidence, list(tr.trajectory))
                self._active[tr.track_id] = st
            st.seen_this_frame = True
            st.last_seen = ts
            st.bbox = tr.bbox
            st.confidence = max(st.confidence, tr.confidence)

            if self.recognition_enabled and self._should_recognize(st, ts):
                st.last_recognized = ts
                rec = self._recognize(frame, tr)
                st.recognition = (rec.person_id, rec.similarity, rec.status)

        # persist detections (sampled, not every frame's raw boxes)
        for tr in tracks:
            session.add(
                DetectionRow(
                    camera_id=self.camera_id,
                    track_id=tr.track_id,
                    frame_ts=ts,
                    label="person",
                    confidence=tr.confidence,
                    bbox={"x": tr.bbox[0], "y": tr.bbox[1], "w": tr.bbox[2], "h": tr.bbox[3]},
                )
            )

        # close stale tracks -> events
        for tid, st in list(self._active.items()):
            if st.seen_this_frame:
                continue
            gap = (ts - st.last_seen).total_seconds()
            if gap >= self.merge_gap:
                event = self._finalize(session, tid, st)
                if event:
                    closed.append(event)
                del self._active[tid]

        # upsert active tracks
        self._upsert_tracks(session)
        return closed

    def _should_recognize(self, st: _TrackState, ts: dt.datetime) -> bool:
        if st.last_recognized is None:
            return True
        return (ts - st.last_recognized).total_seconds() >= self.recognize_interval

    def _recognize(self, frame, tr: Track):
        assert self.face_detector and self.embedder and self.matcher
        face = self.face_detector.detect(frame, tr.bbox)
        if not face:
            return type("R", (), {"person_id": None, "similarity": None, "status": "unknown"})()
        vec = self.embedder.embed(frame, face)
        return self.matcher.search(vec, self.model_version, self._enrolled)

    def _finalize(self, session, tid: str, st: _TrackState) -> Optional[EventRow]:
        pid, sim, status = st.recognition or (None, None, "unknown")
        event = EventRow(
            camera_id=self.camera_id,
            track_id=tid,
            identity_id=pid if status == "known" else None,
            identity_status=status,
            event_type="presence",
            timestamp_start=st.first_seen,
            timestamp_end=st.last_seen,
            confidence=st.confidence,
            bbox={"x": st.bbox[0], "y": st.bbox[1], "w": st.bbox[2], "h": st.bbox[3]},
        )
        session.add(event)
        session.flush()  # populate event.id
        # store an encrypted snapshot reference (no raw face retained by default)
        snap_key = f"snapshots/{self.camera_id}/{event.id}.json"
        payload = json.dumps(
            {"track_id": tid, "identity_status": status, "ts": st.last_seen.isoformat()}
        ).encode()
        self.storage.put(snap_key, payload, "application/json")
        event.snapshot_key_enc = self.crypto.encrypt_str(snap_key)
        session.add(Snapshot(camera_id=self.camera_id, track_id=tid, event_id=event.id,
                             storage_key_enc=self.crypto.encrypt_str(snap_key)))
        return event

    def _upsert_tracks(self, session) -> None:
        for tid, st in self._active.items():
            pid, sim, status = st.recognition or (None, None, "unknown")
            row = session.get(TrackRow, tid)
            if row is None:
                row = TrackRow(id=tid, camera_id=self.camera_id)
                session.add(row)
            row.identity_id = pid if status == "known" else None
            row.identity_status = status
            row.first_seen = st.first_seen
            row.last_seen = st.last_seen
            row.confidence = st.confidence
            row.bbox = {"x": st.bbox[0], "y": st.bbox[1], "w": st.bbox[2], "h": st.bbox[3]}
            row.trajectory = st.trajectory
