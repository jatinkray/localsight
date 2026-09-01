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

    def __init__(self, url: str, width: int = 640, height: int = 360, fps: int = 5, hwaccel=None):
        self.args = __import__("packages.video.ffmpeg", fromlist=["build_args"]).build_args(
            url, width=width, height=height, fps=fps, hwaccel=hwaccel
        )
        self.frame_bytes = width * height * 3  # rgb24

    def frames(self) -> Iterator[Tuple[object, dt.datetime]]:
        from packages.video.ffmpeg import open_decoder

        proc = open_decoder(self.args)
        assert proc.stdout
        try:
            while True:
                buf = proc.stdout.read(self.frame_bytes)
                if not buf or len(buf) < self.frame_bytes:
                    break
                yield (buf, dt.datetime.now(dt.timezone.utc))
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
