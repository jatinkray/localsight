"""SSRF / egress guard for operator-supplied destinations (camera/NVR URLs).

Cameras and NVRs are configured by authenticated users, so a malicious or
compromised operator could point the platform at internal services
(127.0.0.1, cloud metadata 169.254.169.254, management endpoints) and use it as
a network proxy. This module rejects such targets *before* any connection is
made. It is the application-layer control; it must be paired with network-level
egress restrictions (see docs/security/network.md).
"""
from __future__ import annotations

import ipaddress
import socket
import urllib.parse

from packages.security.errors import UnsafeUrlError

# Addresses that must never be reachable through the platform.
_BLOCKED_NETS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT
    ipaddress.ip_network("127.0.0.0/8"),    # loopback
    ipaddress.ip_network("169.254.0.0/16"),  # link-local + cloud metadata
    ipaddress.ip_network("172.16.0.0/12"),   # RFC1918
    ipaddress.ip_network("192.168.0.0/16"),  # RFC1918
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),        # ULA
    ipaddress.ip_network("fe80::/10"),       # link-local
]

_ALLOWED_SCHEMES = {"http", "https", "rtsp", "rtsps"}


def _is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(ip in net for net in _BLOCKED_NETS)


def validate_egress_url(
    url: str,
    *,
    allowlist: list[str] | None = None,
    allowed_schemes: set[str] | None = None,
) -> urllib.parse.ParseResult:
    """Validate a user-supplied URL. Returns the parsed result on success.

    Raises UnsafeUrlError if the scheme is invalid, the host cannot be
    resolved, or any resolved address is private/loopback/link-local/metadata
    unless explicitly present in `allowlist` (IP or CIDR).
    """
    allow = [ipaddress.ip_network(c.strip()) for c in (allowlist or []) if c.strip()]
    schemes = allowed_schemes or _ALLOWED_SCHEMES

    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError as exc:
        raise UnsafeUrlError(f"malformed URL: {exc}") from exc

    if parsed.scheme.lower() not in schemes:
        raise UnsafeUrlError(f"scheme {parsed.scheme!r} not permitted")
    if not parsed.hostname:
        raise UnsafeUrlError("missing host")

    # Allowlist bypass (e.g. the deployed camera VLAN CIDR).
    allowlisted = any(
        _host_in_network(parsed.hostname, net) for net in allow
    )

    # Resolve once; reject on resolution failure (fail closed).
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"cannot resolve host {parsed.hostname!r}: {exc}") from exc

    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if allowlisted:
            continue
        if _is_blocked(ip):
            raise UnsafeUrlError(
                f"destination {ip} ({parsed.hostname}) is a private/loopback/"
                f"link-local address and not on the egress allowlist"
            )
    return parsed


def _host_in_network(host: str, net: ipaddress.IPv4Network | ipaddress.IPv6Network) -> bool:
    try:
        return ipaddress.ip_address(host) in net
    except ValueError:
        return False
