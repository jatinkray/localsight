"""C-1 guard: every view renders with ZERO console errors under real CSP.

The original timeline bug was found exactly this way — inline styles blocked
by the production CSP header rendered 0-width segments. A console error is
the smoke from that class of fire; this test makes it impossible to merge.

Resource-level network lines for DOCUMENTED states are not product errors
(the views surface them as visible states): 503 = no transcoder/unreachable
camera, 400 = SSRF egress guard, 404 on /live-media/* = the manifest of a
transcode that never started, 401 = pre-login session probes.
"""
import re

import httpx
import pytest

pytestmark = pytest.mark.ui

VIEWS = ["dashboard", "live", "cameras", "events", "timeline", "analytics",
         "people", "alerts", "users", "privacy", "audit"]

# Statuses the UI deliberately surfaces as honest per-view states.
DOCUMENTED_STATUSES = {"400", "401", "403", "404", "409", "410", "422", "503"}


def _is_documented(text: str) -> bool:
    if "/live-media/" in text or "manifest" in text:
        return True  # hls.js probing a transcode that never started
    codes = set(re.findall(r"status of (\d{3})", text))
    return bool(codes) and codes <= DOCUMENTED_STATUSES


def test_no_console_errors_any_view(logged_in):
    page = logged_in
    offenders = []

    def on_console(m):
        if m.type == "error" and not _is_documented(m.text):
            offenders.append(m.text[:200])

    page.on("console", on_console)
    for v in VIEWS:
        page.click(f"#nav button[data-view='{v}']")
        page.wait_for_timeout(1400)  # let every view's fetches settle
    assert not offenders, offenders


def test_csp_header_present(server):
    """The CSP that makes inline styles fail must actually be sent —
    otherwise this whole suite guards a policy that isn't deployed."""
    csp = httpx.get(server["base"] + "/", timeout=10).headers.get(
        "Content-Security-Policy", "")
    assert "style-src 'self'" in csp, f"CSP missing style-src 'self': {csp!r}"
