"""Camera & NVR management.

Security notes:
  * Operator-supplied stream URLs are SSRF-validated before being stored or
    used; private/loopback/metadata destinations are rejected unless explicitly
    allow-listed at deploy time.
  * Stream URLs and NVR credentials are encrypted at rest and NEVER returned to
    API clients (CameraOut omits them by design).
  * All create/update/delete actions are audited.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from apps.api.audit import write_audit
from apps.api.bootstrap import Runtime
from apps.api.dependencies import get_current_user, get_db, get_runtime, require_permission
from packages.domain.models import Camera, NvrDevice, User
from packages.security.crypto import CryptoBox
from packages.security.errors import UnsafeUrlError
from packages.security.ssrf import validate_egress_url

router = APIRouter(prefix="/api", tags=["cameras"])


class NvrBody(BaseModel):
    name: str
    host: str
    port: int = 80
    onvif_supported: bool = False
    username: str | None = None
    password: str | None = None


@router.post("/nvr", dependencies=[Depends(require_permission("camera:configure"))])
def create_nvr(body: NvrBody, request: Request, db: Session = Depends(get_db), rt: Runtime = Depends(get_runtime)):
    crypto: CryptoBox = rt.crypto
    nvr = NvrDevice(
        name=body.name,
        host=body.host,
        port=body.port,
        onvif_supported=body.onvif_supported,
        username_enc=crypto.encrypt_str(body.username) if body.username else None,
        password_enc=crypto.encrypt_str(body.password) if body.password else None,
    )
    db.add(nvr)
    db.flush()
    write_audit(db, user=request.state.user, action="nvr.create", resource=nvr.id,
                request_id=getattr(request.state, "request_id", "-"))
    db.commit()
    return {"id": nvr.id, "name": nvr.name, "host": nvr.host, "port": nvr.port,
            "onvif_supported": nvr.onvif_supported}


@router.get("/nvr", dependencies=[Depends(require_permission("camera:configure"))])
def list_nvr(db: Session = Depends(get_db)):
    rows = db.query(NvrDevice).all()
    return [{"id": r.id, "name": r.name, "host": r.host, "port": r.port,
             "onvif_supported": r.onvif_supported} for r in rows]


@router.post("/cameras", dependencies=[Depends(require_permission("camera:configure"))])
def create_camera(body: dict, request: Request, db: Session = Depends(get_db), rt: Runtime = Depends(get_runtime)):
    name = body.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    main_url = body.get("stream_url")
    sub_url = body.get("substream_url")
    # SSRF guard on egress destinations.
    for label, url in (("stream_url", main_url), ("substream_url", sub_url)):
        if url:
            try:
                validate_egress_url(url, allowlist=rt.settings.ssrf_allowlist_cidrs)
            except UnsafeUrlError as exc:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                    detail=f"{label}: {exc}") from exc

    cam = Camera(
        name=name,
        camera_uid=body.get("camera_uid"),
        nvr_device_id=body.get("nvr_device_id"),
        stream_url_enc=rt.crypto.encrypt_str(main_url) if main_url else None,
        substream_url_enc=rt.crypto.encrypt_str(sub_url) if sub_url else None,
        resolution=body.get("resolution", ""),
        fps=int(body.get("fps", 0) or 0),
        timezone=body.get("timezone", "UTC"),
        privacy_masks=body.get("privacy_masks"),
        retention=body.get("retention"),
        status="OFFLINE",
    )
    db.add(cam)
    db.flush()
    write_audit(db, user=request.state.user, action="camera.create", resource=cam.id,
                request_id=getattr(request.state, "request_id", "-"),
                detail={"name": name})
    db.commit()
    return {"id": cam.id, "name": cam.name, "status": cam.status}


@router.get("/cameras", dependencies=[Depends(require_permission("camera:view"))])
def list_cameras(db: Session = Depends(get_db)):
    rows = db.query(Camera).all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "camera_uid": r.camera_uid,
            "status": r.status,
            "health": r.health,
            "last_seen": r.last_seen.isoformat() if r.last_seen else None,
            "resolution": r.resolution,
            "fps": r.fps,
            "timezone": r.timezone,
            "privacy_masks": r.privacy_masks,
            "retention": r.retention,
        }
        for r in rows
    ]


@router.get("/cameras/{camera_id}", dependencies=[Depends(require_permission("camera:view"))])
def get_camera(camera_id: str, db: Session = Depends(get_db)):
    cam = db.get(Camera, camera_id)
    if not cam:
        raise HTTPException(status_code=404, detail="camera not found")
    return {
        "id": cam.id, "name": cam.name, "camera_uid": cam.camera_uid,
        "status": cam.status, "health": cam.health,
        "last_seen": cam.last_seen.isoformat() if cam.last_seen else None,
        "resolution": cam.resolution, "fps": cam.fps, "timezone": cam.timezone,
        "privacy_masks": cam.privacy_masks, "retention": cam.retention,
    }


@router.put("/cameras/{camera_id}", dependencies=[Depends(require_permission("camera:configure"))])
def update_camera(camera_id: str, body: dict, request: Request, db: Session = Depends(get_db), rt: Runtime = Depends(get_runtime)):
    cam = db.get(Camera, camera_id)
    if not cam:
        raise HTTPException(status_code=404, detail="camera not found")
    for f in ("name", "resolution", "fps", "timezone", "privacy_masks", "retention"):
        if f in body:
            setattr(cam, f, body[f])
    if "stream_url" in body and body["stream_url"]:
        validate_egress_url(body["stream_url"], allowlist=rt.settings.ssrf_allowlist_cidrs)
        cam.stream_url_enc = rt.crypto.encrypt_str(body["stream_url"])
    if "substream_url" in body and body["substream_url"]:
        validate_egress_url(body["substream_url"], allowlist=rt.settings.ssrf_allowlist_cidrs)
        cam.substream_url_enc = rt.crypto.encrypt_str(body["substream_url"])
    write_audit(db, user=request.state.user, action="camera.update", resource=camera_id,
                request_id=getattr(request.state, "request_id", "-"))
    db.commit()
    return {"id": cam.id, "name": cam.name, "status": cam.status}


@router.delete("/cameras/{camera_id}", dependencies=[Depends(require_permission("camera:configure"))])
def delete_camera(camera_id: str, request: Request, db: Session = Depends(get_db)):
    cam = db.get(Camera, camera_id)
    if not cam:
        raise HTTPException(status_code=404, detail="camera not found")
    db.delete(cam)
    write_audit(db, user=request.state.user, action="camera.delete", resource=camera_id,
                request_id=getattr(request.state, "request_id", "-"))
    db.commit()
    return {"ok": True}
