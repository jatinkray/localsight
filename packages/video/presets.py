"""Vendor stream-URL presets (broad camera compatibility).

LocalSight is open-camera (BYOC). This module centralizes the per-vendor RTSP / CGI
/ ISAPI / ONVIF URL conventions so the UI and the bulk-provisioning API can suggest
correct URLs for the most common fleets, mirroring what Milestone/Axis expose in
their device libraries. TP-Link VIGI is kept for backward compatibility.

Conventions covered: Axis, Hanwha (Wisenet), Bosch, Reolink, Hikvision (ISAPI),
Dahua, Uniview, and GB/T 28181 (China national standard) plus generic ONVIF.
"""
from __future__ import annotations

from typing import Dict, List


def _tplink(nvr_ip: str, ch: int, stream: str, user=None, password=None, port: int = 554) -> str:
    auth = f"{user}:{password}@" if user else ""
    s = "main" if stream == "main" else "sub"
    return f"rtsp://{auth}{nvr_ip}:{port}/live/ch{ch}/stream/{s}"


def _axis(cam_ip: str, stream: str, user=None, password=None, port: int = 554) -> str:
    auth = f"{user}:{password}@" if user else ""
    s = "1" if stream == "main" else "2"
    return f"rtsp://{auth}{cam_ip}:{port}/axis-media/media.amp?camera=1&resolution=640x360&videostream={s}"


def _hanwha(cam_ip: str, stream: str, user=None, password=None, port: int = 554) -> str:
    auth = f"{user}:{password}@" if user else ""
    s = "1" if stream == "main" else "2"
    return f"rtsp://{auth}{cam_ip}:{port}/profile{('' if s=='1' else '2')}"


def _bosch(cam_ip: str, stream: str, user=None, password=None, port: int = 554) -> str:
    auth = f"{user}:{password}@" if user else ""
    return f"rtsp://{auth}{cam_ip}:{port}/rtsp_tunnel?channel=1&stream={1 if stream == 'main' else 2}"


def _reolink(cam_ip: str, ch: int, stream: str, user=None, password=None, port: int = 554) -> str:
    auth = f"{user}:{password}@" if user else ""
    s = "0" if stream == "main" else "1"
    return f"rtsp://{auth}{cam_ip}:{port}/h264Preview_{ch:02d}_{s}"


def _hikvision(cam_ip: str, stream: str, user=None, password=None, port: int = 554) -> str:
    auth = f"{user}:{password}@" if user else ""
    s = "101" if stream == "main" else "102"
    return f"rtsp://{auth}{cam_ip}:{port}/Streaming/Channels/{s}"


def _dahua(cam_ip: str, stream: str, user=None, password=None, port: int = 554) -> str:
    auth = f"{user}:{password}@" if user else ""
    s = "1" if stream == "main" else "2"
    return f"rtsp://{auth}{cam_ip}:{port}/cam/realmonitor?channel=1&subtype={0 if s=='1' else 1}"


def _uniview(cam_ip: str, stream: str, user=None, password=None, port: int = 554) -> str:
    auth = f"{user}:{password}@" if user else ""
    s = "1" if stream == "main" else "2"
    return f"rtsp://{auth}{cam_ip}:{port}/media/video{('' if s=='1' else '2')}"


# GB/T 28181 uses SIP signaling; the playback/stream URL is assigned by the SIP
# server, so presets are advisory (the platform provisions via SIP out-of-band).
_GBT28181_NOTE = "GB/T 28181 streams are negotiated over SIP; configure the SIP server + device ID in the NVR panel."


VENDORS: Dict[str, Dict] = {
    "vigi_nvr": {"label": "TP-Link VIGI NVR", "fn": _tplink, "nvr": True, "channels": True},
    "axis": {"label": "Axis Communications", "fn": _axis, "nvr": False},
    "hanwha": {"label": "Hanwha (Wisenet)", "fn": _hanwha, "nvr": False},
    "bosch": {"label": "Bosch", "fn": _bosch, "nvr": False},
    "reolink": {"label": "Reolink", "fn": _reolink, "nvr": False, "channels": True},
    "hikvision": {"label": "Hikvision (ISAPI)", "fn": _hikvision, "nvr": False},
    "dahua": {"label": "Dahua (CGI)", "fn": _dahua, "nvr": False},
    "uniview": {"label": "Uniview", "fn": _uniview, "nvr": False},
    "onvif": {"label": "Generic ONVIF (auto-discover)", "fn": None, "nvr": False},
    "gbt28181": {"label": "GB/T 28181 (CN standard)", "fn": None, "nvr": True, "note": _GBT28181_NOTE},
}


def list_profiles() -> List[dict]:
    out = []
    for key, v in VENDORS.items():
        out.append({
            "vendor": key,
            "label": v["label"],
            "nvr": v.get("nvr", False),
            "channels": v.get("channels", False),
            "note": v.get("note"),
        })
    return out


def build_url(vendor: str, *, cam_ip: str = "", channel: int = 1, stream: str = "sub",
              user: str | None = None, password: str | None = None, port: int = 554) -> str:
    if vendor not in VENDORS:
        raise KeyError(f"unknown vendor preset: {vendor}")
    fn = VENDORS[vendor]["fn"]
    if fn is None:
        raise ValueError(f"vendor {vendor} has no static preset (use ONVIF discovery / SIP)")
    if VENDORS[vendor].get("channels"):
        return fn(cam_ip, channel, stream, user, password, port)
    return fn(cam_ip, stream, user, password, port)
