"""Wave-3 Manage endpoints: camera snapshot + person references.

The privacy-mask editor needs one JPEG frame from the camera
(`GET /api/cameras/{id}/snapshot`); the Identities screen needs embedding
metadata per person (`GET /api/persons/{id}/references`) plus a
faces-enrolled count on the list (`faces_enrolled`).

Security/honesty properties under test:
  * snapshot never fabricates a frame — unknown camera → 404, camera
    without a stream URL → 409, unreachable camera / no ffmpeg → 503
  * references metadata NEVER exposes embedding ciphertext or the raw
    upload filename (provenance stays encrypted server-side)
  * faces_enrolled counts only that person's embedding rows
"""
from __future__ import annotations

import io


def _make_camera(client, admin_auth, name="Snapshot Cam", **fields):
    # 1.1.1.1 passes SSRF validation (public) but is not an RTSP host —
    # the snapshot endpoint must answer 503 honestly, never fake a frame.
    body = {"name": name, "stream_url": "rtsp://1.1.1.1/stream", **fields}
    res = client.post("/api/cameras", json=body, headers=admin_auth)
    assert res.status_code == 200, res.text
    return res.json()["id"]


# ── camera snapshot ─────────────────────────────────────────────────────

def test_snapshot_unknown_camera_404(client, admin_auth):
    res = client.get("/api/cameras/nope/snapshot", headers=admin_auth)
    assert res.status_code == 404


def test_snapshot_without_stream_url_409(client, admin_auth):
    res = client.post("/api/cameras", json={"name": "No Stream"}, headers=admin_auth)
    cam_id = res.json()["id"]
    res = client.get(f"/api/cameras/{cam_id}/snapshot", headers=admin_auth)
    assert res.status_code == 409


def test_snapshot_requires_camera_view(client, admin_auth, viewer_auth):
    # VIEWER has camera:view → allowed to fetch the mask-editor frame
    res = client.post("/api/cameras", json={"name": "V Cam",
                       "stream_url": "rtsp://1.1.1.1/stream"},
                      headers=admin_auth)
    res2 = client.get(f"/api/cameras/{res.json()['id']}/snapshot",
                      headers=viewer_auth)
    # Unreachable host: honest 503 (ffmpeg present or not, no frame is faked)
    assert res2.status_code == 503


def test_snapshot_never_leaks_url(client, admin_auth):
    cam_id = _make_camera(client, admin_auth)
    res = client.get(f"/api/cameras/{cam_id}/snapshot", headers=admin_auth)
    # 503 with the documented detail; the stream URL must never appear
    assert res.status_code == 503
    assert "rtsp://" not in res.text


def test_snapshot_signed_url_roundtrip(client, admin_auth):
    """The <img> path: mint a signed URL, fetch WITHOUT the bearer header."""
    cam_id = _make_camera(client, admin_auth, name="Signed Cam")
    mint = client.get(f"/api/cameras/{cam_id}/snapshot-url", headers=admin_auth)
    assert mint.status_code == 200
    url = mint.json()["url"]
    assert url.startswith(f"/api/cameras/{cam_id}/snapshot?exp=")
    # strip the base path — TestClient wants the path+query only
    res = client.get(url)  # no Authorization header
    # unreachable public IP -> honest 503, but AUTH PASSED (not 401)
    assert res.status_code == 503


def test_snapshot_signed_url_rejects_tampering(client, admin_auth):
    cam_id = _make_camera(client, admin_auth, name="Tamper Cam")
    mint = client.get(f"/api/cameras/{cam_id}/snapshot-url", headers=admin_auth).json()
    url = mint["url"]
    # tamper with the signature
    bad = url.replace("sig=", "sig=deadbeef") if "sig=deadbeef" not in url else url
    res = client.get(bad)
    assert res.status_code in (401, 403)


def test_snapshot_no_auth_fails_closed(client, admin_auth):
    cam_id = _make_camera(client, admin_auth, name="Closed Cam")
    # request with NO auth at all — fail closed
    res = client.get(f"/api/cameras/{cam_id}/snapshot")
    assert res.status_code == 401


# ── person references + faces_enrolled ───────────────────────────────────

def _make_person(client, admin_auth, label="w3-person"):
    res = client.post("/api/persons", json={"label": label, "display_name": "W3"},
                      headers=admin_auth)
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _png_bytes() -> bytes:
    # 1x1 PNG
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
        "1f15c4890000000d49444154789c626001000000ffff030000060005"
        "57bfabd40000000049454e44ae426082")


def test_references_metadata_shape(client, admin_auth):
    pid = _make_person(client, admin_auth)
    up = client.post(f"/api/persons/{pid}/references",
                     files={"file": ("alice.png", io.BytesIO(_png_bytes()), "image/png")},
                     headers=admin_auth)
    assert up.status_code == 200, up.text

    res = client.get(f"/api/persons/{pid}/references", headers=admin_auth)
    assert res.status_code == 200
    body = res.json()
    assert body["person_id"] == pid
    assert body["image_bytes_retained"] is False  # honesty field
    assert len(body["references"]) == 1
    ref = body["references"][0]
    assert set(ref) == {"id", "model_version", "dimension", "quality_score", "created_at"}
    # upload filename is stored ENCRYPTED — never echoed in the response
    assert "alice.png" not in res.text

    # list shows the count
    lst = client.get("/api/persons", headers=admin_auth).json()
    row = next(p for p in lst if p["id"] == pid)
    assert row["faces_enrolled"] == 1


def test_references_counts_are_per_person(client, admin_auth):
    p1 = _make_person(client, admin_auth, "w3-person-a")
    p2 = _make_person(client, admin_auth, "w3-person-b")
    client.post(f"/api/persons/{p1}/references",
                files={"file": ("a.png", io.BytesIO(_png_bytes()), "image/png")},
                headers=admin_auth)
    client.post(f"/api/persons/{p1}/references",
                files={"file": ("b.png", io.BytesIO(_png_bytes()), "image/png")},
                headers=admin_auth)
    lst = client.get("/api/persons", headers=admin_auth).json()
    assert next(p for p in lst if p["id"] == p1)["faces_enrolled"] == 2
    assert next(p for p in lst if p["id"] == p2)["faces_enrolled"] == 0


def test_references_unknown_person_404(client, admin_auth):
    res = client.get("/api/persons/nope/references", headers=admin_auth)
    assert res.status_code == 404
