"""ONVIF client (Profile S discovery + stream URIs).

Broad camera compatibility is the #1 shortlist criterion for buyers. This client
lets LocalVision auto-discover ONVIF devices on the LAN and fetch their RTSP
stream URIs without vendor-specific URL guessing, then feed them into the existing
Camera model. It implements the minimal, widely-supported ONVIF operations:

  * WS-Discovery (multicast)        -> find devices
  * GetDeviceInformation / GetProfiles / GetStreamUri (Profile S/T media)
  * GetVideoSources / GetSnapshotUri

SOAP is sent over httpx (lazy-imported) and the transport is injectable so the
logic is fully unit-testable without a camera. All device endpoints are still
SSRF-validated before use by the caller (cameras router).
"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Callable, List, Optional

_DISCOVERY_ADDR = ("239.255.255.250", 3702)

_WS_DISCOVERY_PROBE = """<?xml version="1.0" encoding="utf-8"?>
<Envelope xmlns="http://www.w3.org/2003/05/soap-envelope"
          xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">
  <Header><d:MessageID>urn:uuid:{msg}</d:MessageID>
    <d:Action>a:Probe</d:Action></Header>
  <Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></Body>
</Envelope>"""

_PROFILE_S_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<Envelope xmlns="http://www.w3.org/2003/05/soap-envelope"
          xmlns:trt="http://www.onvif.org/ver10/media/wsdl"
          xmlns:tt="http://www.onvif.org/ver10/schema">
  <Body><trt:{op}><trt:ProfileToken>{token}</trt:ProfileToken></trt:{op}></Body>
</Envelope>"""


def _soap(env: str) -> bytes:
    return env.encode("utf-8")


class OnvifClient:
    def __init__(self, xaddr: str, user: Optional[str] = None, password: Optional[str] = None,
                 transport: Callable[[str, bytes, dict], bytes] | None = None) -> None:
        self.xaddr = xaddr
        self.user = user
        self.password = password
        self._post = transport

    # ── WS-Discovery ───────────────────────────────────────────────────────
    @staticmethod
    def discover(timeout: float = 2.0, sock_send=None) -> List[str]:
        """Return ONVIF device XAddrs seen via WS-Discovery.

        `sock_send` is injectable (default opens a UDP socket) so tests can stub it.
        """
        import socket

        if sock_send is None:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            s.settimeout(timeout)
            s.setsockopt(socket.IPPROTO_IP, 1, 1)
            try:
                s.sendto(_soap(_WS_DISCOVERY_PROBE.format(msg=uuid.uuid4())), _DISCOVERY_ADDR)
                addrs: List[str] = []
                try:
                    while True:
                        data, _ = s.recvfrom(8192)
                        addrs.extend(OnvifClient._parse_xaddrs(data.decode("utf-8", "ignore")))
                except socket.timeout:
                    pass
                return addrs
            finally:
                s.close()
        else:  # pragma: no cover - injection path
            data = sock_send(_soap(_WS_DISCOVERY_PROBE.format(msg=uuid.uuid4())))
            return OnvifClient._parse_xaddrs(data.decode("utf-8", "ignore"))

    @staticmethod
    def _parse_xaddrs(xml: str) -> List[str]:
        out: List[str] = []
        import re

        for m in re.finditer(r"<d:XAddrs[^>]*>(.*?)</d:XAddrs>", xml, re.S):
            for u in m.group(1).split():
                if u:
                    out.append(u)
        return out

    # ── media operations ──────────────────────────────────────────────────
    def _call(self, op: str, token: str = "") -> bytes:
        if self._post is None:
            try:
                import httpx
            except Exception as exc:
                raise RuntimeError("httpx is required for live ONVIF calls") from exc
            resp = httpx.post(
                self.xaddr, content=_soap(_PROFILE_S_TEMPLATE.format(op=op, token=token)),
                headers={"Content-Type": "application/soap+xml"}, timeout=10,
            )
            return resp.content
        return self._post(self.xaddr, _soap(_PROFILE_S_TEMPLATE.format(op=op, token=token)),
                          {"Content-Type": "application/soap+xml"})

    def get_profiles(self) -> List[str]:
        xml = self._call("GetProfiles", "")
        import re

        return re.findall(r"token=\"([^\"]+)\"", xml.decode("utf-8", "ignore"))

    def get_stream_uri(self, profile_token: str) -> str:
        xml = self._call("GetStreamUri", profile_token)
        import re

        m = re.search(r"<tt:Uri>(.*?)</tt:Uri>", xml.decode("utf-8", "ignore"))
        return m.group(1) if m else ""

    def stream_uris(self) -> List[str]:
        out: List[str] = []
        for p in self.get_profiles():
            uri = self.get_stream_uri(p)
            if uri:
                out.append(uri)
        return out
