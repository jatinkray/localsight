"""Domain + pipeline tests: event aggregation, identity classification, and the
full detection -> tracking -> event flow using the reference (no-GPU) modules.
"""
from __future__ import annotations

import datetime as dt

from packages.ai.detector_ref import SyntheticDetector
from packages.ai.face import ReferenceEmbedder, ReferenceFaceDetector
from packages.ai.matcher import VectorMatcher, cosine
from packages.ai.pipeline import CameraPipeline
from packages.ai.tracker import IouTracker
from packages.domain.events import classify_identity, merge_intervals


def test_classify_identity_bands():
    assert classify_identity(0.95, 0.85) == "known"
    assert classify_identity(0.83, 0.85) == "uncertain"   # within delta
    assert classify_identity(0.70, 0.85) == "unknown"
    assert classify_identity(None, 0.85) == "unknown"


def test_merge_intervals():
    base = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    ivs = [(base, base + dt.timedelta(seconds=5)),
           (base + dt.timedelta(seconds=6), base + dt.timedelta(seconds=10)),  # gap 1s <= 10
           (base + dt.timedelta(seconds=100), base + dt.timedelta(seconds=110))]
    merged = merge_intervals(ivs, gap=10)
    assert len(merged) == 2
    assert (merged[0][1] - merged[0][0]).total_seconds() == 10  # first two joined


def test_cosine_similarity():
    a = [1.0, 0.0, 0.0]
    assert abs(cosine(a, a) - 1.0) < 1e-9
    assert abs(cosine(a, [0.0, 1.0, 0.0])) < 1e-9


def test_pipeline_produces_deduplicated_events(client):
    rt = client.app.state.runtime
    # create a camera to satisfy FK expectations
    r = client.post("/api/cameras", json={"name": "cam-p"}, headers={"Authorization": _admin(client)})
    cam_id = r.json()["id"]

    detector = SyntheticDetector(fps=1, appear_seconds=6, gap_seconds=8)
    tracker = IouTracker(iou_threshold=0.3)
    face_chain = (ReferenceFaceDetector(), ReferenceEmbedder())
    matcher = VectorMatcher(threshold=0.85)
    pipeline = CameraPipeline(
        cam_id, detector, tracker, face_chain, matcher, rt.SessionLocal, rt.storage, rt.crypto,
        confidence_threshold=0.1, merge_gap_seconds=2.0,
        recognize_interval_sec=1.0, identity_recognition_enabled=False,
    )

    start = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    closed: list[tuple[str, str]] = []
    with rt.SessionLocal() as session:
        for i in range(25):  # ~25s; one appearance then a gap -> one closed event
            ts = start + dt.timedelta(seconds=i)
            for ev in pipeline.process_frame(session, None, ts):
                session.commit()
                closed.append((ev.id, ev.identity_status))

    assert len(closed) >= 1
    _id, status = closed[0]
    assert status == "unknown"  # no recognition in reference mode


def _admin(client):
    r = client.post("/api/auth/login", json={"email": "admin@test.com", "password": "Sup3rStr0ngPw!"})
    return f"Bearer {r.json()['access_token']}"
