// Global keyboard map (plan §III.5b — complete operability).
//
// Gmail-style two-key navigation: press "g" then a zone key to jump.
// "/" focuses the events search (the one box operators live in), and "?"
// opens the shortcut overlay. All handlers no-op while typing in a field
// or when a drawer holds focus. Existing per-view keys are untouched:
// ↑/↓/Enter on events, arrows+f+Esc on live (wall mode), Esc on drawers.

import { h, render } from "./dom.js";
import { navigate } from "./router.js";

const GOTO = {
  o: "dashboard",  // Overview
  l: "live",       // Live
  e: "events",     // Events (investigate)
  t: "timeline",   // Timeline
  c: "cameras",    // Cameras
  a: "analytics",  // Analytics
  u: "users",      // Users
  p: "privacy",    // Privacy
  d: "audit",      // auDit log
};

let gArmed = false;
let gTimer = null;
let overlayEl = null;

function typing(e) {
  const el = document.activeElement;
  if (!el) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
}

function appVisible() {
  const app = document.getElementById("app");
  return app && !app.classList.contains("hidden");
}

export function wireShortcuts() {
  document.addEventListener("keydown", (e) => {
    if (!appVisible() || typing(e) || e.ctrlKey || e.metaKey || e.altKey) return;

    // "g" then a key: zone jump. Any non-mapped key disarms.
    if (gArmed) {
      disarm();
      const view = GOTO[e.key];
      if (view) { e.preventDefault(); navigate(view); }
      return;
    }

    if (e.key === "g") {
      gArmed = true;
      gTimer = setTimeout(disarm, 1200); // two-key sequence has a beat to land
      e.preventDefault();
      return;
    }

    if (e.key === "/") {
      // Focus the investigation search box — from anywhere.
      e.preventDefault();
      const inp = document.getElementById("ev-camera");
      if (inp) {
        navigate("events");
        // the events panel renders async; focus once it's there
        setTimeout(() => inp.focus(), 120);
      }
      return;
    }

    if (e.key === "?") {
      e.preventDefault();
      toggleOverlay();
      return;
    }

    if (e.key === "Escape" && overlayEl) {
      e.preventDefault();
      closeOverlay();
    }
  });
}

function disarm() {
  gArmed = false;
  if (gTimer) { clearTimeout(gTimer); gTimer = null; }
}

// ── the "?" overlay ───────────────────────────────────────────────────────

function toggleOverlay() {
  if (overlayEl && document.body.contains(overlayEl)) { closeOverlay(); return; }
  openOverlay();
}

function openOverlay() {
  overlayEl = h("div", {
    class: "scrim", id: "shortcut-scrim",
    onClick: () => closeOverlay(),
  },
    h("div", { class: "card shortcut-card", role: "dialog", "aria-label": "Keyboard shortcuts" },
      h("div", { class: "panel-head" },
        h("h2", {}, "Keyboard shortcuts"),
        h("button", {
          class: "ghost", "aria-label": "Close shortcuts",
          onClick: () => closeOverlay(),
        }, "✕")),
      h("div", { class: "shortcut-grid" },
        ...[
          ["g then o / l / e", "Overview · Live · Events"],
          ["g then t / c / a", "Timeline · Cameras · Analytics"],
          ["g then u / p / d", "Users · Privacy · Audit"],
          ["/", "Focus the events search"],
          ["↑ / ↓ then Enter", "Select an event, open it"],
          ["f", "Fullscreen wall mode (Live view)"],
          ["Esc", "Close drawer / overlay / wall"],
          ["?", "This overlay"],
        ].map(([k, d]) => [
          h("kbd", { class: "shortcut-key" }, k),
          h("span", { class: "shortcut-desc" }, d),
        ]).flat()),
    ));
  document.body.append(overlayEl);
  overlayEl.querySelector("button").focus();
}

function closeOverlay() {
  if (overlayEl) { overlayEl.remove(); overlayEl = null; }
}
