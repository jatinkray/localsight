"""Safe FFmpeg invocation. Never use shell=True and never interpolate
user-supplied strings into a shell. We build an argv list and pass it directly
to subprocess, which prevents command injection. The stream URL is validated for
SSRF *before* it is ever passed to FFmpeg.
"""
from __future__ import annotations

import subprocess

from packages.security.errors import SecurityError
from packages.security.ssrf import validate_egress_url


def build_args(
    url: str,
    *,
    width: int = 640,
    height: int = 360,
    fps: int = 5,
    rtsp_transport: str = "tcp",
    hwaccel: str | None = None,
    pix_fmt: str = "rgb24",
) -> list[str]:
    """Return a safe argv list that pipes decoded raw frames to stdout.

    The URL has already been SSRF-validated by the caller; we still re-validate
    here as defense in depth.
    """
    validate_egress_url(url, allowed_schemes={"rtsp", "rtsps", "http", "https"})
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
    args += ["-rtsp_transport", rtsp_transport]
    if hwaccel:
        args += ["-hwaccel", hwaccel]
    args += ["-i", url]
    args += [
        "-an",  # no audio
        "-vf", f"scale={width}:{height},fps={fps}",
        "-pix_fmt", pix_fmt,
        "-f", "rawvideo",
        "-",
    ]
    return args


def open_decoder(args: list[str]) -> subprocess.Popen:
    """Launch FFmpeg as a confined subprocess (no shell, no inheritance of fds
    beyond stdout). Raises SecurityError on spawn failure.

    stderr goes to DEVNULL: it is never drained, and a chatty ffmpeg on a
    full 64 KB stderr pipe would block the process mid-encode (a classic
    subprocess deadlock). Errors surface via exit code and the empty stdout.
    """
    try:
        return subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, ValueError) as exc:
        raise SecurityError(f"failed to launch decoder: {exc}") from exc
