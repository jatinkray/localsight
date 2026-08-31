"""Live view API.

Issues short-lived, camera-scoped, signed tickets that authorize a media gateway
to stream a camera's substream (LL-HLS/WebRTC) without exposing the RTSP URL or
long-lived credentials to the client. Tickets are encrypted envelopes (carry their
own expiry) and are verified on the play endpoint. This keeps the secure-boundary
model: authn/authz is enforced server-side; the media gateway only honors valid tickets.
"""
from __future__ import annotations

import datetime as dt
import os
import subprocess
import threading

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from apps.api.bootstrap import Runtime
from apps.api.dependencies import get_current_user, get_db, get_runtime, require_permission
from packages.domain.models import Camera
from packages.observability.logging import logging as log

router = APIRouter(prefix="/api", tags=["live"])

# Live media is transcoded locally by ffmpeg into this directory, which is served
# read-only at /live-media by the app factory. Keyed by camera_id.
_LIVE_ROOT = os.environ.get("LOCALVISION_LIVE_DIR", "./data/live")
_live_lock = threading.Lock()
_live_streams: dict[str, "subprocess.Popen"] = {}


def _start_stream(rt, camera, url: str) -> str | None:
    """Launch (or reuse) an ffmpeg LL-HLS transcode of the camera substream.

    Returns the directory holding index.m3u8, or None if the transcode cannot start.
    The operator-supplied URL is egress-validated first.
    """
    try:
        from packages.security.ssrf import validate_egress_url

        validate_egress_url(url, allowlist=rt.settings.ssrf_allowlist_cidrs)
    except Exception as exc:  # noqa: BLE001 - unsafe destination
        raise HTTPException(status_code=400, detail=f"unsafe stream URL: {exc}")
    os.makedirs(_LIVE_ROOT, exist_ok=True)
    out_dir = os.path.join(_LIVE_ROOT, camera.id)
    with _live_lock:
        existing = _live_streams.get(camera.id)
        if existing is not None and existing.poll() is None:
            return out_dir  # already streaming
        if existing is not None:
            try:
                existing.terminate()
            except Exception:  # noqa: BLE001
                pass
    os.makedirs(out_dir, exist_ok=True)
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-rtsp_transport", "tcp", "-i", url,
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-f", "hls", "-hls_time", "2", "-hls_list_size", "10",
        "-hls_flags", "delete_segments",
        "-hls_segment_filename", os.path.join(out_dir, "%05d.ts"),
        os.path.join(out_dir, "index.m3u8"),
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, ValueError) as exc:
        # ffmpeg unavailable: degrade gracefully (manifest path still returned; the
        # gateway will produce media wherever ffmpeg is installed).
        log.warning("live transcode failed to start for %s: %s", camera.id, exc)
        return out_dir
    with _live_lock:
        _live_streams[camera.id] = proc
    return out_dir


def _stop_stream(camera_id: str) -> None:
    with _live_lock:
        proc = _live_streams.pop(camera_id, None)
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
                pass


@router.get("/live/streams", dependencies=[Depends(require_permission("live:view"))])
def live_streams():
    """Return the currently active live transcodes (presence/health).

    Each entry reports whether the ffmpeg process is still running and its PID,
    so the dashboard can render live indicators and detect dead streams. Dead
    (exited) processes are filtered out.
    """
    active = []
    for camera_id, proc in _live_streams.items():
        if proc.poll() is None:
            active.append({"camera_id": camera_id, "running": True, "pid": proc.pid})
    return {"active": active, "count": len(active)}


class TicketRequest(BaseModel):
    camera_id: str
    ttl_sec: int = 300


@router.post("/live/ticket", dependencies=[Depends(require_permission("live:view"))])
def issue_ticket(body: TicketRequest, request: Request, db: Session = Depends(get_db),
                 rt: Runtime = Depends(get_runtime)):
    cam = db.get(Camera, body.camera_id)
    if not cam:
        raise HTTPException(status_code=404, detail="camera not found")
    exp = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=max(30, min(body.ttl_sec, 3600)))
    token = rt.crypto.encrypt_json({"camera_id": body.camera_id, "exp": exp.isoformat()})
    from apps.api.audit import write_audit
    write_audit(db, user=request.state.user, action="live.ticket.issue", resource=body.camera_id,
                request_id=getattr(request.state, "request_id", "-"))
    return {"camera_id": body.camera_id, "ticket": token, "expires_at": exp.isoformat()}


@router.get("/live/{camera_id}/play", dependencies=[Depends(require_permission("live:view"))])
def play(camera_id: str, ticket: str, db: Session = Depends(get_db),
         rt: Runtime = Depends(get_runtime)):
    """Validate a live ticket and start/return the authorized LL-HLS manifest.

    The media gateway (ffmpeg) exchanges the ticket for the camera substream and
    transcodes it to LL-HLS under /live-media; this endpoint never returns the RTSP
    URL or credentials.
    """
    cam = db.get(Camera, camera_id)
    if not cam:
        raise HTTPException(status_code=404, detail="camera not found")
    try:
        claims = rt.crypto.decrypt_json(ticket)
    except Exception:
        raise HTTPException(status_code=401, detail="invalid ticket")
    if claims.get("camera_id") != camera_id:
        raise HTTPException(status_code=403, detail="ticket not for this camera")
    exp = dt.datetime.fromisoformat(claims["exp"])
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=dt.timezone.utc)
    if exp < dt.datetime.now(dt.timezone.utc):
        raise HTTPException(status_code=401, detail="ticket expired")
    manifest = f"/live-media/{camera_id}/index.m3u8"
    url_enc = cam.substream_url_enc
    if url_enc:
        try:
            out_dir = _start_stream(rt, cam, rt.crypto.decrypt_str(url_enc))
        except HTTPException:
            raise
        if out_dir is None:
            raise HTTPException(status_code=502, detail="failed to start live stream")
    return {
        "camera_id": camera_id,
        "hls_manifest": manifest,
        "protocols": ["ll-hls"],
        "ticket": ticket,
    }
