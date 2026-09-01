"""Flow probes: token lifecycle, dead-end flows, XSS surface, error states.

Wave-0 verification against the rebuilt UI. Provisions a THROWAWAY USER per
run (via the seeded admin API) so a wrong-password probe never locks the
shared dev accounts — the first version of this script locked
auditor@ out for 15 minutes and every subsequent probe 423'd.

Checks:
1. Session restore: sessionStorage refresh token survives reload (C-6).
2. Genuinely dead session → login screen (not a blank app).
3. API failure → error state with retry (C-12), never blank.
4. XSS: person label with <img onerror> renders as inert text (C-2).
5. Double-submit: enroll button disables during flight (C-13).
6. Login error copy differentiated (C-7).
"""
from __future__ import annotations

import json
import os
import re
import secrets
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("LV_BASE", "http://127.0.0.1:8779")
ADMIN_EMAIL = "admin@localvision.local"
ADMIN_PASSWORD_FILE = Path(".env")


def admin_login() -> str:
    """Login as the seeded bootstrap admin to provision the probe user."""
    # Read the real password from .env (never hardcode dev secrets here).
    pw = None
    if ADMIN_PASSWORD_FILE.exists():
        for line in ADMIN_PASSWORD_FILE.read_text().splitlines():
            if line.startswith("BOOTSTRAP_ADMIN_PASSWORD="):
                pw = line.split("=", 1)[1].strip().strip('"')
                break
    body = json.dumps({"email": ADMIN_EMAIL, "password": pw}).encode()
    r = urllib.request.Request(
        f"{BASE}/api/auth/login", data=body,
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r).read())["access_token"]


def provision_probe_user() -> tuple[str, str]:
    """Create a fresh SECURITY_OPERATOR user for this probe run.

    ANALYST lacks person:enroll, which the C-2/C-13 probes need (they
    enroll payload persons). The old seeded DB masked this: a leftover
    payload person made the XSS check pass on stale data.
    """
    token = admin_login()
    email = f"probe-{secrets.token_hex(4)}@example.com"
    password = f"Probe-{secrets.token_hex(8)}!"  # > 12 chars (API minimum)
    body = json.dumps({
        "email": email, "password": password,
        "role": "SECURITY_OPERATOR", "full_name": "UI Probe",
    }).encode()
    r = urllib.request.Request(
        f"{BASE}/api/users", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"})
    urllib.request.urlopen(r)
    return email, password


EMAIL, PASSWORD = provision_probe_user()
OUT = Path("ui_audit/probes")
OUT.mkdir(parents=True, exist_ok=True)

res: dict = {"probe_user": EMAIL}


def login(page) -> None:
    page.goto(BASE, wait_until="domcontentloaded")
    page.evaluate("() => { localStorage.clear(); sessionStorage.clear(); }")
    page.reload(wait_until="networkidle")
    page.wait_for_selector("#email:visible", timeout=10000)
    page.fill("#email", EMAIL)
    page.fill("#password", PASSWORD)
    page.click("#login-form button[type=submit]")
    page.wait_for_selector("#app:not(.hidden)", timeout=8000)
    page.wait_for_timeout(500)


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    page = ctx.new_page()

    # ── 1. session survives reload (C-6 core promise) ─────────────────
    login(page)
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(1200)
    res["session_survives_reload"] = page.locator("#app").is_visible()
    res["session_key_in_localstorage"] = page.evaluate(
        "() => Object.keys(localStorage).length")  # must be 0
    res["refresh_in_sessionstorage"] = page.evaluate(
        "() => sessionStorage.getItem('lv_session') !== null")
    page.screenshot(path=str(OUT / "wave0-reload-restore.png"))

    # ── 2. dead session → login, not blank ────────────────────────────
    page.evaluate("() => sessionStorage.removeItem('lv_session')")
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(1000)
    res["dead_session_shows_login"] = page.locator("#login").is_visible()
    page.screenshot(path=str(OUT / "wave0-dead-session.png"))

    # ── 3. API failure → error state with retry (C-12) ────────────────
    login(page)
    page.route("**/api/cameras", lambda route: route.abort())
    page.click('nav button[data-view="cameras"]')
    page.wait_for_timeout(1200)
    state_txt = page.locator("#cameras-list").inner_text()
    res["api_error_shows_state"] = "Couldn't load" in state_txt
    res["api_error_has_retry"] = page.locator(
        "#cameras-list button", has_text="Retry").count() > 0
    res["api_error_text"] = state_txt[:160]
    page.screenshot(path=str(OUT / "wave0-api-error.png"))
    page.unroute("**/api/cameras")

    # ── 4. XSS neutralized (C-2): payload renders as inert text ───────
    page.click('nav button[data-view="people"]')
    page.wait_for_timeout(600)
    page.fill("#person-label", "xss-probe-img")
    page.fill("#person-name", "<img src=x onerror=alert(1)> <b>bold</b>")
    page.click("#person-form button[type=submit]")
    page.wait_for_timeout(1000)
    res["xss_img_injected"] = page.locator("#people-list img").count()   # 0 = safe
    res["xss_bold_rendered"] = page.locator("#people-list b").count()    # 0 = safe
    res["xss_shown_as_text"] = page.locator(
        "#people-list td", has_text="onerror").count() > 0
    page.screenshot(path=str(OUT / "wave0-xss-neutralized.png"))

    # ── 5. double-submit guard (C-13) ─────────────────────────────────
    page.fill("#person-label", "dbl-submit-test")
    page.fill("#person-name", "Double")
    btn = page.locator("#person-form button[type=submit]")
    page.click("#person-form button[type=submit]")
    res["submit_disabled_immediately_after_click"] = btn.is_disabled()
    page.wait_for_timeout(800)
    res["submit_reenabled_after_completion"] = not btn.is_disabled()

    # ── 6. autocomplete attrs preserved (regression guard) ────────────
    page.click("#logout")
    page.wait_for_timeout(400)
    res["login_autocomplete_attrs"] = page.evaluate(
        """() => ({
            email: document.querySelector('#email')?.autocomplete,
            pw: document.querySelector('#password')?.autocomplete,
            mfa: document.querySelector('#mfa')?.autocomplete,
        })"""
    )

    # ── 7. wrong-password copy (C-7) — on the THROWAWAY user ──────────
    page.fill("#email", EMAIL)
    page.fill("#password", "definitely-wrong-password")
    page.click("#login-form button[type=submit]")
    page.wait_for_timeout(1000)
    res["login_wrong_pw_copy"] = page.locator("#login-error").inner_text()[:80]
    page.screenshot(path=str(OUT / "wave0-login-copy.png"))

    # ── 8. endpoints wired (exposure floor) ────────────────────────────
    endpoints = set()
    for f in Path("ui").rglob("*.js"):
        endpoints.update(re.findall(r"/api/[a-z_/]+", f.read_text()))
    res["ui_endpoints_wired"] = sorted(endpoints)

    ctx.close()
    browser.close()

(OUT / "wave0_probe_results.json").write_text(json.dumps(res, indent=2))
print(json.dumps(res, indent=2))
