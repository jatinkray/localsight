"""Pytest fixtures. Configures a secure test environment (real random secrets,
an isolated SQLite DB) and exposes the FastAPI app via TestClient.

Run with:  pytest -q   (from the repo root; PYTHONPATH=. is set here)
"""
import os
import sys

# Must be set BEFORE importing the app (Settings reads env at import time).
_REPO = os.path.dirname(os.path.abspath(__file__))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import base64
import secrets

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_localvision.db")
os.environ.setdefault("JWT_SECRET", base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
os.environ.setdefault("MASTER_ENCRYPTION_KEY", base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
os.environ.setdefault("BOOTSTRAP_ADMIN_EMAIL", "admin@test.com")
os.environ.setdefault("BOOTSTRAP_ADMIN_PASSWORD", "Sup3rStr0ngPw!")
os.environ.setdefault("STORAGE_LOCAL_ROOT", "./data/test_storage")
os.environ.setdefault("AI_IDENTITY_RECOGNITION_ENABLED", "false")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from apps.api.bootstrap import Runtime, init_db, seed
from apps.api.main import create_app
from packages.domain.models import Base


@pytest.fixture(scope="session")
def app():
    application = create_app()
    yield application


@pytest.fixture()
def client(app):
    # Reset schema (and re-seed roles/permissions/admin) per test for isolation.
    rt: Runtime = app.state.runtime
    Base.metadata.drop_all(rt.engine)
    init_db(rt)
    seed(rt)
    # Rate limiter is process-global; reset so tests don't accumulate attempts.
    from apps.api.dependencies import limiter
    limiter.clear()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def admin_auth(client):
    resp = client.post(
        "/api/auth/login",
        json={"email": "admin@test.com", "password": "Sup3rStr0ngPw!"},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def viewer_auth(client):
    # Create a viewer user via admin, then log in.
    resp = client.post(
        "/api/auth/login",
        json={"email": "admin@test.com", "password": "Sup3rStr0ngPw!"},
    )
    admin_token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {admin_token}"}
    client.post(
        "/api/users",
        json={"email": "viewer@test.com", "password": "ViewerPw12345", "role": "VIEWER"},
        headers=headers,
    )
    r = client.post("/api/auth/login", json={"email": "viewer@test.com", "password": "ViewerPw12345"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
