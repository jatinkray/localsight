"""Second-pass probe: design metrics + targeted interaction probes.

Extracts quantifiable UX evidence:
- computed colors / font sizes / spacing scale
- hit-target sizes (WCAG 2.5.8 / Apple 44px)
- contrast ratios (WCAG 1.4.3)
- touch-target gaps, focus visibility
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8777"
EMAIL = "admin@localvision.local"
PASSWORD = sys.argv[1] if len(sys.argv) > 1 else "CHANGE_ME_STRONG_PASSWORD"
OUT = Path("ui_audit/metrics")
OUT.mkdir(parents=True, exist_ok=True)

JS = """
() => {
  const lum = (r,g,b) => {
    const f = (c) => { c /= 255; return c <= 0.03928 ? c/12.92 : Math.pow((c+0.055)/1.055, 2.4); };
    return 0.2126*f(r)+0.7152*f(g)+0.0722*f(b);
  };
  const parse = (s) => {
    const m = s.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)(?:,\\s*([\\d.]+))?\\)/);
    return m ? {r:+m[1],g:+m[2],b:+m[3],a:m[4]===undefined?1:+m[4]} : null;
  };
  const contrast = (a, b) => {
    const L1 = lum(a.r,a.g,a.b), L2 = lum(b.g!==undefined?b.g:b.g,b.g,b.g); // placeholder
    return L1;
  };
  const px = (el, prop) => {
    const v = getComputedStyle(el)[prop];
    const m = v.match(/(-?\\d+(?:\\.\\d+)?)px/);
    return m ? +m[1] : null;
  };
  const bg = (el) => {
    // walk up for opaque bg
    let e = el;
    while (e && e !== document.documentElement) {
      const p = parse(getComputedStyle(e).backgroundColor);
      if (p && p.a === 1) return p;
      e = e.parentElement;
    }
    return {r:13,g:17,b:23,a:1}; // --bg fallback #0d1117
  };
  const targets = [...document.querySelectorAll('button, a, input, select')];
  const out = {targets: [], font_sizes: {}, radii: {}, spacing: {}};
  targets.forEach((el) => {
    const r = el.getBoundingClientRect();
    const fg = parse(getComputedStyle(el).color);
    const b = bg(el);
    let ratio = null;
    if (fg) {
      const L1 = lum(fg.r,fg.g,fg.b), L2 = lum(b.r,b.g,b.b);
      ratio = (Math.max(L1,L2)+0.05)/(Math.min(L1,L2)+0.05);
    }
    const cs = getComputedStyle(el);
      const focus = cs.outlineStyle !== 'none' && cs.outlineWidth !== '0px';
    out.targets.push({
      sel: (el.id ? '#'+el.id : el.tagName.toLowerCase())
        + (el.dataset.view ? '['+el.dataset.view+']' : ''),
      text: (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0,24),
      w: Math.round(r.width), h: Math.round(r.height),
      contrast: ratio ? +ratio.toFixed(2) : null,
      focus_visible: focus,
    });
  });
  // typography scale census
  [...document.querySelectorAll('h1,h2,h3,.big,td,th,label,button,body')].forEach(el => {
    const s = getComputedStyle(el).fontSize;
    const cls = (el.className && typeof el.className === 'string')
        ? '.'+el.className.split(' ')[0] : '';
    out.font_sizes[el.tagName.toLowerCase() + cls] = s;
  });
  return out;
}
"""


def main() -> None:
    res: dict = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for label, vp in [("desktop", {"width": 1280, "height": 800}),
                          ("mobile", {"width": 390, "height": 844})]:
            ctx = browser.new_context(viewport=vp)
            page = ctx.new_page()
            page.goto(BASE, wait_until="networkidle")
            page.fill("#email", EMAIL)
            page.fill("#password", PASSWORD)
            page.click("#login-form button[type=submit]")
            page.wait_for_selector("#app:not(.hidden)", timeout=8000)
            page.wait_for_timeout(500)
            page.screenshot(path=str(OUT / f"{label}-dashboard.png"))
            res[label] = page.evaluate(JS)
            if label == "mobile":
                # probe the mobile nav toggle behavior
                page.screenshot(path=str(OUT / "mobile-nav-closed.png"))
                nt = page.locator("#nav-toggle")
                res["mobile_nav_toggle_visible"] = nt.is_visible()
                res["mobile_nav_toggle_size"] = nt.bounding_box()
                try:
                    nt.click()
                    page.wait_for_timeout(400)
                    page.screenshot(path=str(OUT / "mobile-nav-open.png"))
                    res["mobile_menu_after_click"] = True
                    nb = page.locator('nav button[data-view="cameras"]')
                    res["mobile_nav_button_visible_after_click"] = nb.is_visible()
                except Exception as e:
                    res["mobile_menu_after_click"] = f"FAIL: {e}"[:200]
                # events table on mobile: horizontal scroll test
                if res.get("mobile_nav_button_visible_after_click"):
                    page.click('nav button[data-view="events"]')
                page.wait_for_timeout(800)
                page.screenshot(path=str(OUT / "mobile-events.png"))
                sc = page.evaluate("""() => {
                    const t = document.querySelector('.table-scroll');
                    if (!t) return null;
                    return {scrollW: t.scrollWidth, clientW: t.clientWidth,
                            needs_scroll: t.scrollWidth > t.clientWidth};
                }""")
                res["mobile_events_table_overflow"] = sc
            ctx.close()
        browser.close()
    (OUT / "design_metrics.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2)[:2500])


if __name__ == "__main__":
    main()
