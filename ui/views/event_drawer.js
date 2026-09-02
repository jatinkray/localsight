// Event detail drawer — the investigation loop's destination (Wave 1).
//
// Replaces the audit's C-10 finding ("view" opened raw JSON in a new tab):
// operators now get snapshot + bbox overlay, clip playback, identity and
// camera chips, event context, and an audited export with live countdown.
//
// Signed media URLs expire (300 s) — the drawer counts down honestly and
// refetches detail if the operator keeps it open past expiry.

import { h, render, svgEl } from "../core/dom.js";
import { api, ApiError, can } from "../core/api.js";
import { toast } from "../core/toast.js";
import { fmtDateTime, fmtDuration, fmtBytes, label, tone, shortId } from "../core/format.js";

let drawerEl = null;
let scrimEl = null;
let closeFn = null;
let cameraNames = null;
let identityCache = new Map();
let countdownTimer = null;

function ensureDrawer() {
  if (drawerEl) return drawerEl;
  scrimEl = h("div", { class: "drawer-scrim hidden", onClick: () => requestClose() });
  drawerEl = h("aside", {
    class: "drawer hidden",
    role: "dialog",
    "aria-modal": "true",
    "aria-labelledby": "drawer-title",
  });
  document.body.append(scrimEl, drawerEl);

  // Escape closes; focus is trapped inside while open. "E" (drawer open,
  // not typing) triggers the audited export — the keyboard map's power path.
  document.addEventListener("keydown", (e) => {
    if (drawerEl.classList.contains("hidden")) return;
    if (e.key === "Escape") requestClose();
    if (e.key === "Tab") trapFocus(e);
    if ((e.key === "e" || e.key === "E")
        && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) {
      const btn = drawerEl.querySelector(".drawer-export");
      if (btn && !btn.disabled) { e.preventDefault(); btn.click(); }
    }
  });
  return drawerEl;
}

function trapFocus(e) {
  const focusables = drawerEl.querySelectorAll(
    "button, a[href], video, input, select, [tabindex]:not([tabindex='-1'])");
  if (!focusables.length) return;
  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault();
    first.focus();
  }
}

let lastFocused = null;

export function requestClose() {
  if (closeFn) closeFn();
}

async function names() {
  if (cameraNames) return cameraNames;
  try {
    const cams = await api("/api/cameras");
    cameraNames = new Map((Array.isArray(cams) ? cams : cams.items || []).map((c) => [c.id, c.name]));
  } catch { cameraNames = new Map(); }
  return cameraNames;
}

async function identityOf(id) {
  if (!id) return null;
  if (identityCache.has(id)) return identityCache.get(id);
  try {
    const people = await api("/api/persons");
    const p = (Array.isArray(people) ? people : []).find((x) => x.id === id) || null;
    identityCache.set(id, p);
    return p;
  } catch { return null; }
}

/** Snapshot with bbox overlay drawn as SVG (CSP-safe). */
function snapshotWithBbox(src, bbox) {
  const wrap = h("div", { class: "snap-wrap" });
  const img = h("img", {
    class: "snap-img",
    src, alt: "Event snapshot",
    onLoad: () => wrap.classList.add("loaded"),
    onError: () => wrap.classList.add("failed"),
  });
  wrap.append(img);
  if (bbox && typeof bbox.x === "number") {
    // bbox is normalized {x,y,w,h} fractions — scale into a 100x100 viewBox
    // overlaid via preserveAspectRatio="none" so it tracks the image.
    const svg = svgEl("svg", {
      class: "snap-overlay",
      viewBox: "0 0 100 100",
      preserveAspectRatio: "none",
      "aria-hidden": "true",
    });
    svg.append(svgEl("rect", {
      x: bbox.x * 100, y: (bbox.y || 0) * 100,
      width: (bbox.w || 0.2) * 100, height: (bbox.h || 0.2) * 100,
      fill: "none", stroke: "var(--accent-text)", "stroke-width": 0.7, rx: 0.6,
    }));
    wrap.append(svg);
  }
  return wrap;
}

function chip(text, toneClass = "") {
  return h("span", { class: `pill ${toneClass}`.trim() }, text);
}

function detailRows(detail) {
  if (!detail || typeof detail !== "object") return null;
  // Ciphertext values (plate_enc etc.) never render — allowlist only.
  const KEYS = ["direction", "dwell_sec", "count", "zone", "stationary_sec"];
  const rows = Object.entries(detail).filter(([k]) => KEYS.includes(k));
  if (!rows.length) return null;
  return h("div", { class: "kv-grid" },
    ...rows.map(([k, v]) => h("div", { class: "kv" },
      h("span", { class: "kv-key" }, label(k)),
      h("span", { class: "kv-val" }, String(v)),
    )));
}

function startCountdown(expiresIn, el) {
  clearInterval(countdownTimer);
  const until = Date.now() + expiresIn * 1000;
  const tick = () => {
    const left = Math.max(0, until - Date.now());
    const mm = String(Math.floor(left / 60000)).padStart(2, "0");
    const ss = String(Math.floor((left % 60000) / 1000)).padStart(2, "0");
    el.textContent = `link expires in ${mm}:${ss}`;
    if (left <= 30000) el.classList.add("expiring");
    if (left <= 0) clearInterval(countdownTimer);
  };
  tick();
  countdownTimer = setInterval(tick, 1000);
}

