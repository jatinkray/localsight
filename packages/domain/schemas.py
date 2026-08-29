"""Pydantic API schemas (request/response validation)."""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, EmailStr, Field


# ── Auth ────────────────────────────────────────────────────────────────────
class UserLogin(BaseModel):
    email: EmailStr
    password: str
    mfa_code: str | None = None


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class MfaSetup(BaseModel):
    secret: str
    otpauth_uri: str


class MfaVerify(BaseModel):
    code: str


# ── Users ─────────────────────────────────────────────────────────────────
class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = ""
    password: str = Field(min_length=12)
    role: str = "VIEWER"


class UserOut(BaseModel):
    id: str
    email: EmailStr
    full_name: str
    role: str
    is_active: bool
    mfa_enabled: bool
    created_at: dt.datetime

    model_config = {"from_attributes": True}


# ── Cameras / NVR ───────────────────────────────────────────────────────────
class CameraCreate(BaseModel):
    name: str
    camera_uid: str | None = None
    nvr_device_id: str | None = None
    stream_url: str | None = None          # validated for SSRF on write
    substream_url: str | None = None
    resolution: str = ""
    fps: int = 0
    timezone: str = "UTC"
    privacy_masks: list[dict] | None = None
    retention: dict | None = None


class CameraUpdate(BaseModel):
    name: str | None = None
    stream_url: str | None = None
    substream_url: str | None = None
    resolution: str | None = None
    fps: int | None = None
    timezone: str | None = None
    privacy_masks: list[dict] | None = None
    retention: dict | None = None


class CameraOut(BaseModel):
    id: str
    name: str
    camera_uid: str | None
    nvr_device_id: str | None
    status: str
    health: str
    last_seen: dt.datetime | None
    resolution: str
    fps: int
    timezone: str
    privacy_masks: list[dict] | None
    retention: dict | None
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class NvrCreate(BaseModel):
    name: str
    host: str
    port: int = 80
    onvif_supported: bool = False
    username: str | None = None
    password: str | None = None


class NvrOut(BaseModel):
    id: str
    name: str
    host: str
    port: int
    onvif_supported: bool

    model_config = {"from_attributes": True}


class TPLinkNvrSeed(BaseModel):
    """Provision a TP-Link VIGI NVR and all of its channels in one call.

    Builds per-channel RTSP URLs (rtsp://<nvr>/live/ch/<N>/stream/<1|2>) and
    creates an NvrDevice plus one Camera per channel. Credentials are optional
    (VIGI uses digest auth); when given they are encrypted at rest.
    """
    nvr_ip: str
    nvr_name: str = "VIGI NVR"
    username: str | None = None
    password: str | None = None
    channel_count: int = Field(default=8, ge=1, le=64)
    start_channel: int = Field(default=1, ge=0, le=64)
    rtsp_port: int = 554
    onvif_port: int = 80
    retention_days: int = Field(default=7, ge=1, le=3650)


# ── Persons / identity ──────────────────────────────────────────────────────
class PersonCreate(BaseModel):
    label: str = Field(min_length=1)
    display_name: str = ""


class PersonOut(BaseModel):
    id: str
    label: str
    display_name: str
    status: str
    created_at: dt.datetime

    model_config = {"from_attributes": True}


# ── Events / search ─────────────────────────────────────────────────────────
class EventQuery(BaseModel):
    camera_id: str | None = None
    identity_id: str | None = None
    identity_status: str | None = None  # known | unknown | uncertain
    start: dt.datetime | None = None
    end: dt.datetime | None = None
    min_confidence: float | None = None
    limit: int = Field(default=50, le=500)
    offset: int = Field(default=0, ge=0)


class EventOut(BaseModel):
    id: str
    camera_id: str
    track_id: str | None
    identity_id: str | None
    identity_status: str
    event_type: str
    timestamp_start: dt.datetime
    timestamp_end: dt.datetime
    confidence: float
    bbox: dict[str, Any]
    has_snapshot: bool
    has_video: bool

    model_config = {"from_attributes": True}


# ── Pagination ──────────────────────────────────────────────────────────────
class Page(BaseModel):
    items: list[Any]
    total: int
    limit: int
    offset: int


# ── System ──────────────────────────────────────────────────────────────────
class SystemHealth(BaseModel):
    status: str
    components: dict[str, dict]
    generated_at: dt.datetime


def new_id() -> str:
    return uuid.uuid4().hex
