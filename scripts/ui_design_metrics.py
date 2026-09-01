"""Second-pass probe: design metrics + targeted interaction probes.

Extracts quantifiable UX evidence across every view:
- hit-target sizes (WCAG 2.5.8 / 44px enterprise standard)
- contrast ratios (WCAG 1.4.3)
- focus rings — measured CORRECTLY: via keyboard Tab (:focus-visible is
  keyboard-triggered per spec; programmatic focus() does not qualify)
- SVG timeline geometry proof (C-1)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8779"
EMAIL = "auditor@localvision.local"
PASSWORD = sys.argv[1] if len(sys.argv) > 1 else "Audit-Passw0rd!2026"
OUT = Path("ui_audit/metrics")
OUT.mkdir(parents=True, exist_ok=True)

VIEWS = ["dashboard", "cameras", "events", "timeline", "people", "audit"]

JS_TARGETS = """
() => {
  const lum = (r,g,b) => {
    const f = (c) => { c /= 255; return c <= 0.03928 ? c/12.92 : Math.pow((c+0.055)/1.055, 2.4); };
    return 0.2126*f(r)+0.7152*f(g)+0.0722*f(b);
  };
  const parse = (s) => {
    const m = s.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)(?:,\\s*([\\d.]+))?\\)/);
    return m ? {r:+m[1],g:+m[2],b:+m[3],a:m[4]===undefined?1:+m[4]} : null;
  };
  const bg = (el) => {
    let e = el;
    while (e && e !== document.documentElement) {
      const p = parse(getComputedStyle(e).backgroundColor);
      if (p && p.a === 1) return p;
      e = e.parentElement;
    }
    return {r:13,g:17,b:23,a:1};
  };
  const targets = [...document.querySelectorAll('button, a, input, select, [role="button"]')]
    .filter(el => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    });
  return targets.map((el) => {
    const r = el.getBoundingClientRect();
    const fg = parse(getComputedStyle(el).color);
    const b = bg(el);
    let ratio = null;
    if (fg) {
      const L1 = lum(fg.r,fg.g,fg.b), L2 = lum(b.r,b.g,b.b);
      ratio = +((Math.max(L1,L2)+0.05)/(Math.min(L1,L2)+0.05)).toFixed(2);
    }
    return {
      sel: (el.id ? '#'+el.id : el.tagName.toLowerCase())
        + (el.dataset && el.dataset.view ? '['+el.dataset.view+']' : ''),
      text: (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0,24),
      w: Math.round(r.width), h: Math.round(r.height),
      contrast: ratio,
    };
  });
}
"""


def main() -> None:
    res: dict = {"base": BASE}
    with sync_playwright() as p:
        browser = p.chromium.launch()

        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        page = ctx.new_page()
        page.goto(BASE, wait_until="domcontentloaded")
        page.evaluate("() => localStorage.clear()")
        page.reload(wait_until="networkidle")
        page.fill("#email", EMAIL)
        page.fill("#password", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_selector("#app:not(.hidden)", timeout=8000)
        page.wait_for_timeout(600)

        all_targets = []
        for view in VIEWS:
            page.click(f'nav button[data-view="{view}"]')
            page.wait_for_timeout(700)
            tg = page.evaluate(JS_TARGETS)
            for t in tg:
                t["view"] = view
            all_targets.extend(tg)
            page.screenshot(path=str(OUT / f"wave0-{view}.png"))

        res["desktop_targets"] = all_targets

        # focus-ring audit via KEYBOARD (spec-correct :focus-visible probe)
        page.click('nav button[data-view="events"]')
        page.wait_for_timeout(500)
        rings = []
        page.evaluate("() => document.activeElement && document.activeElement.blur()")
        for _ in range(14):
            page.keyboard.press("Tab")
            page.wait_for_timeout(40)
            info = page.evaluate("""() => {
                const el = document.activeElement;
                if (!el || el === document.body) return null;
                const cs = getComputedStyle(el);
                return {
                    id: el.id || el.tagName.toLowerCase()
                        + (el.dataset && el.dataset.view ? '['+el.dataset.view+']' : ''),
                    outlineStyle: cs.outlineStyle,
                    outlineWidth: cs.outlineWidth,
                };
            }""")
            if info:
                rings.append(info)
        res["keyboard_focus_rings"] = rings

        # timeline segment proof (C-1): geometry via SVG attributes
        page.click('nav button[data-view="timeline"]')
        page.wait_for_timeout(900)
        res["timeline_svg"] = page.evaluate("""() => {
            const segs = [...document.querySelectorAll('.tl-seg')];
            return {
                svg_count: document.querySelectorAll('.tl-svg').length,
                segment_count: segs.length,
                attr_widths: segs.map(s => s.getAttribute('width')),
                visible_widths_px: segs.map(s => Math.round(s.getBoundingClientRect().width)),
            };
        }""")

        ctx.close()

        ctx = browser.new_context(
            viewport={"width": 390, "height": 844},
            is_mobile=True, has_touch=True,
            user_agent=("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                        "Version/17.0 Mobile/15E148 Safari/604.1"),
        )
        page = ctx.new_page()
        page.goto(BASE, wait_until="domcontentloaded")
        page.evaluate("() => localStorage.clear()")
        page.reload(wait_until="networkidle")
        page.fill("#email", EMAIL)
        page.fill("#password", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_selector("#app:not(.hidden)", timeout=8000)
        page.wait_for_timeout(600)
        page.click("#nav-toggle")
        page.wait_for_timeout(300)
        res["mobile_targets"] = page.evaluate(JS_TARGETS)
        res["mobile_nav"] = {
            "toggle_visible": page.locator("#nav-toggle").is_visible(),
            "buttons_visible_after_click": page.locator("nav button").first.is_visible(),
        }
        ctx.close()
        browser.close()

    (OUT / "wave0_design_metrics.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2)[:1500])


if __name__ == "__main__":
    main()
