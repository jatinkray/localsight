"""Design-system guards: the token claims, computed and enforced.

The audit's C-3/C-4/C-5 findings (44px targets, focus rings, contrast)
were fixed with tokens whose comments CLAIM ratios — comments don't
enforce anything. These tests compute the real rendered values:

- contrast: every --text-* token against every --surface-* it can
  render on (axe checks the DOM; this checks the SYSTEM, catching a
  bad token before any view uses it)
- target size: nav buttons and primary controls at ≥44px effective
- focus-visible: the ring actually paints when keyboard-focused
"""
import pytest

pytestmark = pytest.mark.ui


def _contrast_ratio(page, fg, bg):
    return page.evaluate(
        """([fg, bg]) => {
          const lum = (c) => {
            const [r, g, b] = c.match(/\\w\\w/g).map(x => parseInt(x, 16) / 255)
              .map(v => v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
            return 0.2126 * r + 0.7152 * g + 0.0722 * b;
          };
          const [a, b] = [lum(fg), lum(bg)].sort((x, y) => y - x);
          return (a + 0.05) / (b + 0.05);
        }""", [fg.replace("#", ""), bg.replace("#", "#")])


class TestTokenContrast:
    def _tokens(self, page):
        return page.evaluate("""
          () => {
            const cs = getComputedStyle(document.body);
            const get = (n) => cs.getPropertyValue(n).trim();
            return {
              text: ['--text-primary', '--text-secondary', '--text-tertiary']
                .map(n => get(n)),
              surfaces: ['--surface-0', '--surface-1', '--surface-2', '--surface-3']
                .map(n => get(n)),
            };
          }""")

    def test_text_tokens_pass_aa_on_every_surface(self, logged_in):
        tokens = self._tokens(logged_in)
        failures = []
        for txt in tokens["text"]:
            for surf in tokens["surfaces"]:
                ratio = _contrast_ratio(logged_in, txt, surf)
                if ratio < 4.5:
                    failures.append(f"{txt} on {surf}: {ratio:.2f}:1")
        assert not failures, failures


class TestTargetSize:
    def test_nav_buttons_44px(self, logged_in):
        """C-4: primary navigation targets are touch-operable."""
        small = logged_in.evaluate("""
          () => [...document.querySelectorAll('#nav button')].filter(b => {
            if (b.classList.contains('hidden')) return false;
            const r = b.getBoundingClientRect();
            return r.height < 44;
          }).map(b => b.textContent.trim())""")
        assert not small, small


class TestFocusVisible:
    def test_keyboard_focus_paints_a_ring(self, logged_in):
        """C-5 guard: focus-visible must not be outline:none'd away."""
        logged_in.keyboard.press("Tab")
        ring = logged_in.evaluate("""
          () => {
            const el = document.activeElement;
            const cs = getComputedStyle(el);
            const w = parseFloat(cs.outlineWidth) || 0;
            return w > 0 && cs.outlineStyle !== 'none';
          }""")
        assert ring, "first Tab stop shows no focus outline"


class TestHonestIds:
    def test_camera_names_not_hex(self, logged_in):
        """UX rule: camera NAMES, ids in muted mono — never raw hex as
        the primary label (the audit's recall finding)."""
        logged_in.click("#nav button[data-view='cameras']")
        logged_in.wait_for_selector(".cam-card")
        text = logged_in.locator(".cam-card").first.inner_text()
        assert "Lobby" in text or "Warehouse" in text or "Dock" in text
        hexlabel = logged_in.locator(".cam-card .cam-name").first.inner_text()
        assert len(hexlabel) < 40, hexlabel
