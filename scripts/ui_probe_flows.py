"""Third-pass probes: token lifecycle, dead-end flows, and dead links.

Evidence-gathering for the UX report:
1. 15-min token expiry → what does the user see? (no refresh logic in app.js)
2. Event 'view' link → raw JSON new tab (already screenshotted); check export/clip
   affordances absent in UI.
3. Empty-state and error-state handling: API 500 vs 401 rendering.
4. Double-submit protection on person enroll.
"""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8777"
EMAIL = "auditor@localvision.local"
# Dev-only audit script: this throwaway credential exists only in the seeded
# dev database (see scripts/seed_dev_data.py) and is never a real secret.
PASSWORD = "Audit-Passw0rd!2026"  # noqa: S105
OUT = Path("ui_audit/probes")
OUT.mkdir(parents=True, exist_ok=True)

res: dict = {}


def login(page) -> None:
    # Deterministic: clear token so the app always boots to the login screen.
    page.goto(BASE, wait_until="domcontentloaded")
    page.evaluate("() => localStorage.removeItem('lv_token')")
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

    # ── 1. token expiry simulation ─────────────────────────────────────
    login(page)
    # corrupt the token to simulate expiry (client has no refresh flow)
    page.evaluate("() => localStorage.setItem('lv_token', 'expired.jwt.here')")
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(600)
    # The app calls enterApp() if token exists -> API calls 401 -> logout() called
    # What does the user see?
    res["after_expired_token_url"] = page.url
    res["login_visible_after_expiry"] = page.locator("#login").is_visible()
    res["app_visible_after_expiry"] = page.locator("#app").is_visible()
    page.screenshot(path=str(OUT / "expired-token.png"))

    # What if API dies mid-session (worker down / network cut)?
    login(page)
    page.route("**/api/cameras", lambda route: route.abort())
    page.click('nav button[data-view="cameras"]')
    page.wait_for_timeout(900)
    res["cameras_view_on_api_error"] = page.locator("#cameras-list").inner_text()[:200]
    res["console_after_api_error"] = "check findings"
    page.screenshot(path=str(OUT / "api-error-state.png"))

    # ── 2. double submit / button state during flight ──────────────────
    login(page)
    page.click('nav button[data-view="people"]')
    page.fill("#person-label", "dbl-submit-test")
    page.fill("#person-name", "Double")
    btn = page.locator("#person-form button[type=submit]")
    res["submit_disabled_during_flight"] = btn.is_disabled()  # expect False — no protection
    page.click("#person-form button[type=submit]")
    res["submit_disabled_after_click"] = btn.is_disabled()
    page.wait_for_timeout(800)

    # ── 3. XSS surface check (report M-30 already flagged innerHTML) ────
    # Enroll a person with HTML payload and see how the list renders it.
    page.fill("#person-label", "<img src=x onerror=alert(1)>")
    page.fill("#person-name", "<b>Bold</b>Injection")
    page.click("#person-form button[type=submit]")
    page.wait_for_timeout(900)
    injected = page.locator("#people-list img")
    res["xss_img_injected"] = injected.count()  # >0 = innerHTML XSS fires
    bold = page.locator("#people-list b")
    res["xss_bold_rendered"] = bold.count()
    page.screenshot(path=str(OUT / "xss-probe.png"))

    # ── 4. login page: password manager support / autocomplete  ────────
    page.click("#logout")
    page.wait_for_timeout(400)
    res["login_autocomplete_attrs"] = page.evaluate(
        """() => ({
            email: document.querySelector('#email')?.autocomplete,
            pw: document.querySelector('#password')?.autocomplete,
            mfa: document.querySelector('#mfa')?.autocomplete,
        })"""
    )

    # ── 5. does any view use live view / rules / alerts / analytics? ────
    res["ui_calls_surface"] = page.evaluate(
        """() => {
            const s = document.documentElement.outerHTML;
            return {
                mentions_live: s.includes('live'), mentions_rules: s.includes('rules'),
                mentions_alerts: s.includes('alerts'), mentions_analytics: s.includes('analytics'),
            };
        }"""
    )
    # fetch surface actually wired:
    res["appjs_endpoints"] = []
    import re
    src = Path("ui/app.js").read_text()
    res["appjs_endpoints"] = sorted(set(re.findall(r"/api/[a-z/]+", src)))

    ctx.close()
    browser.close()

(OUT / "probe_results.json").write_text(json.dumps(res, indent=2))
print(json.dumps(res, indent=2))
