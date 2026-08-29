"""API behavior tests: events search/pagination, timeline, audit, MFA, and secure
media delivery via signed URLs.
"""
from __future__ import annotations

import datetime as dt
import urllib.parse

from packages.domain.models import Event, Person
from packages.security.mfa import current_code


def _admin(client):
    r = client.post("/api/auth/login", json={"email": "admin@test.com", "password": "Sup3rStr0ngPw!"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _make_event(client, cam_id, identity_status="unknown", minutes_ago=10, label=None):
    rt = client.app.state.runtime
    with rt.SessionLocal() as s:
        start = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes_ago)
        ev = Event(
            camera_id=cam_id, track_id="t1", identity_id=None, identity_status=identity_status,
            event_type="presence", timestamp_start=start,
            timestamp_end=start + dt.timedelta(minutes=2), confidence=0.9,
            bbox={"x": 0.1, "y": 0.1, "w": 0.1, "h": 0.1},
        )
        s.add(ev)
        s.commit()
        return ev.id


def test_events_pagination_and_filter(client, admin_auth):
    r = client.post("/api/cameras", json={"name": "cam-e"}, headers=admin_auth)
    cam_id = r.json()["id"]
    for _ in range(3):
        _make_event(client, cam_id)

    # total
    res = client.get("/api/events", headers=admin_auth)
    assert res.status_code == 200
    body = res.json()
    assert body["total"] >= 3
    # pagination
    paged = client.get("/api/events?limit=2&offset=0", headers=admin_auth).json()
    assert len(paged["items"]) == 2
    # filter by camera
    by_cam = client.get(f"/api/events?camera_id={cam_id}", headers=admin_auth).json()
    assert by_cam["total"] >= 3
    assert all(i["camera_id"] == cam_id for i in by_cam["items"])


def test_timeline_groups_events(client, admin_auth):
    r = client.post("/api/cameras", json={"name": "cam-t"}, headers=admin_auth)
    cam_id = r.json()["id"]
    _make_event(client, cam_id)
    day = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    res = client.get(f"/api/timeline?date={day}&camera_id={cam_id}", headers=admin_auth)
    assert res.status_code == 200
    assert len(res.json()["timeline"]) >= 1


def test_audit_logged_and_queryable(client, admin_auth):
    # admin_auth already triggered a login audit entry
    res = client.get("/api/audit?action=login", headers=admin_auth)
    assert res.status_code == 200
    assert res.json()["total"] >= 1


def test_mfa_enrollment_and_step_up(client):
    auth = _admin(client)
    setup = client.post("/api/auth/mfa/setup", headers=auth)
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    code = current_code(secret)
    verify = client.post("/api/auth/mfa/verify", json={"code": code}, headers=auth)
    assert verify.status_code == 200

    # login now requires MFA
    bad = client.post("/api/auth/login", json={"email": "admin@test.com", "password": "Sup3rStr0ngPw!"})
    assert bad.status_code == 401
    good = client.post("/api/auth/login", json={"email": "admin@test.com", "password": "Sup3rStr0ngPw!", "mfa_code": code})
    assert good.status_code == 200


def test_signed_video_url_access(client, admin_auth):
    rt = client.app.state.runtime
    r = client.post("/api/cameras", json={"name": "cam-v"}, headers=admin_auth)
    cam_id = r.json()["id"]
    key = f"segments/{cam_id}/clip.mp4"
    rt.storage.put(key, b"fake-video-bytes", "video/mp4")
    enc = rt.crypto.encrypt_str(key)
    with rt.SessionLocal() as s:
        ev = Event(camera_id=cam_id, track_id="t", identity_status="unknown", event_type="presence",
                   timestamp_start=dt.datetime.now(dt.timezone.utc),
                   timestamp_end=dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=1),
                   confidence=0.9, bbox={}, video_segment_key_enc=enc)
        s.add(ev)
        s.commit()
        ev_id = ev.id

    detail = client.get(f"/api/events/{ev_id}", headers=admin_auth).json()
    assert detail["video_url"]
    u = urllib.parse.urlparse(detail["video_url"])
    q = urllib.parse.parse_qs(u.query)
    # fetch with valid signature
    g = client.get(f"/api/video/{urllib.parse.quote(key, safe='')}?exp={q['exp'][0]}&sig={q['sig'][0]}", headers=admin_auth)
    assert g.status_code == 200
    assert g.content == b"fake-video-bytes"
    # tampered signature rejected
    bad = client.get(f"/api/video/{urllib.parse.quote(key, safe='')}?exp={q['exp'][0]}&sig=deadbeef", headers=admin_auth)
    assert bad.status_code == 403


def test_user_management_requires_privilege(client, viewer_auth):
    r = client.post("/api/users", json={"email": "x@y.z", "password": "NewUserPw1234", "role": "VIEWER"}, headers=viewer_auth)
    assert r.status_code == 403


def test_tplink_presets_listed(client, admin_auth):
    res = client.get("/api/cameras/presets", headers=admin_auth)
    assert res.status_code == 200
    vendors = {p["vendor"] for p in res.json()}
    assert {"vigi_camera", "vigi_nvr", "tapo"} <= vendors
    vigi_nvr = next(p for p in res.json() if p["vendor"] == "vigi_nvr")
    assert "live/ch/" in vigi_nvr["main_stream"]
    assert vigi_nvr["onvif_ports"] == [80, 2020]


def test_provision_vigi_nvr_creates_channels(client, admin_auth):
    res = client.post("/api/cameras/from-nvr", json={
        "nvr_ip": "192.168.99.50", "nvr_name": "VIGI NVR", "channel_count": 2,
    }, headers=admin_auth)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["nvr_host"] == "192.168.99.50"
    assert len(body["cameras"]) == 2
    for c in body["cameras"]:
        assert "/live/ch/" in c["main_stream"]
        assert c["sub_stream"].endswith("/stream/2")
    # cameras are listed and linked to the NVR
    cams = client.get("/api/cameras", headers=admin_auth).json()
    assert len([c for c in cams if c["nvr_device_id"] == body["nvr_id"]]) == 2


def test_from_nvr_blocks_metadata_ip(client, admin_auth):
    # SSRF egress guard must reject link-local/metadata destinations.
    res = client.post("/api/cameras/from-nvr", json={
        "nvr_ip": "169.254.169.254", "channel_count": 1,
    }, headers=admin_auth)
    assert res.status_code == 400
