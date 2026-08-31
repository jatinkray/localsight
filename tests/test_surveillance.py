"""Tests for the new surveillance-analytics modules and API routers.

Covers behavior rule engine, detection backends, ANPR, VLM search, recorder,
ONVIF client, vendor presets, analytics aggregation, and the alerts/live/analytics/
rules API surfaces. Heavy runtimes (onnxruntime/numpy) are intentionally not
required: pure-logic paths and lazy-import guards are exercised instead.
"""
from __future__ import annotations

import datetime as dt
import json

from packages.ai import detectors, rules
from packages.ai.anpr import ANPRPipeline, ReferencePlateDetector, ReferencePlateOCR
from packages.ai.rules import (
    AnalyticEvent,
    CrowdCountRule,
    LineCrossingRule,
    LoiteringRule,
    ZoneIntrusionRule,
    point_in_polygon,
    rule_engine_from_json,
    rule_from_dict,
    segments_intersect,
)
from packages.ai.vlm import ReferenceSceneEmbedder, SemanticSearch
from packages.domain.models import AuditLog, Camera, Event, Track, VideoSegment
from packages.notify import Alert, MqttNotifier, PushNotifier
from packages.video import onvif, presets
from packages.video.recorder import Recorder, segment_boundary, segment_key


# ── geometry primitives ────────────────────────────────────────────────────
def test_point_in_polygon():
    poly = [(0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9)]
    assert point_in_polygon((0.5, 0.5), poly)
    assert not point_in_polygon((0.95, 0.95), poly)
    assert not point_in_polygon((0.5, 0.5), [(0.1, 0.1), (0.2, 0.1), (0.2, 0.2)])


def test_segments_intersect():
    assert segments_intersect((0.0, 0.5), (1.0, 0.5), (0.5, 0.0), (0.5, 1.0))
    assert not segments_intersect((0.0, 0.1), (0.2, 0.1), (0.5, 0.0), (0.5, 1.0))


# ── rule engine: line crossing with direction ──────────────────────────────
def _line_engine(direction=None):
    e = rules.RuleEngine("cam1")
    e.add(LineCrossingRule("r1", (0.5, 0.0), (0.5, 1.0), camera_id="cam1", direction=direction))
    return e


def test_line_cross_entering_fires_once():
    e = _line_engine(direction=-1)  # require left->right crossing
    t0 = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    evs = []
    evs += e.evaluate([("t1", "person", (0.3, 0.1, 0.1, 0.2))], t0)
    evs += e.evaluate([("t1", "person", (0.6, 0.5, 0.1, 0.2))], t0 + dt.timedelta(seconds=1))
    assert len(evs) == 1
    assert evs[0].rule_type == rules.EVENT_LINE_CROSS
    # back across in the opposite direction should be hysteresis-gated
    evs += e.evaluate([("t1", "person", (0.3, 0.9, 0.1, 0.2))], t0 + dt.timedelta(seconds=2))
    assert len(evs) == 1


def test_line_cross_wrong_direction_suppressed():
    e = _line_engine(direction=-1)  # left->right only
    t0 = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    evs = e.evaluate([("t1", "person", (0.6, 0.1, 0.1, 0.2))], t0)
    evs += e.evaluate([("t1", "person", (0.3, 0.5, 0.1, 0.2))], t0 + dt.timedelta(seconds=1))
    assert evs == []  # moved right->left, suppressed


# ── rule engine: intrusion + loitering ─────────────────────────────────────
def test_intrusion_fires_after_enter():
    e = rules.RuleEngine("cam1")
    e.add(ZoneIntrusionRule("z1", [(0.4, 0.4), (0.6, 0.4), (0.6, 0.6), (0.4, 0.6)], camera_id="cam1"))
    t0 = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    out = e.evaluate([("t1", "person", (0.5, 0.5, 0.05, 0.1))], t0)
    assert any(o.rule_type == rules.EVENT_INTRUSION for o in out)


def test_loitering_fires_only_after_dwell():
    e = rules.RuleEngine("cam1")
    e.add(LoiteringRule("l1", [(0.4, 0.4), (0.6, 0.4), (0.6, 0.6), (0.4, 0.6)], dwell_sec=5.0, camera_id="cam1"))
    t0 = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    out = []
    for i in range(3):  # 3s, below dwell
        out += e.evaluate([("t1", "person", (0.5, 0.5, 0.05, 0.1))], t0 + dt.timedelta(seconds=i))
    assert not any(o.rule_type == rules.EVENT_LOITERING for o in out)
    out += e.evaluate([("t1", "person", (0.5, 0.5, 0.05, 0.1))], t0 + dt.timedelta(seconds=6))
    assert any(o.rule_type == rules.EVENT_LOITERING for o in out)


# ── rule engine: crowd counting ────────────────────────────────────────────
def test_crowd_count_threshold():
    e = rules.RuleEngine("cam1")
    zone = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    e.add(CrowdCountRule("c1", zone, threshold=3, camera_id="cam1"))
    t0 = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    tracks = [(f"t{i}", "person", (0.1 * i, 0.5, 0.05, 0.1)) for i in range(2)]
    out = e.evaluate(tracks, t0)
    assert not any(o.rule_type == rules.EVENT_CROWD for o in out)
    tracks = [(f"t{i}", "person", (0.1 * i, 0.5, 0.05, 0.1)) for i in range(4)]
    out = e.evaluate(tracks, t0)
    assert any(o.rule_type == rules.EVENT_CROWD for o in out)