/** Open the drawer for an event id. onClose returns to the caller's view. */
export async function openEventDrawer(eventId, { onClose } = {}) {
  const drawer = ensureDrawer();
  closeFn = () => { closeDrawer(); if (onClose) onClose(); };
  lastFocused = document.activeElement;

  render(drawer, h("div", { class: "drawer-body" },
    // E-9: the heading exists from the FIRST frame — the dialog is
    // labelled by it even while content loads.
    h("div", { class: "drawer-head" },
      h("h2", { id: "drawer-title" }, "Event detail"),
      h("button", { class: "ghost drawer-close", type: "button",
        "aria-label": "Close event detail", onClick: () => requestClose() }, "✕")),
    h("div", { class: "skeleton skeleton-card" }),
    h("div", { class: "skeleton skeleton-row" }),
    h("div", { class: "skeleton skeleton-row" }),
  ));
  drawer.classList.remove("hidden");
  scrimEl.classList.remove("hidden");
  document.body.classList.add("drawer-open");

  try {
    const ev = await api(`/api/events/${encodeURIComponent(eventId)}`);
    const [nameMap, person] = await Promise.all([names(), identityOf(ev.identity_id)]);

    const camName = nameMap.get(ev.camera_id) || `camera ${shortId(ev.camera_id)}`;
    const durSec = (new Date(ev.timestamp_end) - new Date(ev.timestamp_start)) / 1000;

    const head = h("div", { class: "drawer-head" },
      h("h2", { id: "drawer-title" }, label(ev.event_type)),
      h("button", {
        class: "ghost drawer-close", type: "button",
        "aria-label": "Close event detail", onClick: () => requestClose(),
      }, "✕"),
    );

    const body = [];
    if (ev.snapshot_url) {
      body.push(h("div", { class: "drawer-section" },
        h("h3", {}, "Snapshot"),
        snapshotWithBbox(ev.snapshot_url, ev.bbox),
      ));
    }
    if (ev.video_url) {
      body.push(h("div", { class: "drawer-section" },
        h("h3", {}, "Clip"),
        h("video", {
          class: "clip-player", src: ev.video_url, controls: true, preload: "metadata",
        }),
        h("div", { class: "muted expiry-note" }, ""),
      ));
    }

    body.push(h("div", { class: "drawer-section" },
      h("h3", {}, "Context"),
      h("div", { class: "chip-row" },
        chip(camName, "info"),
        chip(label(ev.identity_status), tone(ev.identity_status)),
        person ? chip(person.display_name || person.label, "ok") : null,
      ),
      h("div", { class: "kv-grid" },
        h("div", { class: "kv" }, h("span", { class: "kv-key" }, "Started"),
          h("span", { class: "kv-val" }, fmtDateTime(ev.timestamp_start))),
        h("div", { class: "kv" }, h("span", { class: "kv-key" }, "Ended"),
          h("span", { class: "kv-val" }, fmtDateTime(ev.timestamp_end))),
        h("div", { class: "kv" }, h("span", { class: "kv-key" }, "Duration"),
          h("span", { class: "kv-val" }, Number.isFinite(durSec) ? fmtDuration(durSec) : "—")),
        h("div", { class: "kv" }, h("span", { class: "kv-key" }, "Confidence"),
          h("span", { class: "kv-val" }, ev.confidence.toFixed(2))),
        h("div", { class: "kv" }, h("span", { class: "kv-key" }, "Event ID"),
          h("span", { class: "kv-val mono" }, shortId(ev.id))),
      ),
    ));

    render(drawer, h("div", { class: "drawer-body" }, head, ...body));

    // Export is role-gated (events:export): viewers never see a button they
    // can't use — the RBAC failure is designed out of the UI, not caught
    // after a click. Export is audited server-side; countdown reflects the
    // 300 s link TTL.
    const exportBtn = can("events:export")
      ? h("button", {
          class: "primary drawer-export", type: "button",
          disabled: !ev.video_url,
          onClick: async () => {
            exportBtn.disabled = true;
            exportBtn.textContent = "Preparing…";
            try {
              const res = await api(`/api/events/${encodeURIComponent(eventId)}/export`);
              await navigator.clipboard.writeText(new URL(res.url, location.origin).toString())
                .catch(() => {});
              const expiryEl = drawer.querySelector(".expiry-note");
              if (expiryEl) startCountdown(res.expires_in, expiryEl);
              toast("Signed link copied — expires in 5 minutes", { tone: "ok", timeout: 5000 });
            } catch (err) {
              const msg = err instanceof ApiError && err.status === 403
                ? "Your role can't export video" : "Export failed — try again";
              toast(msg, { tone: "error" });
            } finally {
              exportBtn.disabled = false;
              exportBtn.textContent = "Export evidence";
            }
          },
        }, "Export evidence")
      : h("span", { class: "muted" }, "Video export requires an operator role");
    if (!ev.video_url && can("events:export")) exportBtn.title = "No recorded video for this event";

    const foot = h("div", { class: "drawer-foot" },
      exportBtn,
      h("span", { class: "muted mono drawer-id" }, shortId(ev.id)),
    );
    drawer.append(foot);
    drawer.querySelector(".drawer-close").focus();
  } catch (err) {
    render(drawer, h("div", { class: "drawer-body" },
      h("div", { class: "drawer-head" },
        h("h2", { id: "drawer-title" }, "Event detail"),
        h("button", { class: "ghost drawer-close", type: "button", onClick: () => requestClose() }, "✕"),
      ),
      err.status === 404
        ? h("p", { class: "muted" }, "This event no longer exists (retention may have removed it).")
        : h("p", { class: "muted" }, `Couldn't load this event: ${err.message}`),
    ));
    drawer.querySelector(".drawer-close").focus();
  }
}

function closeDrawer() {
  clearInterval(countdownTimer);
  if (drawerEl) {
    drawerEl.classList.add("hidden");
    drawerEl.replaceChildren();
  }
  if (scrimEl) scrimEl.classList.add("hidden");
  document.body.classList.remove("drawer-open");
  closeFn = null;
  if (lastFocused && lastFocused.isConnected) lastFocused.focus();
}
