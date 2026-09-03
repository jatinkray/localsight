"""Shared live-view configuration.

Single source of truth for the live media directory and stream lifecycle
thresholds. The app factory (mount point), the live router (transcode root), and
operators all read these values; before this module existed the mount was
hard-coded relative to `main.py` while the router read `LOCALSIGHT_LIVE_DIR`,
so setting the env var made ffmpeg write segments to a directory the app never
served (see docs/reviews/CODE_ANALYSIS_REPORT.md D-4).
"""
from __future__ import annotations

import os

# Environment lookup with rename back-compat: LocalSight was previously
# LocalVision, and deployed environments may still export the old names.
# New name wins; old name is honored so upgrades don't silently reset config.
def _env(name: str, default: str = "") -> str:
    legacy = name.replace("LOCALSIGHT_", "LOCALVISION_", 1)
    return os.environ.get(name) or os.environ.get(legacy) or default


# Directory transcoded LL-HLS segments are written to and served read-only
# from /live-media. Overridable via LOCALSIGHT_LIVE_DIR.
LIVE_DIR = _env(
    "LOCALSIGHT_LIVE_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "live"),
).rstrip("/") or "./data/live"

# A stream whose manifest hasn't been requested for this long is considered
# abandoned (viewer closed the tab) and terminated by the reaper.
LIVE_IDLE_TIMEOUT_SEC = int(_env("LOCALSIGHT_LIVE_IDLE_TIMEOUT_SEC", "300"))

# Hard ceiling per transcode. Long-lived encodes drift (keyframes, tmpfs
# segment churn); the reaper restarts viewers still actively probing.
LIVE_MAX_DURATION_SEC = int(_env("LOCALSIGHT_LIVE_MAX_DURATION_SEC", "14400"))