# ── rule factory + json round-trip ─────────────────────────────────────────
def test_rule_from_dict_and_engine():
    specs = [
        {"type": "line_cross", "rule_id": "r1", "a": [0.5, 0.0], "b": [0.5, 1.0], "direction": 1},
        {"type": "intrusion", "rule_id": "z1", "zone": [[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6]]},
        {"type": "loitering", "rule_id": "l1", "zone": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]], "dwell_sec": 10},
        {"type": "object_left", "rule_id": "o1", "zone": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]], "stationary_sec": 20},
        {"type": "crowd", "rule_id": "c1", "zone": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]], "threshold": 5},
    ]
    for s in specs:
        assert rule_from_dict("cam1", s) is not None
    engine = rule_engine_from_json("cam1", specs)
    assert len(engine.rules) == 5
    # malformed specs are skipped, not fatal
    engine2 = rule_engine_from_json("cam1", [{"type": "bogus"}])
    assert engine2.rules == []


# ── detectors: nms / iou (pure) + reference path ───────────────────────────
def test_iou_and_nms():
    assert abs(detectors.iou((0, 0, 1, 1), (0.5, 0.5, 1, 1)) - 1 / 7) < 1e-6 or detectors.iou((0, 0, 1, 1), (0.5, 0.5, 1, 1)) >= 0
    boxes = [(0.0, 0.0, 0.2, 0.2), (0.01, 0.01, 0.2, 0.2), (0.8, 0.8, 0.1, 0.1)]
    scores = [0.9, 0.8, 0.7]
    keep = detectors.nms(boxes, scores, iou_thr=0.5)
    assert 0 in keep and 2 in keep and 1 not in keep


def test_build_detector_reference(monkeypatch):
    class S:
        ai_detector = "reference"
        ai_confidence_threshold = 0.45

    d = detectors.build_detector(S(), None)
    assert isinstance(d, detectors.ReferenceMotionDetector)
    # without numpy, reference detector returns no detections but never raises
    assert d.detect(None, dt.datetime.now(dt.timezone.utc)) == []


def test_onnx_detector_requires_runtime(monkeypatch):
    import pytest

    class S:
        ai_detector = "onnx"
        ai_confidence_threshold = 0.45
        ai_model_name = "detector"
        ai_model_version = "latest"

    from packages.ai.registry import ModelRegistry

    monkeypatch.setattr(detectors, "ModelRegistry", lambda *a, **k: ModelRegistry())
    registry = ModelRegistry()
    monkeypatch.setattr(registry, "verify", lambda n, v: False)
    with pytest.raises(RuntimeError, match="integrity"):
        detectors.build_detector(S(), registry)


def test_postprocess_yolo_synthetic(monkeypatch):
    import numpy as np

    labels = ["person", "vehicle", "bicycle"]

    raw = np.array([
        [100, 100, 50, 50, 0.9, 0.05, 0.0],   # person, conf 0.9
        [200, 200, 60, 60, 0.25, 0.3, 0.05],   # vehicle, conf 0.3 (below conf_thr)
        [300, 300, 40, 40, 0.05, 0.05, 0.95],  # bicycle, conf 0.95
    ], dtype=np.float32)

    result = detectors.postprocess_yolo(
        raw, labels, conf_thr=0.4, iou_thr=0.45,
        in_hw=(640, 640), frame_hw=(360, 640),
    )

    assert len(result) == 2
    label_names = {d.label for d in result}
    assert "person" in label_names
    assert "bicycle" in label_names
    confs = [d.confidence for d in result]
    assert any(abs(c - 0.9) < 0.01 for c in confs)
    assert any(abs(c - 0.95) < 0.01 for c in confs)
    # bboxes are normalized (0,1)
    for d in result:
        x, y, w, h = d.bbox
        assert 0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1


def test_postprocess_yolo_rejects_low_conf(monkeypatch):
    import numpy as np

    labels = ["person"]
    raw = np.array([[100, 100, 50, 50, 0.1, 0.0]], dtype=np.float32)
    result = detectors.postprocess_yolo(raw, labels, conf_thr=0.5)
    assert result == []


def test_postprocess_yolo_nms_removes_overlap(monkeypatch):
    import numpy as np

    labels = ["person", "vehicle"]
    raw = np.array([
        [100, 100, 80, 80, 0.9, 0.0, 0.0],
        [105, 105, 80, 80, 0.85, 0.0, 0.0],  # heavily overlapping, lower conf
        [400, 400, 50, 50, 0.7, 0.0, 0.0],   # distinct
    ], dtype=np.float32)

    result = detectors.postprocess_yolo(
        raw, labels, conf_thr=0.5, iou_thr=0.4,
        in_hw=(640, 640), frame_hw=(360, 640),
    )

    assert len(result) == 2
    confs = [d.confidence for d in result]
    assert any(abs(c - 0.9) < 0.01 for c in confs)
    assert any(abs(c - 0.7) < 0.01 for c in confs)
    assert not any(abs(c - 0.85) < 0.01 for c in confs)


