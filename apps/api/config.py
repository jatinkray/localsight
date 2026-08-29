"""Application configuration via environment / .env.

Security-first: the app refuses to start if the JWT secret or the master
encryption key are missing or still set to the shipped placeholders. There are
no insecure defaults for secrets.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_env: str = "development"
    app_name: str = "LocalVision"
    log_level: str = "INFO"

    database_url: str = "sqlite:///./localvision.db"

    jwt_secret: str = ""
    master_encryption_key: str = ""

    cors_allow_origins: str = ""
    ssrf_allowlist: str = ""

    access_token_ttl_min: int = 15
    refresh_token_ttl_days: int = 7
    max_login_attempts: int = 5
    lockout_minutes: int = 15

    storage_backend: str = "local"
    storage_local_root: str = "./data/storage"
    storage_s3_bucket: str = ""
    storage_s3_endpoint: str | None = None
    storage_s3_region: str = "us-east-1"
    storage_s3_prefix: str = "localvision"

    ai_detector: str = "reference"
    ai_inference_fps: int = 5
    ai_confidence_threshold: float = 0.45
    ai_iou_threshold: float = 0.50
    ai_motion_gate_enabled: bool = True
    ai_recognize_interval_sec: float = 2.0
    ai_similarity_threshold: float = 0.85
    # Biometric identification is OFF by default (privacy by design). Enable only
    # after establishing a lawful basis and operator approval.
    ai_identity_recognition_enabled: bool = False

    retention_recordings_days: int = 7
    retention_events_days: int = 30
    retention_snapshots_days: int = 14
    retention_embeddings_days: int = 90
    retention_audit_days: int = 365

    bootstrap_admin_email: str = "admin@localvision.local"
    bootstrap_admin_password: str = ""

    # ── derived helpers ──────────────────────────────────────────────────
    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @property
    def ssrf_allowlist_cidrs(self) -> list[str]:
        return [c.strip() for c in self.ssrf_allowlist.split(",") if c.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    def assert_secure(self) -> None:
        placeholders = {
            "CHANGE_ME_GENERATE_A_32_BYTE_BASE64_KEY",
            "CHANGE_ME_GENERATE_ANOTHER_32_BYTE_BASE64_KEY",
        }
        if not self.jwt_secret or self.jwt_secret in placeholders:
            raise RuntimeError(
                "JWT_SECRET is missing or still a placeholder. Set a 32-byte base64 value."
            )
        if not self.master_encryption_key or self.master_encryption_key in placeholders:
            raise RuntimeError(
                "MASTER_ENCRYPTION_KEY is missing or still a placeholder. Set a 32-byte base64 value."
            )
        if len(self.jwt_secret) < 16 or len(self.master_encryption_key) < 16:
            raise RuntimeError("JWT_SECRET / MASTER_ENCRYPTION_KEY are too short.")
