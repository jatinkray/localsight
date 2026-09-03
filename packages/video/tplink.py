"""TP-Link-native stream presets and URL builders.

TP-Link ships two camera families with third-party streaming:

* **VIGI** (business/SMB surveillance) — native ONVIF Profile S/G/T and RTSP.
  Camera streams: ``rtsp://<ip>:554/stream1`` (main) and ``/stream2`` (sub).
  VIGI NVRs expose a *per-channel* RTSP interface:
  ``rtsp://<nvr>:554/live/ch/<N>/stream/<1|2>`` (channel 0 = channel-zero overview).
* **Tapo** (consumer) — RTSP/ONVIF on *wired* models only; a separate in-app
  "Camera Account" is required. URLs carry credentials:
  ``rtsp://<user>:<pass>@<ip>:554/stream1|stream2``.

All of these are plain RTSP, so LocalSight ingests them directly. These helpers
just encode the exact vendor URL/port/auth conventions so adding a TP-Link device
is a single, correct call instead of hand-building URLs.

Sources of truth: TP-Link VIGI FAQ 4201/3711/5198, VIGI NVR FAQ 4677, Tapo FAQ 2680.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote


@dataclass(frozen=True)
class TPLinkProfile:
    vendor: str
    label: str
    rtsp_port: int
    onvif_ports: tuple[int, ...]
    main_stream: str
    sub_stream: str
    wired_only: bool = False
    needs_camera_account: bool = False
    notes: str = ""


VIGI_CAMERA = TPLinkProfile(
    "vigi_camera", "TP-Link VIGI camera (direct RTSP)", 554, (80, 2020),
    "stream1", "stream2",
    notes="ONVIF Profile S/G/T; HTTP digest auth (MD5 or SHA-256). Use /stream2 substream for AI.",
)
VIGI_NVR = TPLinkProfile(
    "vigi_nvr", "TP-Link VIGI NVR (per-channel RTSP)", 554, (80, 2020),
    "live/ch/{ch}/stream/1", "live/ch/{ch}/stream/2",
    notes="Per-channel: rtsp://<nvr>/live/ch/<N>/stream/<1|2>. Channel 0 = channel-zero overview (main only).",
)
TAPO = TPLinkProfile(
    "tapo", "TP-Link Tapo (wired only)", 554, (2020,),
    "stream1", "stream2",
    wired_only=True, needs_camera_account=True,
    notes="RTSP/ONVIF on wired models only; create a Camera Account in the Tapo app (separate from Tapo login). "
          "Battery/solar models unsupported. Credentials go in the URL.",
)

PROFILES: dict[str, TPLinkProfile] = {
    "vigi_camera": VIGI_CAMERA,
    "vigi_nvr": VIGI_NVR,
    "tapo": TAPO,
}


def _host(ip: str, port: int) -> str:
    return f"{ip}:{port}"


def _maybe_creds(user: str | None, password: str | None) -> str:
    if user and password:
        return f"{quote(user, safe='')}:{quote(password, safe='')}@"
    return ""


def camera_rtsp_url(
    profile: TPLinkProfile,
    ip: str,
    *,
    user: str | None = None,
    password: str | None = None,
    port: int | None = None,
    stream: str = "main",
) -> str:
    """RTSP URL for a directly-attached VIGI camera or a Tapo camera."""
    port = port or profile.rtsp_port
    path = profile.main_stream if stream == "main" else profile.sub_stream
    return f"rtsp://{_maybe_creds(user, password)}{_host(ip, port)}/{path}"


def nvr_channel_rtsp_url(
    ip: str,
    channel: int,
    *,
    stream: str = "main",
    port: int = 554,
    user: str | None = None,
    password: str | None = None,
) -> str:
    """RTSP URL for one channel of a VIGI NVR.

    channel 1..N are individual cameras; channel 0 is the channel-zero overview.
    stream 1 = main, 2 = sub.
    """
    stream_no = 1 if stream == "main" else 2
    path = f"live/ch/{channel}/stream/{stream_no}"
    return f"rtsp://{_maybe_creds(user, password)}{_host(ip, port)}/{path}"


def list_profiles() -> list[dict]:
    return [
        {
            "vendor": p.vendor,
            "label": p.label,
            "rtsp_port": p.rtsp_port,
            "onvif_ports": list(p.onvif_ports),
            "main_stream": p.main_stream,
            "sub_stream": p.sub_stream,
            "wired_only": p.wired_only,
            "needs_camera_account": p.needs_camera_account,
            "notes": p.notes,
        }
        for p in PROFILES.values()
    ]