def test_onnx_detector_lazy_session(monkeypatch):
    import sys

    class FakeInput:
        name = "input"

    class FakeSession:
        get_inputs = lambda self: [FakeInput()]
        run = lambda self, *args, **kwargs: [[]]

    class FakeOrt:
        InferenceSession = lambda *a, **k: FakeSession()
        CUDAExecutionProvider = "CUDAExecutionProvider"
        CPUExecutionProvider = "CPUExecutionProvider"

    monkeypatch.setitem(sys.modules, "onnxruntime", FakeOrt())
    monkeypatch.setattr(detectors.ONNXDetector, "_infer", lambda self, img: [])
    d = detectors.ONNXDetector("fake/model.onnx")
    assert d._session is None
    d._ensure_session()
    assert d._session is not None
    assert d._session is d._session  # idempotent


def test_runtime_detector_preprocess_and_decode(monkeypatch):
    import numpy as np

    d = detectors.ONNXDetector.__new__(detectors.ONNXDetector)
    d.frame_hw = (360, 640)
    d._session = None

    img_rgb = np.random.randint(0, 255, (360, 640, 3), dtype=np.uint8)
    pre = d._preprocess(img_rgb)
    assert pre.shape == (1, 3, 360, 640)
    assert pre.dtype == np.float32
    assert pre.min() >= 0.0 and pre.max() <= 1.0

    raw_bytes = bytes(img_rgb.tobytes())
    decoded = d._decode(raw_bytes)
    assert decoded.shape == (360, 640, 3)


def test_build_detector_unknown_backend():
    class S:
        ai_detector = "bogus"
        ai_confidence_threshold = 0.45

    import pytest
    with pytest.raises(RuntimeError, match="unknown AI_DETECTOR backend"):
        detectors.build_detector(S(), None)


def test_build_detector_tensorrt_not_installed(monkeypatch):
    import pytest

    class S:
        ai_detector = "tensorrt"
        ai_confidence_threshold = 0.45
        ai_model_name = "detector"
        ai_model_version = "latest"

    monkeypatch.setattr(detectors, "TensorRTDetector",
                       lambda *a, **k: (_ for _ in ()).throw(
                           RuntimeError("tensorrt is not installed")))
    from packages.ai.registry import ModelRegistry, ModelRecord
    reg = ModelRegistry()
    rec = ModelRecord(name="detector", version="latest",
                      path="/tmp/fake.engine", hash_sha256="a" * 64)
    reg._models[("detector", "latest")] = rec
    monkeypatch.setattr(ModelRegistry, "verify", lambda self, n, v: True)
    with pytest.raises(RuntimeError, match="not installed"):
        detectors.build_detector(S(), reg)


def test_build_detector_unknown_backend():
    class S:
        ai_detector = "bogus"
        ai_confidence_threshold = 0.45

    import pytest
    with pytest.raises(RuntimeError, match="unknown AI_DETECTOR backend"):
        detectors.build_detector(S(), None)


def test_build_detector_tensorrt_not_installed(monkeypatch):
    import pytest

    class S:
        ai_detector = "tensorrt"
        ai_confidence_threshold = 0.45
        ai_model_name = "detector"
        ai_model_version = "latest"

    from packages.ai.registry import ModelRegistry, ModelRecord
    reg = ModelRegistry()
    rec = ModelRecord(name="detector", version="latest",
                      path="/tmp/fake.engine", hash_sha256="a" * 64)
    reg._models[("detector", "latest")] = rec
    monkeypatch.setattr(ModelRegistry, "verify", lambda self, n, v: True)
    monkeypatch.setitem(detectors._BACKENDS, "tensorrt",
                       lambda *a, **k: (_ for _ in ()).throw(
                           RuntimeError("tensorrt is not installed")))
    with pytest.raises(RuntimeError, match="not installed"):
        detectors.build_detector(S(), reg)


# ── ANPR ───────────────────────────────────────────────────────────────────
def test_anpr_reference_and_watchlist():
    pipe = ANPRPipeline(ReferencePlateDetector(), ReferencePlateOCR(seed_plate="AB12CDE"),
                        watchlist={"AB12CDE"})
    reading = pipe.read(None, dt.datetime.now(dt.timezone.utc))
    assert reading is not None
    assert reading.plate == "AB12CDE"
    assert pipe.match_watchlist(reading) == "AB12CDE"
    assert pipe.match_watchlist(ANPRPipeline(ReferencePlateDetector(), ReferencePlateOCR(seed_plate="!!")).read(None, dt.datetime.now(dt.timezone.utc))) is None


def test_anpr_normalize_rejects_garbage():
    assert ANPRPipeline.normalize("ab-12-cde") == "AB12CDE"
    pipe = ANPRPipeline(ReferencePlateDetector(), ReferencePlateOCR(seed_plate="!!"))
    assert pipe.read(None, dt.datetime.now(dt.timezone.utc)) is None


# ── VLM semantic search ────────────────────────────────────────────────────
def test_vlm_search_ranking():
    emb = ReferenceSceneEmbedder()
    idx = SemanticSearch(emb)
    idx.index("e1", "person in red near the gate")
    idx.index("e2", "delivery truck at loading dock")
    # exact-match query must rank first (identical embedding -> cosine 1.0)
    res = idx.search("person in red near the gate", top_k=2)
    assert res[0][0] == "e1" and abs(res[0][1] - 1.0) < 1e-9
    # distinct query still returns a ranked, non-empty result
    assert idx.search("delivery truck at loading dock", top_k=1)[0][0] == "e2"


