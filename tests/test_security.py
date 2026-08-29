"""Security tests: auth, lockout, RBAC, SSRF, encryption, path traversal."""
from __future__ import annotations

import base64
import secrets

import pytest

from packages.security.crypto import CryptoBox
from packages.security.ssrf import UnsafeUrlError, validate_egress_url
from packages.storage.local import LocalFilesystemStorage


# ── Auth + lockout ────────────────────────────────────────────────────────────
def test_login_success(client, admin_auth):
    assert admin_auth["Authorization"].startswith("Bearer ")


def test_wrong_password_lockout(client):
    email = "admin@test.com"
    for _ in range(5):
        r = client.post("/api/auth/login", json={"email": email, "password": "wrongpassword1"})
        assert r.status_code in (401, 423)
    # now even the correct password is rejected while locked
    r = client.post("/api/auth/login", json={"email": email, "password": "Sup3rStr0ngPw!"})
    assert r.status_code == 423


def test_refresh_rotation(client, admin_auth):
    # first get a refresh token via login response
    login = client.post("/api/auth/login", json={"email": "admin@test.com", "password": "Sup3rStr0ngPw!"})
    refresh = login.json()["refresh_token"]
    r1 = client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert r1.status_code == 200
    new_refresh = r1.json()["refresh_token"]
    # replaying the old refresh must fail (rotated/revoked)
    r2 = client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert r2.status_code == 401
    # new refresh still works
    r3 = client.post("/api/auth/refresh", json={"refresh_token": new_refresh})
    assert r3.status_code == 200


def test_me_requires_auth(client):
    assert client.get("/api/auth/me").status_code == 401


# ── RBAC ──────────────────────────────────────────────────────────────────────
def test_viewer_cannot_create_camera(client, viewer_auth):
    r = client.post("/api/cameras", json={"name": "x"}, headers=viewer_auth)
    assert r.status_code == 403


def test_operator_can_create_camera(client, admin_auth):
    r = client.post("/api/cameras", json={"name": "cam-1"}, headers=admin_auth)
    assert r.status_code == 200
    assert "id" in r.json()


# ── SSRF ──────────────────────────────────────────────────────────────────────
def test_ssrf_blocks_private_and_metadata(client, admin_auth):
    for bad in ["rtsp://127.0.0.1/stream", "rtsp://169.254.169.254/latest", "rtsp://10.0.0.5/x", "rtsp://192.168.1.5/x"]:
        r = client.post("/api/cameras", json={"name": "bad", "stream_url": bad}, headers=admin_auth)
        assert r.status_code == 400, f"expected block for {bad}, got {r.status_code}: {r.text}"


def test_ssrf_allows_public(client, admin_auth):
    r = client.post("/api/cameras", json={"name": "pub", "stream_url": "rtsp://1.1.1.1/stream"}, headers=admin_auth)
    assert r.status_code == 200


def test_validate_egress_unit():
    with pytest.raises(UnsafeUrlError):
        validate_egress_url("http://169.254.169.254/")
    # allowlist bypass
    res = validate_egress_url("http://10.0.0.5/x", allowlist=["10.0.0.0/8"])
    assert res.hostname == "10.0.0.5"


# ── Encryption at rest ─────────────────────────────────────────────────────────
def test_crypto_roundtrip():
    box = CryptoBox(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
    ct = box.encrypt_str("secret-value")
    assert ct != "secret-value"
    assert box.decrypt_str(ct) == "secret-value"
    obj = box.encrypt_json({"a": [1, 2, 3]})
    assert box.decrypt_json(obj) == {"a": [1, 2, 3]}


def test_crypto_rejects_empty_key():
    with pytest.raises(Exception):
        CryptoBox("")


# ── Path traversal in storage ──────────────────────────────────────────────────
def test_storage_path_traversal_rejected(tmp_path):
    store = LocalFilesystemStorage(str(tmp_path), "signing-secret-1234567890")
    with pytest.raises(ValueError):
        store.put("../escape.txt", b"x")
    with pytest.raises(ValueError):
        store.put("/abs/path.txt", b"x")


def test_signed_url_tamper_rejected(tmp_path):
    store = LocalFilesystemStorage(str(tmp_path), "signing-secret-1234567890")
    store.put("seg/1.mp4", b"data")
    url = store.sign_get_url("seg/1.mp4", expires_sec=300)
    from urllib.parse import urlparse, parse_qs
    p = urlparse(url)
    q = parse_qs(p.query)
    sig = q["sig"][0]
    exp = q["exp"][0]
    assert store.verify_signed_url("seg/1.mp4", exp, sig) is True
    assert store.verify_signed_url("seg/1.mp4", exp, "deadbeef") is False
