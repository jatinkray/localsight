"""Retention sweep regression (Wave-2 demo hardening).

The worker's `_sweep_retention` chained `.limit(...).with_for_update()
.delete(...)`, which SQLAlchemy 2.0 rejects at query time — the worker logged
"retention sweep failed" every cycle and deleted NOTHING (silent retention
violation; AGENTS.md rule 7). These tests would have caught it.
"""
from __future__ import annotations

import datetime as dt

import pytest

from packages.domain.models import (
    AuditLog,
    Camera,
    Event,
    Person,
    PersonEmbedding,
    RefreshToken,
    Role,
    User,
)


@pytest.fixture()
def rt(app):
    return app.state.runtime


def _old(now, days):
    return now - dt.timedelta(days=days + 1)  # comfortably past the cutoff


def test_sweep_deletes_expired_events(rt, app):
    now = dt.datetime.now(dt.UTC)
    with rt.SessionLocal() as s:
        cam = Camera(name="sweep-test-cam")
        s.add(cam)
        s.commit()
        cam_id = cam.id
    with rt.SessionLocal() as s:
        s.add(Event(camera_id=cam_id, track_id="t", identity_status="unknown",
                    event_type="presence",
                    timestamp_start=_old(now, rt.settings.retention_events_days),
                    timestamp_end=_old(now, rt.settings.retention_events_days),
                    confidence=0.9, bbox={}))
        s.commit()

    from apps.worker.main import _sweep_retention
    _sweep_retention(rt)  # must not raise (the old code raised at query time)

    with rt.SessionLocal() as s:
        left = s.query(Event).filter(Event.camera_id == cam_id).count()
    assert left == 0, "expired events must be swept"


def test_sweep_deletes_expired_refresh_tokens_and_audit(rt, app):
    now = dt.datetime.now(dt.UTC)
    with rt.SessionLocal() as s:
        role = s.query(Role).filter_by(name="VIEWER").first()
        user = User(email=f"sweep-rt-{int(now.timestamp())}@example.com",
                    password_hash="x", role_id=role.id)
        s.add(user)
        s.flush()
        user_id = user.id
        s.add(RefreshToken(user_id=user_id, jti="sweep-jti-test",
                           expires_at=_old(now, 0)))
        s.add(AuditLog(user_id=user_id, action="test.sweep",
                       ts=_old(now, rt.settings.retention_audit_days)))
        s.commit()

    from apps.worker.main import _sweep_retention
    _sweep_retention(rt)

    with rt.SessionLocal() as s:
        assert s.query(RefreshToken).filter(RefreshToken.user_id == user_id).count() == 0
        assert s.query(AuditLog).filter(AuditLog.user_id == user_id).count() == 0


def test_sweep_deletes_expired_embeddings(rt, app):
    now = dt.datetime.now(dt.UTC)
    person_id = None
    with rt.SessionLocal() as s:
        role = s.query(Role).filter_by(name="VIEWER").first()
        u = User(email=f"sweep-emb-{int(now.timestamp())}@example.com",
                 password_hash="x", role_id=role.id)
        s.add(u)
        s.flush()
        u_id = u.id
        p = Person(label="sweep-emb-person", display_name="Sweep", created_by=u_id)
        s.add(p)
        s.flush()
        person_id = p.id
        s.add(PersonEmbedding(person_id=p.id, embedding_enc="enc",
                              model_version="ref-v0", dimension=4,
                              created_at=_old(now, rt.settings.retention_embeddings_days)))
        s.commit()

    from apps.worker.main import _sweep_retention
    _sweep_retention(rt)

    with rt.SessionLocal() as s:
        assert s.query(PersonEmbedding).filter(
            PersonEmbedding.person_id == person_id).count() == 0
