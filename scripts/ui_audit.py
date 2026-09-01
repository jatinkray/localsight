"""Playwright UI/UX audit for the LocalVision dashboard.

Drives the real app (seeded dev DB) through every view, captures screenshots,
console errors, failed requests, and interaction timings.

Usage:
    .venv/bin/python scripts/ui_audit.py --base http://127.0.0.1:8777 \
        --email admin@localvision.local --password '…' --out ui_audit/
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

VIEWS = ["dashboard", "cameras", "events", "timeline", "people", "audit"]


def audit(base: str, email: str, password: str, out: Path, mobile: bool) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    findings: dict = {
        "base": base, "mobile": mobile, "console": [],
        "requests_failed": [], "timings": {},
    }

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx_kwargs = {"viewport": {"width": 1280, "height": 800}}
        if mobile:
            ctx_kwargs = {
                "viewport": {"width": 390, "height": 844},
                "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
                "is_mobile": True,
                "has_touch": True,
            }
        ctx = browser.new_context(**ctx_kwargs)
        page = ctx.new_page()
        page.on("console", lambda m: findings["console"].append(
            {"type": m.type, "text": m.text[:300]}))
        page.on("requestfailed", lambda r: findings["requests_failed"].append(
            {"url": r.url[:200], "failure": (r.failure or {}).get("errorText", "?")}))
        page.on("pageerror", lambda e: findings["console"].append(
            {"type": "pageerror", "text": str(e)[:300]}))

        # ── login screen ──────────────────────────────────────────────────
        t0 = time.time()
        page.goto(base, wait_until="networkidle")
        findings["timings"]["first_paint_login"] = round(time.time() - t0, 2)
        page.screenshot(path=str(out / ("01-login.png" if not mobile else "m01-login.png")))

        # login form a11y probe
        inputs = page.locator("input")
        findings["login_inputs"] = inputs.count()
        btn = page.locator("#login-form button[type=submit]")
        findings["login_button_text"] = btn.inner_text()

        # failed login (wrong password)
        page.fill("#email", email)
        page.fill("#password", "wrong-password")
        page.click("#login-form button[type=submit]")
        page.wait_for_timeout(1200)
        err = page.locator("#login-error").inner_text()
        findings["login_error_message"] = err
        shot = "02-login-error.png" if not mobile else "m02-login-error.png"
        page.screenshot(path=str(out / shot))

        # real login
        page.fill("#password", password)
        page.click("#login-form button[type=submit]")
        try:
            page.wait_for_selector("#app:not(.hidden)", timeout=8000)
            findings["timings"]["login_to_dashboard"] = round(time.time() - t0, 2)
        except Exception:
            findings["login_failed"] = True
            page.screenshot(path=str(out / "02b-login-stuck.png"))
            browser.close()
            return findings
        page.wait_for_timeout(600)
        page.screenshot(path=str(out / ("03-dashboard.png" if not mobile else "m03-dashboard.png")))

        # dashboard content probe
        findings["dashboard_stats"] = page.locator("#stat-cards .card").count()
        findings["dashboard_health_rows"] = page.locator("#health div").count()
        findings["dashboard_health_text"] = page.locator("#health").inner_text()[:400]

        # nav structure
        findings["nav_items"] = page.locator("nav button").all_inner_texts()

        # ── each view ─────────────────────────────────────────────────────
        for i, view in enumerate(VIEWS[1:], start=4):
            if mobile:
                page.click("#nav-toggle")
                page.wait_for_timeout(300)
            page.click(f'nav button[data-view="{view}"]')
            if mobile:
                page.wait_for_timeout(200)
            page.wait_for_timeout(900)
            findings["timings"][f"view_{view}"] = round(time.time() - t0, 2)
            shot = f"{i:02d}-{view}.png" if not mobile else f"m{i:02d}-{view}.png"
            page.screenshot(path=str(out / shot))
            if view == "events":
                findings["events_rows"] = page.locator("#events-table tbody tr").count()
                findings["events_first_row"] = (
                    page.locator("#events-table tbody tr").first.inner_text().replace("\n", " | ")
                    if page.locator("#events-table tbody tr").count()
                    else ""
                )
                # click "view" link — the only event drill-down affordance
                link = page.locator("#events-table a").first
                if link.count():
                    with page.expect_popup() as pop:
                        link.click()
                    dp = pop.value
                    dp.wait_for_timeout(1200)
                    dp.screenshot(path=str(out / "04b-event-detail-raw-json.png"))
                    findings["event_detail_raw"] = dp.url[:120]
                    dp.close()
            if view == "timeline":
                page.fill("#tl-date", "")
                page.click("#tl-go")
                page.wait_for_timeout(900)
                page.screenshot(path=str(out / "04c-timeline-empty-date.png"))

        # ── error/empty state probes ─────────────────────────────────────
        def open_nav_if_mobile():
            if mobile:
                page.click("#nav-toggle")
                page.wait_for_timeout(300)

        open_nav_if_mobile()
        page.click('nav button[data-view="events"]')
        page.fill("#ev-camera", "nonexistent-camera-id-xyz")
        page.click("#ev-search")
        page.wait_for_timeout(900)
        page.screenshot(path=str(out / "07-events-no-results.png"))
        findings["events_noresult_rows"] = page.locator("#events-table tbody tr").count()

        # people enroll flow (form UX)
        open_nav_if_mobile()
        page.click('nav button[data-view="people"]')
        page.fill("#person-label", "audit-temp-person")
        page.fill("#person-name", "Audit Temp")
        page.click("#person-form button[type=submit]")
        page.wait_for_timeout(900)
        findings["people_after_enroll"] = page.locator("#people-list table tr").count()
        page.screenshot(path=str(out / "08-people-after-enroll.png"))

        # keyboard / focus probe: tab order through a view
        seq = []
        for _ in range(12):
            page.keyboard.press("Tab")
            el = page.evaluate(
                "() => document.activeElement"
                " ? (document.activeElement.id || document.activeElement.tagName) : null"
            )
            seq.append(el)
        findings["tab_order"] = seq

        # keyboard / focus probe: tab through login elements order
        findings["doc_title"] = page.title()
        findings["html_lang"] = page.locator("html").get_attribute("lang")
        findings["viewport_meta"] = page.locator("meta[name=viewport]").count()

        # storage token probe (XSS surface + token persistence design)
        findings["token_in_localstorage"] = page.evaluate(
            "() => Object.keys(localStorage).filter(k => k.includes('token')).length"
        )

        ctx.close()
        browser.close()
    return findings


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8777")
    ap.add_argument("--email", default="admin@localvision.local")
    ap.add_argument("--password", required=True)
    ap.add_argument("--out", default="ui_audit")
    ap.add_argument("--mobile", action="store_true")
    args = ap.parse_args()
    result = audit(args.base, args.email, args.password, Path(args.out), args.mobile)
    (Path(args.out) / "findings.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2)[:3000])