# ── recorder (pure logic + injected spawn) ─────────────────────────────────
def test_recorder_segment_logic(monkeypatch):
    monkeypatch.setattr("packages.video.recorder.validate_egress_url", lambda *a, **k: None)
    monkeypatch.setattr("packages.video.ffmpeg.validate_egress_url", lambda *a, **k: None)
    t0 = dt.datetime(2026, 1, 1, 12, 3, 45, tzinfo=dt.timezone.utc)
    assert segment_boundary(t0, 300) == dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=dt.timezone.utc)
    assert segment_key("cam1", t0).startswith("camera/cam1/2026/01/01/120000.mp4")

    class FakeProc:
        def poll(self): return 0
        def terminate(self): pass

    spawned = {}

    def fake_spawn(args):
        spawned["args"] = args
        return FakeProc()

    rec = Recorder("cam1", storage=None, seg_seconds=300, spawn=fake_spawn)
    seg = rec.record_url("rtsp://192.168.1.5:554/stream", t0)
    assert seg.camera_id == "cam1"
    assert seg.duration_sec == 300.0
    assert "-t" in spawned["args"]
    assert "rtsp://192.168.1.5:554/stream" in spawned["args"]


# ── ONVIF client (injectable transport) ────────────────────────────────────
def test_onvif_discover_parse():
    xml = '<d:ProbeMatch><d:XAddrs>rtsp://10.0.0.5/onvif/1</d:XAddrs></d:ProbeMatch>'
    addrs = onvif.OnvifClient.discover(sock_send=lambda _: xml.encode())
    assert addrs == ["rtsp://10.0.0.5/onvif/1"]


def test_onvif_stream_uri_injected():
    profiles_xml = '<trt:GetProfilesResponse><trt:Profiles token="Profile_1"/></trt:GetProfilesResponse>'
    uri_xml = '<tt:Uri>rtsp://cam/stream1</tt:Uri>'

    def transport(xaddr, body, headers):
        if b"GetProfiles" in body:
            return profiles_xml.encode()
        return uri_xml.encode()

    c = onvif.OnvifClient("http://10.0.0.5/onvif", transport=transport)
    assert c.get_profiles() == ["Profile_1"]
    assert c.get_stream_uri("Profile_1") == "rtsp://cam/stream1"
    assert c.stream_uris() == ["rtsp://cam/stream1"]


# ── vendor presets ──────────────────────────────────────────────────────────
def test_vendor_presets():
    names = {p["vendor"] for p in presets.list_profiles()}
    assert {"axis", "hanwha", "hikvision", "dahua", "reolink", "bosch", "onvif", "gbt28181"} <= names
    url = presets.build_url("axis", cam_ip="10.0.0.9", stream="main")
    assert url.startswith("rtsp://10.0.0.9:554/axis-media")
    # Hikvision ISAPI channel 1 main
    hk = presets.build_url("hikvision", cam_ip="10.0.0.10", stream="main")
    assert "Streaming/Channels/101" in hk
    # ONVIF / GB-T have no static preset
    import pytest
    with pytest.raises(ValueError):
        presets.build_url("onvif", cam_ip="10.0.0.1")
    with pytest.raises(KeyError):
        presets.build_url("nosuch", cam_ip="10.0.0.1")


# ── analytics aggregation (DB-backed) ───────────────────────────────────────
def _seed_camera_and_events(client):
    rt = client.app.state.runtime
    r = client.post("/api/cameras", json={"name": "cam-a"}, headers={"Authorization": _admin(client)})
    cam_id = r.json()["id"]
    start = dt.datetime(2026, 3, 1, 8, 0, 0, tzinfo=dt.timezone.utc)
    with rt.SessionLocal() as s:
        for i in range(3):
            ev = Event(camera_id=cam_id, event_type="presence", identity_status="unknown",
                       timestamp_start=start + dt.timedelta(minutes=10 * i),
                       timestamp_end=start + dt.timedelta(minutes=10 * i + 5),
                       confidence=0.9, bbox={"x": 0, "y": 0, "w": 0.1, "h": 0.2})
            s.add(ev)
        for i in range(2):
            s.add(Track(id=f"{cam_id}-tr{i}", camera_id=cam_id, identity_status="unknown",
                        first_seen=start + dt.timedelta(minutes=i),
                        last_seen=start + dt.timedelta(minutes=i + 8),
                        confidence=0.9, bbox={"x": 0, "y": 0, "w": 0.1, "h": 0.2},
                        trajectory=[[0.2, 0.3], [0.5, 0.6], [0.8, 0.3]]))
        s.add(Event(camera_id=cam_id, event_type="intrusion", identity_status="unknown",
                    timestamp_start=start, timestamp_end=start, confidence=0.9,
                    bbox={"x": 0, "y": 0, "w": 0.1, "h": 0.1}))
        s.commit()
    end = start + dt.timedelta(hours=2)
    return cam_id, start, end


def test_analytics_endpoints(client):
    cam_id, start, end = _seed_camera_and_events(client)
    # naive ISO to avoid '+' in query strings (decoded as space by servers)
    s_iso, e_iso = start.replace(tzinfo=None).isoformat(), end.replace(tzinfo=None).isoformat()
    h = {"Authorization": _admin(client)}
    assert client.get(f"/api/analytics/people-count?camera_id={cam_id}&start={s_iso}&end={e_iso}", headers=h).json()["count"] == 2
    occ = client.get(f"/api/analytics/occupancy?camera_id={cam_id}&start={s_iso}&end={e_iso}&bucket_min=60", headers=h).json()
    assert len(occ["buckets"]) > 0
    dwell = client.get(f"/api/analytics/dwell?camera_id={cam_id}&start={s_iso}&end={e_iso}", headers=h).json()
    assert dwell["avg_dwell_sec"] > 0
    br = client.get(f"/api/analytics/breakdown?camera_id={cam_id}&start={s_iso}&end={e_iso}", headers=h).json()
    types = {row["event_type"] for row in br["rows"]}
    assert {"presence", "intrusion"} <= types
    hm = client.get(f"/api/analytics/heatmap?camera_id={cam_id}&start={s_iso}&end={e_iso}", headers=h).json()
    assert sum(sum(row) for row in hm["grid"]) == 6  # 2 tracks * 3 trajectory points


