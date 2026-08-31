"""Runtime bootstrap: engine, sessions, crypto, storage, model registry, and
first-run seeding of roles/permissions and the bootstrap admin.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from packages.ai.face import ReferenceEmbedder
from packages.ai.registry import ModelRegistry
from packages.domain.models import (
    Base,
    Permission,
    RefreshToken,
    Role,
    User,
)
from packages.security.crypto import CryptoBox
from packages.security.passwords import hash_password
from packages.security.rbac import ROLE_PERMISSIONS
from packages.storage.base import StorageProvider
from packages.storage.local import LocalFilesystemStorage
from packages.storage.s3 import S3CompatibleStorage

from apps.api.config import Settings


class Runtime:
    def __init__(
        self,
        settings: Settings,
        engine,
        SessionLocal,
        crypto: CryptoBox,
        storage: StorageProvider,
        registry: ModelRegistry,
        embedder,
    ) -> None:
        self.settings = settings
        self.engine = engine
        self.SessionLocal = SessionLocal
        self.crypto = crypto
        self.storage = storage
        self.registry = registry
        self.embedder = embedder


def build(settings: Settings) -> Runtime:
    settings.assert_secure()

    connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
    engine = create_engine(settings.database_url, future=True, connect_args=connect_args)
    SessionLocal = sessionmaker(bind=engine, future=True)

    crypto = CryptoBox(settings.master_encryption_key)

    if settings.storage_backend == "s3":
        storage: StorageProvider = S3CompatibleStorage(
            bucket=settings.storage_s3_bucket,
            endpoint_url=settings.storage_s3_endpoint,
            region=settings.storage_s3_region,
            prefix=settings.storage_s3_prefix,
            signing_secret=settings.master_encryption_key,
        )
    else:
        storage = LocalFilesystemStorage(settings.storage_local_root, settings.master_encryption_key)

    registry = ModelRegistry("models/registry.json")
    embedder = ReferenceEmbedder()

    rt = Runtime(settings, engine, SessionLocal, crypto, storage, registry, embedder)
    init_db(rt)
    seed(rt)
    return rt


def init_db(rt: Runtime) -> None:
    # v1 schema management via create_all. Production should migrate with Alembic
    # (see docs/operations/runbook.md). Idempotent and safe on existing schemas
    # only when no columns were added — use Alembic for evolving schemas.
    Base.metadata.create_all(rt.engine)
    _ensure_columns(rt)


def _ensure_columns(rt: Runtime) -> None:
    """Defensively add columns introduced on existing tables.

    `create_all` only creates missing *tables*, never ALTERs existing ones, so a
    column added to a pre-existing table (e.g. `cameras.rules`) would otherwise be
    missing on an upgraded database and crash the camera worker. This guards against
    that without requiring Alembic for a single additive column.
    """
    from sqlalchemy import text

    added = [
        "ALTER TABLE cameras ADD COLUMN rules JSON",
        "ALTER TABLE alert_routes ADD COLUMN cooldown_sec INTEGER NOT NULL DEFAULT 0",
    ]
    with rt.engine.begin() as conn:
        for stmt in added:
            try:
                conn.execute(text(stmt))
            except Exception:  # noqa: BLE001 - already present (or unsupported type)
                pass


def seed(rt: Runtime) -> None:
    with rt.SessionLocal() as session:
        # Permissions + roles (idempotent)
        perm_cache = {}
        for role_name, perms in ROLE_PERMISSIONS.items():
            for p in perms:
                existing = session.query(Permission).filter_by(name=p).first()
                if not existing:
                    existing = Permission(name=p)
                    session.add(existing)
                    session.flush()
                perm_cache[p] = existing
            role = session.query(Role).filter_by(name=role_name).first()
            if not role:
                role = Role(name=role_name, description=f"Built-in {role_name} role")
                session.add(role)
                session.flush()
            role.permissions = [perm_cache[p] for p in perms]

        # Only bootstrap admin if no users exist yet.
        if session.query(User).count() == 0 and settings_bootstrap_email(rt):
            role_admin = session.query(Role).filter_by(name="ADMIN").first()
            admin = User(
                email=rt.settings.bootstrap_admin_email,
                full_name="Bootstrap Administrator",
                password_hash=hash_password(rt.settings.bootstrap_admin_password or "CHANGE_ME_STRONG_PASSWORD"),
                role_id=role_admin.id,
                is_active=True,
            )
            session.add(admin)
        session.commit()


def settings_bootstrap_email(rt: Runtime) -> bool:
    return bool(rt.settings.bootstrap_admin_email)
