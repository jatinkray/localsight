"""A11y gate: axe-core scans every view; violations block the merge.

Wave 4's one-shot audit becomes a permanent gate here. The three real
defects it caught (tertiary-text contrast, missing h1, login without a
main landmark) are exactly the kind of regression this test exists to
prevent re-landing.
"""
import pytest

pytestmark = pytest.mark.ui

VIEWS = ["dashboard", "live", "cameras", "events", "timeline", "analytics",
         "people", "alerts", "users", "privacy", "audit"]

# Violations that would justify a waiver (product decisions, not defects):
# none today — the bar is zero.
WAIVED = set()


def _scan(axe, view, violations):
    res = axe()
    for v in res["violations"]:
        if v["id"] in WAIVED:
            continue
        violations.append(
            f"{view}: {v['id']} ({v['impact']}) — {v['help'][:80]} "
            f"[{len(v['nodes'])} nodes]")


def test_axe_all_views(logged_in, axe):
    violations = []
    for v in VIEWS:
        logged_in.click(f"#nav button[data-view='{v}']")
        logged_in.wait_for_timeout(1400)
        _scan(axe, v, violations)
    assert not violations, violations


def test_axe_login_screen(server, page, axe):
    """The login screen is a view too — it got a <main> landmark for this."""
    page.goto(server["base"] + "/")
    page.wait_for_selector("#login:not(.hidden)")
    page.wait_for_timeout(400)
    res = axe()
    ids = [v["id"] for v in res["violations"]]
    assert not ids, ids


def test_skip_link_first(logged_in):
    """The skip link must be the first tab stop from page start.

    After login the sequential-focus starting point sits at the sign-in
    button (Chromium keeps the "sequential focus navigation starting
    point" where focus last was), so a reload is the honest way to test
    the from-page-start tab order a keyboard user gets on a fresh load.
    The session survives via the rotating refresh token — which is also
    the C-6 flow working end to end.
    """
    logged_in.reload()
    logged_in.wait_for_selector("#app:not(.hidden)")
    logged_in.wait_for_timeout(500)
    logged_in.keyboard.press("Tab")
    focused = logged_in.evaluate(
        "document.activeElement && (document.activeElement.className || '')")
    assert "skip-link" in focused, f"first Tab stop was: {focused!r}"