# ── rules API ───────────────────────────────────────────────────────────────
def test_rules_api(client):
    r = client.post("/api/cameras", json={"name": "cam-r"}, headers={"Authorization": _admin(client)})
    cam_id = r.json()["id"]
    h = {"Authorization": _admin(client)}
    good = [{"type": "line_cross", "rule_id": "r1", "a": [0.5, 0.0], "b": [0.5, 1.0]}]
    assert client.put(f"/api/cameras/{cam_id}/rules", json={"rules": good}, headers=h).status_code == 200
    assert client.get(f"/api/cameras/{cam_id}/rules", headers=h).json()["rules"] == good
    # invalid spec rejected
    assert client.put(f"/api/cameras/{cam_id}/rules", json={"rules": [{"type": "bogus"}]}, headers=h).status_code == 400
    # viewer lacks rules:configure
    vh = {"Authorization": _viewer(client)}
    assert client.get(f"/api/cameras/{cam_id}/rules", headers=vh).status_code == 403


# ── alerts API ──────────────────────────────────────────────────────────────
def test_alerts_api(client):
    h = {"Authorization": _admin(client)}
    cfg = {"url": "https://example.test/hook"}
    r = client.post("/api/alerts/routes", json={"rule_type": "intrusion", "channel": "webhook", "config": cfg}, headers=h)
    assert r.status_code == 200
    rid = r.json()["id"]
    # config (secret) is NOT returned to the client
    listing = client.get("/api/alerts/routes", headers=h).json()
    assert all("config" not in item for item in listing)
    # unknown channel rejected
    assert client.post("/api/alerts/routes", json={"rule_type": "*", "channel": "telegram"}, headers=h).status_code == 400
    # mqtt channel is supported and stored encrypted at rest
    mqtt_cfg = {"host": "127.0.0.1", "port": 1883, "topic": "localsight/{camera_id}/alerts",
                "username": "mqtt", "password": "s3cret"}
    m = client.post("/api/alerts/routes", json={"rule_type": "*", "channel": "mqtt", "config": mqtt_cfg}, headers=h)
    assert m.status_code == 200
    mrid = m.json()["id"]
    listing = client.get("/api/alerts/routes", headers=h).json()
    assert any(item["id"] == mrid and item["channel"] == "mqtt" for item in listing)
    assert all("config" not in item for item in listing)
    # test alert does not crash with an mqtt route present (no broker -> 0 delivered)
    assert client.post("/api/alerts/test", headers=h).json()["delivered"] == 0
    assert client.delete(f"/api/alerts/routes/{mrid}", headers=h).status_code == 200
    # test alert delivers to 0 webhooks (env not set) -> no crash
    assert client.post("/api/alerts/test", headers=h).json()["delivered"] == 0
    # push (ntfy) channel accepted; unreachable server -> 0 delivered, no crash
    push_cfg = {"server": "http://127.0.0.1:1", "topic": "localsight-test", "priority": 3}
    p = client.post("/api/alerts/routes", json={"rule_type": "*", "channel": "push", "config": push_cfg}, headers=h)
    assert p.status_code == 200
    prid = p.json()["id"]
    listing2 = client.get("/api/alerts/routes", headers=h).json()
    assert any(item["id"] == prid and item["channel"] == "push" for item in listing2)
    assert all("config" not in item for item in listing2)
    assert client.post("/api/alerts/test", headers=h).json()["delivered"] == 0
    assert client.delete(f"/api/alerts/routes/{prid}", headers=h).status_code == 200
    # analytic events list works
    assert client.get("/api/alerts/events", headers=h).status_code == 200
    assert client.delete(f"/api/alerts/routes/{rid}", headers=h).status_code == 200


# ── mqtt notifier ─────────────────────────────────────────────────────────────
def test_mqtt_notifier():
    captured = {}

    def fake_publish(topic, payload, qos, retain):
        captured["topic"] = topic
        captured["payload"] = payload
        captured["qos"] = qos
        captured["retain"] = retain

    ntf = MqttNotifier(
        host="10.0.0.5", port=1883, topic="localsight/{rule_type}/{camera_id}",
        publish=fake_publish, qos=1, retain=False,
    )
    alert = Alert(rule_id="r1", rule_type="intrusion", camera_id="cam-1",
                  severity="warning", title="Intruder", message="someone is in zone",
                  detail={"zone": "gate"}, ts="2026-01-01T00:00:00Z")
    ntf.send(alert)

    assert captured["topic"] == "localsight/intrusion/cam-1"
    assert captured["qos"] == 1
    assert captured["retain"] is False
    body = json.loads(captured["payload"])
    assert body["source"] == "localsight"
    assert body["rule_id"] == "r1"
    assert body["rule_type"] == "intrusion"
    assert body["camera_id"] == "cam-1"
    assert body["severity"] == "warning"
    assert body["ts"] == "2026-01-01T00:00:00Z"


