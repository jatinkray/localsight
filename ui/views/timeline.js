// SVG forensic timeline (fixes C-1).
//
// The old view injected inline style="left:x%;width:y%" — blocked by the
// app's own hardened CSP (style-src 'self'), rendering every segment 0px.
// SVG geometry uses x/width ATTRIBUTES, which CSP does not restrict: the
// correct CSP-native medium for data-driven geometry.
//
// Structure per camera row: one <svg> with viewBox="0 0 1440 H" where 1
// unit = 1 minute. Segments positioned by minute. Hour ticks every 60.
// Hover scrub line + live time readout; click jumps to Events filtered
// to that camera (Wave 1 investigation loop).
//
// Wave 1: hover readout is CSP-safe (no inline styles): the scrub line is
// an SVG attribute-driven element; the readout is a DOM node whose
// textContent updates.

import { h, svgEl, render } from "../core/dom.js";
import { shortId, label, tone } from "../core/format.js";
import { api } from "../core/api.js";
import { skeletonRows, emptyState, errorState } from "../core/states.js";
import { navigate } from "../core/router.js";

const HOUR_UNITS = 60;
const DAY_UNITS = 1440;
const BAR_H = 22;
const SVG_H = 30; // bar + padding for hour ticks

/** Minutes-from-midnight for an ISO string, clamped to [0, 1440]. */
function minutesOf(iso, clamp) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const m = d.getHours() * 60 + d.getMinutes();
  if (clamp === "start" && m >= DAY_UNITS - 1) return DAY_UNITS - 1;
  if (clamp === "end" && m <= 0) return 1;
  return Math.min(Math.max(m, 0), DAY_UNITS);
}

function hhmm(mins) {
  return `${String(Math.floor(mins / 60)).padStart(2, "0")}:${String(mins % 60).padStart(2, "0")}`;
}

function buildRow(t, cameraNames) {
  const name = cameraNames.get(t.camera_id) || `camera ${shortId(t.camera_id)}`;

  const svg = svgEl("svg", {
    class: "tl-svg tl-interactive",
    viewBox: `0 0 ${DAY_UNITS} ${SVG_H}`,
    preserveAspectRatio: "none",
    "aria-label": `${name} activity for the day`,
    role: "img",
  });

  // track background
  svg.append(svgEl("rect", {
    class: "tl-track", x: 0, y: 8, width: DAY_UNITS, height: BAR_H, rx: 3,
  }));

  // hour gridlines every 2h, labels every 4h
  const ticks = svgEl("g", { class: "tl-hours" });
  for (let hm = 0; hm <= DAY_UNITS; hm += HOUR_UNITS * 2) {
    ticks.append(svgEl("line", { x1: hm, y1: 8, x2: hm, y2: 8 + BAR_H }));
    if (hm % (HOUR_UNITS * 4) === 0) {
      const t = svgEl("text", { x: Math.min(hm, DAY_UNITS - 30), y: 5 });
      t.textContent = String(hm / HOUR_UNITS).padStart(2, "0");
      ticks.append(t);
    }
  }
  svg.append(ticks);

  // scrub line + readout (hidden until hover)
  const scrub = svgEl("line", { class: "tl-scrub hidden", x1: 0, x2: 0, y1: 4, y2: 8 + BAR_H });
  svg.append(scrub);
  const readout = h("span", { class: "tl-readout muted mono", "aria-hidden": "true" }, "—");

  // segments: x/width as ATTRIBUTES — CSP-safe (the fix).
  // Minimum width 3 units (3 min on screen) so short events stay visible
  // and hoverable; clipped to the day edge.
  for (const iv of t.intervals || []) {
    const s = minutesOf(iv.start, "start");
    const e = minutesOf(iv.end, "end");
    if (s == null || e == null || e <= s) continue;
    const width = Math.max(e - s, 3);
    const seg = svgEl("rect", {
      class: "tl-seg",
      x: s, y: 8, width: Math.min(width, DAY_UNITS - s), height: BAR_H, rx: 1.5,
    });
    const title = svgEl("title");
    title.textContent =
      `${label(iv.identity_status || "unknown")} · ${new Date(iv.start).toLocaleTimeString()} → ${new Date(iv.end).toLocaleTimeString()}`;
    seg.append(title);
    svg.append(seg);
  }

  // Hover: move the scrub line (x1/x2 attributes — CSP-safe) and show the
  // wall-clock minute under the cursor.
  svg.addEventListener("mousemove", (ev) => {
    const r = svg.getBoundingClientRect();
    const unit = Math.min(Math.max((ev.clientX - r.left) / r.width * DAY_UNITS, 0), DAY_UNITS);
    scrub.setAttribute("x1", unit);
    scrub.setAttribute("x2", unit);
    scrub.classList.remove("hidden");
    readout.textContent = hhmm(Math.floor(unit));
  });
  svg.addEventListener("mouseleave", () => {
    scrub.classList.add("hidden");
    readout.textContent = "—";
  });

  // Click: jump to Events for this camera — the natural next step in an
  // investigation ("what happened around 14:20 on the loading dock?").
  svg.addEventListener("click", () => {
    navigate("events", { camera: t.camera_id });
  });

  return h("div", { class: "tl-row" },
    h("div", { class: "tl-row-label" },
      h("span", { class: "dot", "aria-hidden": "true" }, ""),
      h("strong", {}, name),
      h("span", { class: "muted" },
        `${(t.intervals || []).length} period${(t.intervals || []).length === 1 ? "" : "s"} · ${label(t.label || "unknown")}`),
    ),
    svg,
    readout,
  );
}

let cameraNameCache = null;

async function cameraNames() {
  if (cameraNameCache) return cameraNameCache;
  try {
    const cams = await api("/api/cameras");
    cameraNameCache = new Map((Array.isArray(cams) ? cams : cams.items || []).map((c) => [c.id, c.name]));
  } catch {
    cameraNameCache = new Map();
  }
  return cameraNameCache;
}

export async function loadTimeline(outEl, { date, cameraId } = {}) {
  skeletonRows(outEl, 4);
  try {
    const names = await cameraNames();
    const qs = new URLSearchParams({ date });
    if (cameraId) qs.set("camera_id", cameraId);
    const data = await api(`/api/timeline?${qs}`);
    const rows = (data.timeline || []).map((t) => buildRow(t, names));
    render(outEl, rows.length ? rows : emptyState({
      icon: "◔", title: "No activity on this day",
      hint: "No presence was recorded for the selected date and camera.",
    }));
  } catch (err) {
    render(outEl, errorState(err, {
      noun: "timeline",
      onRetry: () => loadTimeline(outEl, { date, cameraId }),
    }));
  }
}
