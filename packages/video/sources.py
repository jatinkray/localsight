"""Frame sources. A FrameSource yields (frame, timestamp) tuples. `frame` is
opaque: None for the synthetic source, raw bytes for FFmpeg, an image payload
for the image source. Detectors that need pixels receive it unchanged.
"""
from __future__ import annotations

import datetime as dt
import os
import time
from abc import ABC, abstractmethod
from typing import Iterator, Tuple


class FrameSource(ABC):
    @abstractmethod
    def frames(self) -> Iterator[Tuple[object, dt.datetime]]:
        ...


class SyntheticFrameSource(FrameSource):
    """Emits timestamps at `fps` with no pixel data. Used when no camera/FFmpeg
    is available so the pipeline remains runnable and testable."""

    def __init__(self, fps: int = 5, duration_sec: float | None = None) -> None:
        self.fps = max(1, fps)
        self.duration = duration_sec

    def frames(self) -> Iterator[Tuple[object, dt.datetime]]:
        start = dt.datetime.now(dt.timezone.utc)
        n = 0
        while True:
            if self.duration and n / self.fps >= self.duration:
                break
            yield (None, start + dt.timedelta(seconds=n / self.fps))
            n += 1
            time.sleep(1.0 / self.fps)


class ImageFileSource(FrameSource):
    """Repeatedly yields a single image file's bytes (for tests/demos)."""

    def __init__(self, path: str, fps: int = 5, loops: int = 50) -> None:
        self.path = path
        self.fps = fps
        self.loops = loops

    def frames(self) -> Iterator[Tuple[object, dt.datetime]]:
        with open(self.path, "rb") as fh:
            data = fh.read()
        start = dt.datetime.now(dt.timezone.utc)
        for i in range(self.loops):
            yield (data, start + dt.timedelta(seconds=i / self.fps))
            time.sleep(1.0 / self.fps)


class FFmpegFrameSource(FrameSource):
    """Decodes a stream via FFmpeg into raw frames. Requires FFmpeg installed."""

    def __init__(self, url: str, width: int = 640, height: int = 360, fps: int = 5,
                 hwaccel=None, allowlist: list[str] | None = None):
        self.width = width
        self.height = height
        self.allowlist = allowlist
        self.args = __import__("packages.video.ffmpeg", fromlist=["build_args"]).build_args(
            url, width=width, height=height, fps=fps, hwaccel=hwaccel, allowlist=allowlist
        )
        self.frame_bytes = width * height * 3  # rgb24

    def frames(self) -> Iterator[Tuple[object, dt.datetime]]:
        from packages.video.ffmpeg import open_decoder

        # The pipeline's consumers (detector, motion gate, ANPR crop) work on
        # pixel arrays; a raw-bytes frame is silently skipped by the reference
        # detector, so a real RTSP camera would stream for hours and produce
        # zero detections/events. Decode into an ndarray here when numpy is
        # available (it is a declared runtime dependency for detection); the
        # bytes fallback keeps the source usable without numpy.
        try:
            import numpy
        except ImportError:  # pragma: no cover - numpy is a runtime dep
            numpy = None  # type: ignore[assignment]

        def _decode(buf: bytes):
            if numpy is None:
                return buf
            return numpy.frombuffer(buf, dtype=numpy.uint8).reshape(
                self.height, self.width, 3
            )

        proc = open_decoder(self.args)
        assert proc.stdout
        n_frames = 0
        ended_normally = False
        try:
            while True:
                buf = proc.stdout.read(self.frame_bytes)
                if not buf or len(buf) < self.frame_bytes:
                    ended_normally = True
                    break
                n_frames += 1
                yield (_decode(buf), dt.datetime.now(dt.timezone.utc))
        finally:
            # Terminate AND wait: a terminate without wait leaves the exited
            # ffmpeg as a zombie on every generator close (client disconnect,
            # camera restart), pinning a PID slot until the worker restarts.
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001 - best-effort reap
                try:
                    proc.kill()
                except OSError:
                    pass
        # A live RTSP stream never ends on its own. Reaching here with
        # ended_normally means ffmpeg exited (camera dropped, unreachable
        # broker, 404 path) — NOT a clean EOF. Returning would make
        # StreamGateway treat this finite-source style "clean end" as terminal
        # and kill the camera thread permanently; raising routes it into the
        # gateway's designed reconnect-with-backoff path instead. (If the
        # consumer closed the generator, GeneratorExit already propagated and
        # this line never runs.)
        if ended_normally:
            raise RuntimeError(
                f"rtsp stream ended after {n_frames} frame(s) "
                f"(camera dropped or unreachable)"
            )
