"""Main-stream recorder.

Per the architecture, the *main* stream is recorded while the *substream* drives
AI. This module records the main stream into short, seekable, time-aligned segments
(HLS-ready .mp4) under the StorageProvider and writes a VideoSegment row per
segment so the archive is queryable and retention-enforceable.

FFmpeg is launched as a confined subprocess (no shell, validated URL — reuses the
SSRF guard via `validate_egress_url`, which also honors the deploy-time allowlist so
private-VLAN camera URLs are accepted). Segment scheduling is pure and unit-tested;
only the subprocess spawn is environment-dependent and is injectable for tests.
"""
from __future__ import annotations

import datetime as dt
import os
import subprocess
from typing import Callable, Optional

from packages.domain.models import VideoSegment
from packages.security.errors import SecurityError
from packages.security.ssrf import validate_egress_url
from packages.video.ffmpeg import build_args  # reuse safe argv builder


def segment_boundary(ts: dt.datetime, seg_seconds: int) -> dt.datetime:
    """Start time of the segment bucket containing `ts` (UTC, floor to seg)."""
    epoch = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
    delta = (ts - epoch).total_seconds()
    aligned = int(delta // seg_seconds) * seg_seconds
    return epoch + dt.timedelta(seconds=aligned)


def segment_key(camera_id: str, start: dt.datetime, ext: str = "mp4", seg_seconds: int = 300) -> str:
    """Deterministic storage key: camera/<id>/<YYYY>/<MM>/<DD>/<HHMMSS>.{ext}.

    Aligned to the segment boundary so the key is stable for the whole window.
    """
    b = segment_boundary(start, seg_seconds)
    return (
        f"camera/{camera_id}/{b.year:04d}/{b.month:02d}/{b.day:02d}/"
        f"{b.hour:02d}{b.minute:02d}{b.second:02d}.{ext}"
    )


def _tmp_path(camera_id: str, start: dt.datetime) -> str:
    return f"/tmp/seg_{camera_id}_{start.timestamp()}.mp4"


class Recorder:
    def __init__(
        self,
        camera_id: str,
        storage,
        crypto=None,
        seg_seconds: int = 300,
        spawn: Callable[..., subprocess.Popen] | None = None,
        on_segment: Callable[[VideoSegment], None] | None = None,
        allowlist: list[str] | None = None,
    ) -> None:
        self.camera_id = camera_id
        self.storage = storage
        self.crypto = crypto
        self.seg_seconds = seg_seconds
        self._spawn = spawn or subprocess.Popen
        self._on_segment = on_segment
        self._allowlist = allowlist
        self._procs: dict[str, subprocess.Popen] = {}
        self._pending: dict[str, VideoSegment] = {}
        self._last_proc: Optional[subprocess.Popen] = None
        self._last_seg: Optional[VideoSegment] = None

    def _build_args(self, url: str, start: dt.datetime) -> list[str]:
        tmp = _tmp_path(self.camera_id, start)
        return [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-rtsp_transport", "tcp", "-i", url,
            "-y", "-t", str(self.seg_seconds),
            "-an", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-f", "mp4", tmp,
        ]

    def record_url(self, url: str, start: dt.datetime) -> VideoSegment:
        """Spawn a recorder for one segment and return the (unsaved) VideoSegment row.

        The caller is responsible for waiting on `last_proc` and then calling
        `finalize_last` to upload the completed clip and persist the row (this keeps
        the interface test-friendly and lets the worker run a continuous loop).
        """
        validate_egress_url(
            url, allowed_schemes={"rtsp", "rtsps", "http", "https"},
            allowlist=self._allowlist,
        )
        seg_start = segment_boundary(start, self.seg_seconds)
        key = segment_key(self.camera_id, seg_start)
        args = self._build_args(url, seg_start)
        try:
            proc = self._spawn(args)
        except (OSError, ValueError) as exc:
            raise SecurityError(f"failed to launch recorder: {exc}") from exc
        seg = VideoSegment(
            camera_id=self.camera_id,
            storage_key=key,
            storage_backend=getattr(self.storage, "backend_name", "local"),
            start_ts=seg_start,
            end_ts=seg_start + dt.timedelta(seconds=self.seg_seconds),
            duration_sec=float(self.seg_seconds),
            size_bytes=0,
        )
        self._procs[key] = proc
        self._pending[key] = seg
        self._last_proc = proc
        self._last_seg = seg
        return seg

    def finalize_last(self) -> Optional[VideoSegment]:
        """Wait for the most recent segment's ffmpeg process to finish, upload the
        clip to storage (if a provider is configured), and return the row with the
        real size. Returns None if the segment failed or is missing.
        """
        seg = self._last_seg
        proc = self._last_proc
        if seg is None or proc is None:
            return None
        self._last_proc = None
        self._last_seg = None
        self._pending.pop(seg.storage_key, None)
        self._procs.pop(seg.storage_key, None)
        if proc.poll() is None:
            try:
                proc.wait()
            except Exception:  # noqa: BLE001 - best-effort wait
                pass
        if proc.returncode not in (0, None):
            return None
        tmp = _tmp_path(self.camera_id, seg.start_ts)
        try:
            with open(tmp, "rb") as fh:
                data = fh.read()
        except OSError:
            return None
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        if not data:
            return None
        if self.storage is not None:
            self.storage.put(seg.storage_key, data, "video/mp4")
        seg.size_bytes = len(data)
        if self._on_segment:
            self._on_segment(seg)
        return seg

    def stop_all(self) -> None:
        for p in self._procs.values():
            if p.poll() is None:
                p.terminate()
        self._procs.clear()
        self._pending.clear()
