"""Tests for the new surveillance-analytics modules and API routers.

Covers behavior rule engine, detection backends, ANPR, VLM search, recorder,
ONVIF client, vendor presets, analytics aggregation, and the alerts/live/analytics/
rules API surfaces. Heavy runtimes (onnxruntime/numpy) are intentionally not
required: pure-logic paths and lazy-import guards are exercised instead.
"""
from __future__ import annotations

import datetime as dt

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
from packages.domain.models import Camera, Event, Track
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
    # registry is empty -> build_detector raises (no staged model)
    with pytest.raises((RuntimeError, KeyError)):
        detectors.build_detector(S(), ModelRegistry())


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
    # test alert delivers to 0 webhooks (env not set) -> no crash
    assert client.post("/api/alerts/test", headers=h).json()["delivered"] == 0
    # analytic events list works
    assert client.get("/api/alerts/events", headers=h).status_code == 200
    assert client.delete(f"/api/alerts/routes/{rid}", headers=h).status_code == 200


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
