"""Video ingestion: frame sources, a resilient stream gateway, recording, ONVIF,
and vendor presets."""

from packages.video import ffmpeg, gateway, onvif, presets, sources, tplink

__all__ = ["ffmpeg", "gateway", "onvif", "presets", "sources", "tplink"]