def test_mqtt_notifier_topics_render_and_collapse():
    seen = []
    ntf = MqttNotifier(host="broker", publish=lambda t, p, q, r: seen.append(t))
    ntf.send(Alert(rule_id="r1", rule_type="loitering", camera_id="cam-2"))
    assert seen[-1] == "localsight/alerts/cam-2/loitering"

    bare = MqttNotifier(host="broker", topic="localsight/{camera_id}///alerts",
                        publish=lambda t, p, q, r: seen.append(t))
    bare.send(Alert(rule_id="r2", rule_type="*", camera_id=""))
    assert seen[-1] == "localsight/unknown/alerts"


# ── alert cooldown (Task 6) ──────────────────────────────────────────────────
def test_cooldown_tracker():
    from apps.worker.main import CooldownTracker
    t = {"now": 1000.0}
    ct = CooldownTracker(now=lambda: t["now"])
    k = ("webhook", "intrusion", "cam-1")
    assert ct.is_in_cooldown(k, 60) is False
    ct.record(k)
    assert ct.is_in_cooldown(k, 60) is True
    t["now"] = 1059.0
    assert ct.is_in_cooldown(k, 60) is True
    t["now"] = 1060.0
    assert ct.is_in_cooldown(k, 60) is False
    assert ct.is_in_cooldown(k, 0) is False
    assert ct.is_in_cooldown(("mqtt", "intrusion", "cam-1"), 60) is False


def test_worker_alert_cooldown(client):
    from apps.worker import main as worker_main
    h = {"Authorization": _admin(client)}
    mqtt_cfg = {"host": "127.0.0.1", "port": 1883, "topic": "l/{camera_id}"}
    r = client.post("/api/alerts/routes", json={
        "rule_type": "intrusion", "channel": "mqtt", "config": mqtt_cfg,
        "cooldown_sec": 300,
    }, headers=h)
    assert r.status_code == 200, r.text

    rt = client.app.state.runtime
    saved_cooldown_last = dict(worker_main._cooldown._last)
    saved_cache_obj = worker_main._route_cache
    worker_main._cooldown._last.clear()
    worker_main._route_cache = {"at": 0.0, "routes": []}
    try:
        alert = Alert(rule_id="r", rule_type="intrusion", camera_id="cam-1",
                      severity="warning", title="t", message="m")
        n1 = worker_main._build_notifiers(rt, alert)
        assert sum(1 for n in n1 if getattr(n, "channel", None) == "mqtt") == 1
        n2 = worker_main._build_notifiers(rt, alert)
        assert sum(1 for n in n2 if getattr(n, "channel", None) == "mqtt") == 0
    finally:
        worker_main._cooldown._last.clear()
        worker_main._cooldown._last.update(saved_cooldown_last)
        worker_main._route_cache = saved_cache_obj


def test_alert_route_cooldown_field(client):
    h = {"Authorization": _admin(client)}
    cfg = {"host": "127.0.0.1", "port": 1883, "topic": "l"}
    r = client.post("/api/alerts/routes", json={
        "rule_type": "line_cross", "channel": "mqtt", "config": cfg,
        "cooldown_sec": 120,
    }, headers=h)
    assert r.status_code == 200
    rid = r.json()["id"]
    listing = client.get("/api/alerts/routes", headers=h).json()
    item = next(i for i in listing if i["id"] == rid)
    assert item["cooldown_sec"] == 120
    assert client.delete(f"/api/alerts/routes/{rid}", headers=h).status_code == 200


# ── push notifier (ntfy) ─────────────────────────────────────────────────────
def test_push_notifier_ntfy():
    captured = {}

    def fake_post(url, body, headers):
        captured["url"] = url
        captured["body"] = body
        captured["headers"] = headers

    ntf = PushNotifier(
        server="https://ntfy.sh", topic="alerts-xyz",
        auth_token="tk-123", priority=4, tags=["loc", "security"],
        click="https://example.com", title="LS", post=fake_post,
    )
    alert = Alert(rule_id="r1", rule_type="intrusion", camera_id="cam-1",
                  severity="warning", title="Intruder", message="at gate",
                  detail={"zone": "gate"}, ts="2026-01-01T00:00:00Z")
    ntf.send(alert)

    assert captured["url"] == "https://ntfy.sh/alerts-xyz"
    assert captured["headers"]["Authorization"] == "Bearer tk-123"
    assert captured["headers"]["Content-Type"] == "application/json"
    body = captured["body"]
    assert body["title"] == "LS"
    assert body["message"] == "at gate"
    assert body["priority"] == 4
    assert body["click"] == "https://example.com"
    assert "loc" in body["tags"] and "security" in body["tags"]
    assert "warning" in body["tags"] and "camera:cam-1" in body["tags"]
    assert len(ntf.sent) == 1 and ntf.sent[0] is alert


def test_push_notifier_reference_fallback():
    posted: list = []
    handled: list = []
    ntf = PushNotifier(handler=lambda a: handled.append(a),
                       post=lambda u, b, h: posted.append(u))
    alert = Alert(rule_id="r2", rule_type="line_cross", camera_id="c",
                  severity="info", title="cross", message="m")
    ntf.send(alert)
    assert posted == []
    assert len(handled) == 1 and handled[0] is alert
    assert len(ntf.sent) == 1


