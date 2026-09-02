"""Perf budget gate (Wave 5): the plan's speed claims become enforced.

The audit measured 0.55s TTI and ~120 KB source — competitive advantages
that regress silently (a stray dependency, a heavier first paint). These
tests measure the REAL app served by the REAL ASGI server and fail the
merge when budgets break:

- TTI (time to app interactive, #app not hidden post-load): < 3.0 s
  (headless CI hardware is slower than the audit's desktop; the budget
  keeps 5x headroom over the 0.55s measurement while still catching
  order-of-magnitude regressions like a blocked script chain)
- transferred JS payload: < 300 KB (the whole app stays lighter than
  one React vendor bundle)
- per-view interaction latency (click → data rendered): < 2.5 s
"""
import pytest

pytestmark = pytest.mark.ui

TTI_BUDGET_S = 3.0
JS_BUDGET_BYTES = 300_000
VIEW_LATENCY_BUDGET_S = 2.5


def test_time_to_interactive(server, page):
    import time
    t0 = time.monotonic()
    page.goto(server["base"] + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#app:not(.hidden), #login:not(.hidden)",
                           timeout=TTI_BUDGET_S * 1000)
    tti = time.monotonic() - t0
    assert tti < TTI_BUDGET_S, f"app interactive in {tti:.2f}s > {TTI_BUDGET_S}s"


def test_js_payload_budget(server, page):
    sizes = {}

    def on_response(resp):
        if resp.request.resource_type == "script":
            sizes[resp.url] = len(resp.body()) if resp.status == 200 else 0

    page.on("response", on_response)
    # Visit the heaviest view (live lazy-loads hls.js) + the shell.
    page.goto(server["base"] + "/", wait_until="networkidle")
    page.wait_for_selector("#login:not(.hidden), #app:not(.hidden)")
    total = sum(sizes.values())
    assert total < JS_BUDGET_BYTES, (
        f"JS payload {total / 1024:.0f} KB > {JS_BUDGET_BYTES // 1024} KB "
        f"({len(sizes)} files)")


@pytest.mark.parametrize("view,marker", [
    ("cameras", ".cam-card"),
    ("events", "tr.event-row"),
    ("analytics", "[data-role='an-widgets']"),
    ("people", "[data-person]"),
])
def test_view_latency(logged_in, view, marker):
    import time
    page = logged_in
    page.click("#nav button[data-view='events']")  # warm the shell first
    page.wait_for_selector("tr.event-row")
    page.click(f"#nav button[data-view='{view}']")
    t0 = time.monotonic()
    page.wait_for_selector(marker, timeout=VIEW_LATENCY_BUDGET_S * 1000)
    dt = time.monotonic() - t0
    assert dt < VIEW_LATENCY_BUDGET_S, f"{view} took {dt:.2f}s to show data"
