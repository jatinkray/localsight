// Opt-in UI telemetry — Wave 5 (plan §III.11 "telemetry (opt-in)").
//
// Product rule first: NOTHING leaves the host. This is a local, in-page
// ring buffer of interaction marks (view loads, slow views, page errors)
// that helps an OPERATOR diagnose their own console; the data lives in
// memory, never in storage, never on the network. Enabling it is per-
// browser (localStorage flag, default OFF) and export is a one-click
// JSON download — the user owns the file.
//
// What's recorded per mark: {ts, kind, detail} — no URLs, no user
// content, no event data. The ring holds the last 500 marks.

const KEY = "lv-telemetry-opt-in";
const RING_MAX = 500;

const ring = [];
let enabled = readFlag();

function readFlag() {
  try {
    return localStorage.getItem(KEY) === "on";
  } catch {
    return false;
  }
}

export function isTelemetryOn() {
  return enabled;
}

export function setTelemetry(on) {
  enabled = Boolean(on);
  try {
    localStorage.setItem(KEY, enabled ? "on" : "off");
  } catch { /* storage unavailable — the flag lives in memory for this tab */ }
  mark("telemetry", enabled ? "enabled" : "disabled");
}

/** Record a mark if enabled. kind is a stable string; detail is short. */
export function mark(kind, detail = "") {
  if (!enabled) return;
  ring.push({ ts: new Date().toISOString(), kind, detail: String(detail).slice(0, 120) });
  if (ring.length > RING_MAX) ring.shift();
}

/** Wire global error + slow-view collection. Call once at app boot. */
export function wireTelemetry() {
  window.addEventListener("error", (e) => {
    mark("js-error", `${e.message || e.type}`);
  });
  window.addEventListener("unhandledrejection", (e) => {
    mark("js-rejection", String(e.reason).slice(0, 120));
  });
  // slow-view marks: patch nothing — views call markView() themselves via
  // app.js's onView instrumentation (below).
}

/** A view visit mark. app.js wires this per navigation; the number is the
 *  hashchange-to-microtask dispatch lag (a proxy for listener work, not a
 *  substitute for the perf suite's measured view latency). */
export function markView(view, ms = 0) {
  const note = ms > 2500 ? " (slow dispatch)" : "";
  mark("view", `${view}:${Math.round(ms)}ms${note}`);
}

/** Export what's collected as a downloadable JSON — the user keeps it. */
export function exportTelemetry() {
  const blob = new Blob([JSON.stringify(ring, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `localsight-ui-marks-${new Date().toISOString().slice(0, 10)}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

/** Everything collected so far (for the Privacy card + tests). */
export function telemetrySnapshot() {
  return { enabled, count: ring.length, marks: ring.slice() };
}