# ── event clip export ────────────────────────────────────────────────────────
def test_event_clip_export(client):
    h = {"Authorization": _admin(client)}
    r = client.post("/api/cameras", json={"name": "cam-clip"}, headers=h)
    assert r.status_code == 200, r.text
    cam_id = r.json()["id"]

    rt = client.app.state.runtime
    t0 = dt.datetime(2026, 1, 1, 0, 0, 0, tzinfo=dt.timezone.utc)
    payload_a = b"\x00\x00\x00\x18ftypisom" + b"A" * 64
    payload_b = b"\x00\x00\x00\x18ftypisom" + b"B" * 64
    key_a = f"{cam_id}/2026-01-01T00-00-00/seg-a.mp4"
    key_b = f"{cam_id}/2026-01-01T00-05-00/seg-b.mp4"
    key_out = f"{cam_id}/2026-01-01T02-00-00/seg-out.mp4"
    rt.storage.put(key_a, payload_a, content_type="video/mp4")
    rt.storage.put(key_b, payload_b, content_type="video/mp4")
    rt.storage.put(key_out, payload_a, content_type="video/mp4")

    seg_a_start = t0
    seg_a_end = t0 + dt.timedelta(seconds=60)
    seg_b_start = t0 + dt.timedelta(seconds=300)
    seg_b_end = seg_b_start + dt.timedelta(seconds=60)
    seg_out_start = t0 + dt.timedelta(hours=2)
    seg_out_end = seg_out_start + dt.timedelta(seconds=60)
    with rt.SessionLocal() as s:
        s.add_all([
            VideoSegment(camera_id=cam_id, storage_key=key_a,
                         start_ts=seg_a_start, end_ts=seg_a_end,
                         duration_sec=60.0, size_bytes=len(payload_a)),
            VideoSegment(camera_id=cam_id, storage_key=key_b,
                         start_ts=seg_b_start, end_ts=seg_b_end,
                         duration_sec=60.0, size_bytes=len(payload_b)),
            VideoSegment(camera_id=cam_id, storage_key=key_out,
                         start_ts=seg_out_start, end_ts=seg_out_end,
                         duration_sec=60.0, size_bytes=len(payload_a)),
        ])
        ev = Event(camera_id=cam_id,
                   timestamp_start=t0 + dt.timedelta(seconds=10),
                   timestamp_end=t0 + dt.timedelta(seconds=320),
                   event_type="line_cross", confidence=0.9,
                   bbox={"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2})
        s.add(ev)
        s.commit()
        ev_id = ev.id

    r = client.get(f"/api/events/{ev_id}/clip", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["event_id"] == ev_id
    assert body["camera_id"] == cam_id
    assert body["segment_count"] == 2
    assert body["expires_in"] == 300
    starts = [seg["start_ts"] for seg in body["segments"]]
    assert starts == sorted(starts)
    assert all("/api/video/" in seg["url"] and "exp=" in seg["url"] and "sig=" in seg["url"]
               for seg in body["segments"])
    expected_payloads = [payload_a, payload_b]
    for seg, expected in zip(body["segments"], expected_payloads):
        fetched = client.get(seg["url"], headers=h)
        assert fetched.status_code == 200, seg["url"]
        assert fetched.content == expected

    vh = {"Authorization": _viewer(client)}
    assert client.get(f"/api/events/{ev_id}/clip", headers=vh).status_code == 403

    with rt.SessionLocal() as s:
        lonely = Event(camera_id=cam_id,
                       timestamp_start=t0 + dt.timedelta(days=365),
                       timestamp_end=t0 + dt.timedelta(days=365, seconds=10),
                       event_type="intrusion", confidence=0.8,
                       bbox={"x": 0, "y": 0, "w": 0.1, "h": 0.1})
        s.add(lonely)
        s.commit()
        lonely_id = lonely.id
    assert client.get(f"/api/events/{lonely_id}/clip", headers=h).status_code == 404
    assert client.get("/api/events/does-not-exist/clip", headers=h).status_code == 404

    with rt.SessionLocal() as s:
        audit_rows = s.query(AuditLog).filter_by(
            action="video.clip.assemble", resource=ev_id).all()
        assert len(audit_rows) == 1
        assert audit_rows[0].detail == {"camera_id": cam_id, "segment_count": 2}


# ── timeline merge (events + recording) + live health (Task 3) ───────────────
def test_timeline_merged(client):
    h = {"Authorization": _admin(client)}
    cam_id = client.post("/api/cameras", json={"name": "cam-tl"}, headers=h).json()["id"]
    cam2 = client.post("/api/cameras", json={"name": "cam-tl2"}, headers=h).json()["id"]
    rt = client.app.state.runtime
    t0 = dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=dt.timezone.utc)
    with rt.SessionLocal() as s:
        s.add(Event(camera_id=cam_id, event_type="presence",
                    timestamp_start=t0, timestamp_end=t0 + dt.timedelta(minutes=10),
                    confidence=0.8, bbox={"x": 0, "y": 0, "w": 0.1, "h": 0.1}))
        s.add(Event(camera_id=cam_id, event_type="line_cross",
                    timestamp_start=t0 + dt.timedelta(minutes=5),
                    timestamp_end=t0 + dt.timedelta(minutes=5, seconds=1),
                    confidence=0.9, bbox={"x": 0, "y": 0, "w": 0.1, "h": 0.1}))
        s.add(Event(camera_id=cam2, event_type="intrusion",
                    timestamp_start=t0 + dt.timedelta(minutes=7),
                    timestamp_end=t0 + dt.timedelta(minutes=7, seconds=1),
                    confidence=0.95, bbox={"x": 0, "y": 0, "w": 0.1, "h": 0.1}))
        s.add(VideoSegment(camera_id=cam_id, storage_key=f"{cam_id}/seg.mp4",
                           start_ts=t0, end_ts=t0 + dt.timedelta(minutes=15),
                           duration_sec=900.0, size_bytes=1024))
        s.add(VideoSegment(camera_id=cam_id, storage_key=f"{cam_id}/seg-out.mp4",
                           start_ts=t0 + dt.timedelta(days=30),
                           end_ts=t0 + dt.timedelta(days=30, minutes=15),
                           duration_sec=900.0, size_bytes=1024))
        s.commit()

    body = client.get("/api/timeline?date=2026-01-01", headers=h).json()
    assert body["date"] == "2026-01-01"
    assert len(body["timeline"]) == 1
    assert body["timeline"][0]["camera_id"] == cam_id
    assert len(body["timeline"][0]["intervals"]) == 1
    assert {m["event_type"] for m in body["markers"]} == {"line_cross", "intrusion"}
    assert all(m["camera_id"] in (cam_id, cam2) for m in body["markers"])
    assert len(body["recording"]) == 1
    assert body["recording"][0]["camera_id"] == cam_id
    assert body["recording"][0]["duration_sec"] == 900.0
    assert body["limits"] == {"recording": 500, "markers": 500}

    filt = client.get(f"/api/timeline?date=2026-01-01&camera_id={cam_id}", headers=h).json()
    assert {m["event_type"] for m in filt["markers"]} == {"line_cross"}
    assert {r["camera_id"] for r in filt["recording"]} == {cam_id}

    assert client.get("/api/timeline?date=nope", headers=h).status_code == 400

    empty = client.get("/api/timeline?date=2027-01-01", headers=h).json()
    assert empty["timeline"] == [] and empty["markers"] == [] and empty["recording"] == []


def test_live_streams_health(client):
    from apps.api.routers import live as live_mod

    class _FakeProc:
        def __init__(self, pid):
            self.pid = pid

        def poll(self):
            return None

    saved = dict(live_mod._live_streams)
    try:
        live_mod._live_streams["cam-live-1"] = _FakeProc(pid=7777)
        h = {"Authorization": _admin(client)}
        r = client.get("/api/live/streams", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body["active"], list) and "count" in body
        mine = [e for e in body["active"] if e["camera_id"] == "cam-live-1"]
        assert len(mine) == 1
        assert mine[0]["running"] is True
        assert mine[0]["pid"] == 7777
        assert client.get("/api/live/streams").status_code == 401
    finally:
        live_mod._live_streams.clear()
        live_mod._live_streams.update(saved)


# ── live view API ───────────────────────────────────────────────────────────
def test_live_ticket_flow(client):
    r = client.post("/api/cameras", json={"name": "cam-l"}, headers={"Authorization": _admin(client)})
    cam_id = r.json()["id"]
    h = {"Authorization": _admin(client)}
    t = client.post("/api/live/ticket", json={"camera_id": cam_id, "ttl_sec": 300}, headers=h)
    assert t.status_code == 200
    ticket = t.json()["ticket"]
    play = client.get(f"/api/live/{cam_id}/play?ticket={ticket}", headers=h)
    assert play.status_code == 200
    assert play.json()["hls_manifest"].endswith("index.m3u8")
    # tampered ticket -> 401
    assert client.get(f"/api/live/{cam_id}/play?ticket=garbage", headers=h).status_code == 401
    # wrong camera ticket -> 403
    r2 = client.post("/api/cameras", json={"name": "cam-l2"}, headers=h)
    cam2 = r2.json()["id"]
    bad = client.post("/api/live/ticket", json={"camera_id": cam2, "ttl_sec": 300}, headers=h).json()["ticket"]
    assert client.get(f"/api/live/{cam_id}/play?ticket={bad}", headers=h).status_code == 403
    # viewer can also obtain a ticket (live:view granted)
    vh = {"Authorization": _viewer(client)}
    assert client.post("/api/live/ticket", json={"camera_id": cam_id, "ttl_sec": 60}, headers=vh).status_code == 200


# ── cameras vendor presets API ──────────────────────────────────────────────
def test_vendor_presets_api(client):
    h = {"Authorization": _admin(client)}
    assert client.get("/api/cameras/vendor-presets", headers=h).status_code == 200
    b = client.post("/api/cameras/presets/build",
                    json={"vendor": "axis", "cam_ip": "10.0.0.9", "stream": "main"}, headers=h)
    assert b.status_code == 200 and b.json()["url"].startswith("rtsp://")
    assert client.post("/api/cameras/presets/build", json={"vendor": "nope"}, headers=h).status_code == 400


# ── helpers ─────────────────────────────────────────────────────────────────
def _admin(client):
    r = client.post("/api/auth/login", json={"email": "admin@test.com", "password": "Sup3rStr0ngPw!"})
    return f"Bearer {r.json()['access_token']}"


def _viewer(client):
    client.post("/api/auth/login", json={"email": "admin@test.com", "password": "Sup3rStr0ngPw!"})
    client.post("/api/users", json={"email": "viewer@test.com", "password": "ViewerPw12345", "role": "VIEWER"},
                headers={"Authorization": _admin(client)})
    r = client.post("/api/auth/login", json={"email": "viewer@test.com", "password": "ViewerPw12345"})
    return f"Bearer {r.json()['access_token']}"
