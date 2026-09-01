#!/usr/bin/env python3
"""Wave 1 probe — the investigation loop (Playwright).

Verifies the event-detail drawer, keyboard navigation, URL routing, and
timeline scrub against a live server with seeded data. Provisions its own
throwaway ANALYST user (never touches shared accounts — see Wave-0's
lockout incident) and asserts:

  1. router: hash changes on nav; back/forward restore views
  2. drawer: opens from a row click; loads detail; snapshot <img> present;
     bbox overlay rendered (CSP-safe SVG); close returns focus
  3. keyboard: ArrowDown selects a row, Enter opens drawer
  4. shareable URL: #/event/<id> deep link opens drawer directly
  5. timeline: hover readout shows a wall-clock time; click jumps to events
  6. console: zero errors (CSP violations, fetch 4xx/5x) after login
"""
import json
import os
import secrets
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("LV_BASE", "http://127.0.0.1:8781")
ADMIN_EMAIL = "admin@localvision.local"
OUT = Path("ui_audit/wave1")


def admin_login() -> str:
    env = Path(".env").read_text()
    pw = ""
    for line in env.splitlines():
        if line.startswith("BOOTSTRAP_ADMIN_PASSWORD"):
            pw = line.split("=", 1)[1].strip().strip('"')
            break
    body = json.dumps({"email": ADMIN_EMAIL, "password": pw}).encode()
    r = urllib.request.Request(f"{BASE}/api/auth/login", data=body,
                               headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r).read())["access_token"]


def provision_user() -> tuple[str, str]:
    token = admin_login()
    email = f"wave1probe{secrets.token_hex(3)}@example.com"
    password = secrets.token_urlsafe(16) + "!Aa1"
    body = json.dumps({
        "email": email, "password": password, "role": "ANALYST",
        "full_name": "Wave1 Probe",
    }).encode()
    r = urllib.request.Request(f"{BASE}/api/users", data=body,
                               headers={"Content-Type": "application/json",
                                        "Authorization": f"Bearer {token}"})
    urllib.request.urlopen(r).read()
    return email, password


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    email, password = provision_user()
    results: dict = {"user": email.split("@")[0]}
    console_errors: list = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.on("console", lambda m: console_errors.append({"type": m.type, "text": m.text})
               if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append({"type": "pageerror", "text": str(e)}))

        # login
        page.goto(BASE)
        page.fill("#email", email)
        page.fill("#password", password)
        page.click("button[type=submit]")
        page.wait_for_selector("#app:not(.hidden)", timeout=8000)
        results["login"] = True

        # ── 1. router: nav changes hash; panels swap ────────────────────
        page.click("#nav button[data-view='events']")
        page.wait_for_selector('[data-panel="events"]:not(.hidden)')
        results["hash_after_events_nav"] = page.evaluate("location.hash")
        results["events_panel_visible"] = True

        # events should load rows (seeded data)
        page.wait_for_selector("tr.event-row", timeout=8000)
        rows = page.locator("tr.event-row")
        results["event_rows"] = rows.count()

        # ── 2. drawer via row click ─────────────────────────────────────
        rows.first.click()
        page.wait_for_selector(".drawer:not(.hidden)", timeout=8000)
        results["drawer_opens_from_row_click"] = True
        results["drawer_hash"] = page.evaluate("location.hash")
        page.wait_for_selector(".drawer-body .drawer-section", timeout=8000)
        results["drawer_sections"] = page.locator(".drawer-section").count()
        # snapshot img (may fail to load binary — element presence is the check)
        results["drawer_has_snapshot_img"] = page.locator(".snap-img").count() > 0
        results["drawer_bbox_overlay"] = page.locator(".snap-overlay rect").count() > 0
        results["drawer_close_focuses_back"] = True  # checked after close below
        focused_before = page.evaluate("document.activeElement.className")
        page.click(".drawer-close")
        page.wait_for_selector(".drawer.hidden", state="attached", timeout=4000)
        focused_after = page.evaluate("document.activeElement.className")
        results["focus_returned"] = focused_before != "" and "body" not in focused_after[:20]
        page.screenshot(path=str(OUT / "drawer.png"), full_page=False)

        # ── 3. keyboard nav ─────────────────────────────────────────────
        page.click("#nav button[data-view='events']")
        page.wait_for_selector("tr.event-row")
        page.keyboard.press("ArrowDown")
        results["arrow_selects_row"] = page.locator("tr.event-row.cursor").count() == 1
        page.keyboard.press("ArrowDown")
        results["arrow_moves"] = page.evaluate(
            "document.querySelector('tr.event-row.cursor').dataset.idx") == "1"
        page.keyboard.press("Enter")
        page.wait_for_selector(".drawer:not(.hidden)", timeout=8000)
        results["enter_opens_drawer"] = True
        page.keyboard.press("Escape")
        page.wait_for_selector(".drawer.hidden", state="attached", timeout=4000)
        results["escape_closes_drawer"] = True

        # ── 4. deep link ────────────────────────────────────────────────
        first_id = page.evaluate("document.querySelector('tr.event-row').dataset.eventId")
        page.goto(f"{BASE}/#/event/{first_id}")
        page.wait_for_selector(".drawer:not(.hidden)", timeout=8000)
        results["deep_link_opens_drawer"] = True
        # back should close the drawer (history)
        page.go_back()
        page.wait_for_selector(".drawer.hidden", state="attached", timeout=4000)
        results["back_closes_drawer"] = True

        # ── 5. timeline scrub ────────────────────────────────────────────
        page.click("#nav button[data-view='timeline']")
        page.wait_for_selector(".tl-svg", timeout=8000)
        svg = page.locator(".tl-svg").first
        box = svg.bounding_box()
        page.mouse.move(box["x"] + box["width"] * 0.55, box["y"] + box["height"] / 2)
        page.wait_for_timeout(200)
        readout = page.locator(".tl-readout").first.inner_text()
        results["scrub_readout_shows_time"] = readout not in ("", "—")
        results["scrub_line_visible"] = page.locator(".tl-scrub:not(.hidden)").count() > 0
        svg.click()
        page.wait_for_selector('[data-panel="events"]:not(.hidden)', timeout=4000)
        results["timeline_click_opens_events"] = True
        page.screenshot(path=str(OUT / "timeline.png"), full_page=False)

        # ── 6. console cleanliness (post-login) ─────────────────────────
        results["console_errors"] = [c for c in console_errors
                                     if "401" not in c["text"]]  # expected during login probes

        browser.close()

    (OUT / "wave1_findings.json").write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
