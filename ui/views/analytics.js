// Analytics view — the "what happened" screens over archived data.
// Wave 4: time-range + camera selector shared across widgets; every chart
// is CSP-safe inline SVG or canvas geometry (no chart library, no inline
// styles — AGENTS.md rules 3.1/3.2).

import { h, render, svgEl } from "../core/dom.js";
import { api } from "../core/api.js";
import { toast } from "../core/toast.js";
import { navigate } from "../core/router.js";
import { emptyState, errorState } from "../core/states.js";
import { fmtDateTime, fmtDuration } from "../core/format.js";

const SVG_W = 560;
const SVG_H = 160;
const PAD = 28; // left axis room

const RANGES = [
  { key: "24h", label: "Last 24 hours", days: 1 },
  { key: "7d", label: "Last 7 days", days: 7 },
  { key: "30d", label: "Last 30 days", days: 30 },
];

/** Query state for the whole view; every widget re-reads it. */
let sel = { range: "24h", cameraId: null };

function isoDaysAgo(days) {
  return new Date(Date.now() - days * 86400_000).toISOString();
}

function rangeBounds() {
  const r = RANGES.find((x) => x.key === sel.range) || RANGES[0];
  return { start: isoDaysAgo(r.days), end: new Date().toISOString(), days: r.days };
}

// ── chart builders (pure SVG attributes; every value is code, not user data)
// ──────────────────────────────────────────────────────────────────────────

/** Occupancy buckets → an SVG line chart with axis ticks. */
function lineChart(buckets) {
  if (!buckets.length) return emptyState({
    icon: "◔", title: "No activity in this range",
    hint: "Record something first — or widen the time range." });
  const max = Math.max(1, ...buckets.map((b) => b.count));
  const step = (SVG_W - PAD * 2) / Math.max(1, buckets.length - 1);
  const y = (c) => SVG_H - PAD - (c / max) * (SVG_H - PAD * 2);
  const pts = buckets.map((b, i) => [PAD + i * step, y(b.count)]);
  const path = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
  const ticks = [0, Math.ceil(max / 2), max].map((c) => svgEl("text", {
    x: PAD - 6, y: y(c) + 4, class: "axis-label", "text-anchor": "end",
  }, String(c)));
  const marks = buckets
    .filter((_, i) => i === 0 || i === buckets.length - 1
      || i === Math.floor(buckets.length / 2))
    .map((b) => svgEl("text", {
      x: PAD + buckets.indexOf(b) * step, y: SVG_H - 8,
      class: "axis-label", "text-anchor": "middle",
    }, fmtDateTime(b.ts).slice(11, 16) || b.ts.slice(0, 10)));
  return svgEl("svg", { viewBox: `0 0 ${SVG_W} ${SVG_H}`, class: "an-chart", role: "img",
    "aria-label": "People count over time" },
    svgEl("path", { d: path, class: "an-line" }),
    ...pts.map((p) => svgEl("circle", { cx: p[0].toFixed(1), cy: p[1].toFixed(1), r: 2.5, class: "an-dot" })),
    ...ticks, ...marks,
  );
}

/** Peak occupancy + when — the honest gauge: numbers the API really has. */
function peakCard(buckets) {
  if (!buckets.length) return emptyState({
    icon: "◔", title: "No occupancy data", hint: "Nothing was recorded in this range." });
  const peak = buckets.reduce((m, b) => (b.count > m.count ? b : m), buckets[0]);
  return h("div", { class: "an-kv" },
    h("div", { class: "an-peak" }, String(peak.count)),
    h("div", { class: "muted" }, "peak simultaneous"),
    h("div", { class: "mono text-xs" }, fmtDateTime(peak.ts)),
  );
}

/** Dwell: the API gives an average — render it as one honest number, no fake histogram. */
function dwellCard(avgDwell) {
  return h("div", { class: "an-kv" },
    h("div", { class: "an-peak" }, fmtDuration(avgDwell)),
    h("div", { class: "muted" }, "average dwell per track"),
    h("div", { class: "muted text-xs" }, "across all tracked people in range"),
  );
}

/** Event-type breakdown → SVG horizontal bars (width is an attribute —
 * CSP forbids inline style widths, and attribute geometry is the pattern
 * the timeline already established). */
function breakdownBars(rows) {
  if (!rows.length) return emptyState({
    icon: "▤", title: "No events in range", hint: "Nothing to break down yet." });
  const W = 480;
  const rowH = 30;
  const labelW = 120;
  const countW = 48;
  const barMax = W - labelW - countW - 8;
  const max = Math.max(1, ...rows.map((r) => r.count));
  const H = rows.length * rowH + 4;
  return svgEl("svg", { viewBox: `0 0 ${W} ${H}`, class: "an-chart an-bars-svg",
    role: "img", "aria-label": "Events by type" },
    ...rows.map((r, i) => {
      const y = i * rowH + 4;
      const w = Math.max(2, (r.count / max) * barMax);
      return svgEl("g", {},
        svgEl("text", { x: labelW - 8, y: y + 17, class: "axis-label", "text-anchor": "end" }, r.event_type),
        svgEl("rect", { x: labelW, y: y + 4, width: w, height: rowH - 8, rx: 3, class: "an-bar" }),
        svgEl("text", { x: labelW + w + 6, y: y + 17, class: "axis-label" }, String(r.count)),
      );
    }));
}

/** Heatmap grid → canvas with alpha-blended cells (CSP-safe: width/height
 * are set as properties, geometry via fillRect — never an inline style). */
function heatmapCanvas(grid) {
  const wrap = h("div", { class: "an-heat-wrap" });
  const canvas = h("canvas", { class: "an-heat", "aria-label": "Detection density heatmap", role: "img" });
  canvas.width = 320; canvas.height = 180; // properties, not attributes/styles
  wrap.append(canvas);
  const ctx = canvas.getContext("2d");
  const rows = grid.length || 0;
  const cols = rows ? grid[0].length : 0;
  const max = Math.max(1, ...grid.flat());
  if (!rows || !cols) {
    wrap.append(h("p", { class: "muted" },
      "No detections in this range — the heatmap has nothing to draw yet."));
    return wrap;
  }
  const cw = canvas.width / cols;
  const ch = canvas.height / rows;
  for (let y = 0; y < rows; y++) {
    for (let x = 0; x < cols; x++) {
      const v = grid[y][x] / max; // 0..1
      if (v <= 0) continue;
      ctx.fillStyle = `rgba(226, 232, 240, ${(0.12 + v * 0.75).toFixed(2)})`;
      ctx.fillRect(x * cw, y * ch, Math.ceil(cw), Math.ceil(ch));
    }
  }
  return wrap;
}

// ── the view ─────────────────────────────────────────────────────────────

export async function loadAnalytics(outEl, params = {}) {
  if (params.range && RANGES.some((r) => r.key === params.range)) sel.range = params.range;
  if (params.camera !== undefined) sel.cameraId = params.camera || null;
  if (params.q !== undefined) return renderSearch(outEl, params);

  const [cams] = await Promise.all([api("/api/cameras").catch(() => [])]);
  const { start, end } = rangeBounds();

  render(outEl, h("div", { class: "an-grid" },
    h("div", { class: "card an-controls", "data-role": "an-controls" },
      h("div", { class: "panel-head" },
        h("h2", {}, "Analytics"),
        h("div", { class: "toolbar" },
          h("label", { class: "visually-hidden", for: "an-range" }, "Time range"),
          h("select", {
            id: "an-range", "data-field": "range",
            onChange: (e) => { sel.range = e.target.value; loadAnalytics(outEl, {}); },
          }, RANGES.map((r) => h("option", { value: r.key, selected: r.key === sel.range }, r.label))),
          h("label", { class: "visually-hidden", for: "an-camera" }, "Camera"),
          h("select", {
            id: "an-camera", "data-field": "camera",
            onChange: (e) => { sel.cameraId = e.target.value || null; loadAnalytics(outEl, {}); },
          },
            h("option", { value: "" }, "All cameras"),
            cams.map((c) => h("option", { value: c.id, selected: c.id === sel.cameraId }, c.name)),
          ),
        ),
      ),
      searchBox(outEl),
    ),
    h("div", { class: "an-widgets", "data-role": "an-widgets" },
      h("div", { class: "card" }, h("div", { class: "skeleton skeleton-card" })),
      h("div", { class: "card" }, h("div", { class: "skeleton skeleton-card" })),
      h("div", { class: "card" }, h("div", { class: "skeleton skeleton-card" })),
      h("div", { class: "card" }, h("div", { class: "skeleton skeleton-card" })),
      h("div", { class: "card" }, h("div", { class: "skeleton skeleton-card" })),
    ),
  ));
  drawWidgets(outEl, start, end, cams);
}

/** Fetch every widget's data in parallel; each renders independently —
 * one failing widget never blanks the page. */
async function drawWidgets(outEl, start, end, cams) {
  const wrap = outEl.querySelector("[data-role='an-widgets']");
  if (!wrap) return;
  const camQ = sel.cameraId
    ? `camera_id=${encodeURIComponent(sel.cameraId)}&start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`
    : `start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`;

  const load = (path) => api(`/api/${path}${path.includes("?") ? "&" : "?"}${camQ}`).catch((err) => err);

  // occupancy needs a camera (the endpoint is per-camera): pick selected or first
  const camId = sel.cameraId || (cams[0] && cams[0].id);
  const tasks = {
    occupancy: camId ? load(`analytics/occupancy?camera_id=${camId}&bucket_min=60`) : null,
    dwell: camId ? load(`analytics/dwell?camera_id=${camId}`) : null,
    breakdown: load("analytics/breakdown"),
  };

  const [occ, dwell, brk] = await Promise.all(
    [tasks.occupancy, tasks.dwell, tasks.breakdown]);

  const cards = [];
  cards.push(widget("People-count trend", errOr(occ)
    ? errorState(occ, { noun: "trend", onRetry: () => loadAnalytics(outEl, {}) })
    : lineChart(occ.buckets || []), "an-span-2"));
  cards.push(widget("Peak occupancy", errOr(occ)
    ? errorState(occ, { noun: "occupancy", onRetry: () => loadAnalytics(outEl, {}) })
    : peakCard(occ.buckets || [])));
  cards.push(widget("Dwell", errOr(dwell)
    ? errorState(dwell, { noun: "dwell", onRetry: () => loadAnalytics(outEl, {}) })
    : dwellCard(dwell.avg_dwell_sec ?? 0)));
  cards.push(widget("Events by type", errOr(brk)
    ? errorState(brk, { noun: "breakdown", onRetry: () => loadAnalytics(outEl, {}) })
    : breakdownBars(brk.rows || [])));

  // heatmap needs its own grid — same pattern, one more fetch
  const hm = camId
    ? await load(`analytics/heatmap?camera_id=${camId}&grid_x=16&grid_y=9`).catch((e) => e)
    : null;
  cards.push(widget("Detection density", errOr(hm)
    ? errorState(hm, { noun: "heatmap", onRetry: () => loadAnalytics(outEl, {}) })
    : heatmapCanvas(hm.grid || []), "an-span-2"));

  render(wrap, cards);
}

function errOr(v) { return v instanceof Error; }

function widget(title, body, extraClass = "") {
  return h("div", { class: `card an-widget ${extraClass}` },
    h("h3", { class: "an-widget-title" }, title),
    body);
}

// ── natural-language search ───────────────────────────────────────────────

const EXAMPLES = [
  "people at the dock",
  "unknown person after 6pm",
  "line crossings",
];

function searchBox(outEl) {
  return h("form", { class: "an-search", "data-form": "an-search", role: "search" },
    h("label", { class: "visually-hidden", for: "an-q" }, "Search events in plain language"),
    h("input", {
      id: "an-q", "data-field": "q", type: "search",
      placeholder: "Search in plain language — “people at the dock”",
      autocomplete: "off",
    }),
    h("button", { class: "primary", type: "submit" }, "Search"),
    h("div", { class: "an-examples" },
      EXAMPLES.map((ex) => h("button", {
        class: "ghost linklike", type: "button",
        onClick: () => {
          const inp = document.getElementById("an-q");
          if (inp) { inp.value = ex; inp.focus(); }
        },
      }, ex)),
    ),
  );
}

/** Results view: ranked events with a score, linking into the Events view
 * with the camera filter pre-filled — the investigation loop's front door. */
async function renderSearch(outEl, { q }) {
  render(outEl, h("div", { class: "an-grid" },
    h("div", { class: "card an-controls" },
      h("div", { class: "panel-head" },
        h("h2", {}, "Analytics — natural-language search"),
        h("button", { class: "ghost", onClick: () => loadAnalytics(outEl, {}) }, "← Back to analytics"),
      ),
      searchBox(outEl),
    ),
    h("div", { class: "an-widgets an-span-full", "data-role": "an-results" },
      h("div", { class: "skeleton skeleton-row" })),
  ));

  const form = outEl.querySelector("[data-form='an-search']");
  const inp = form && form.querySelector("[data-field='q']");
  if (inp && !q) { inp.value = ""; }
  if (inp) { inp.value = q || ""; }

  let data;
  try {
    data = await api(`/api/analytics/search?q=${encodeURIComponent(q)}&top_k=20`);
  } catch (err) {
    render(outEl.querySelector("[data-role='an-results']"),
      errorState(err, { noun: "search", onRetry: () => renderSearch(outEl, { q }) }));
    return;
  }
  rememberQuery(q);
  const results = data.results || [];
  const cams = await api("/api/cameras").catch(() => []);
  const nameOf = (id) => (cams.find((c) => c.id === id) || { name: id.slice(0, 8) }).name;

  render(outEl.querySelector("[data-role='an-results']"),
    results.length
      ? h("div", { class: "table-scroll" },
          h("table", {},
            h("thead", {}, h("tr", {},
              h("th", {}, "When"), h("th", {}, "Camera"),
              h("th", {}, "Type"), h("th", {}, "Match"))),
            h("tbody", {}, results.map((r) => h("tr", {
              class: "event-row linklike", "data-event-id": r.id,
              onClick: () => { location.hash = `#/event/${r.id}`; },
            },
              h("td", { class: "mono" }, fmtDateTime(r.ts)),
              h("td", {}, nameOf(r.camera_id)),
              h("td", {}, r.event_type),
              h("td", {}, h("span", { class: "pill" }, `${(r.score * 100).toFixed(0)}%`)),
            ))),
          ),
          h("p", { class: "muted text-xs" },
            "Ranked by similarity to your words. The reference embedder is deterministic — a production VLM swaps in behind the same API."))
      : emptyState({
          icon: "◔", title: "Nothing matched",
          hint: "Try different words, or widen the time range on the analytics screen.",
          action: { label: "Back to analytics", onClick: () => loadAnalytics(outEl, {}) },
        }),
  );
}

/** Keep the last 5 queries in sessionStorage (never localStorage — this is
 * an investigation trail, not a persistent tracking artifact). */
function rememberQuery(q) {
  if (!q) return;
  try {
    const key = "lv-nl-history";
    const hist = JSON.parse(sessionStorage.getItem(key) || "[]").filter((x) => x !== q);
    hist.unshift(q);
    sessionStorage.setItem(key, JSON.stringify(hist.slice(0, 5)));
  } catch { /* storage unavailable — history is optional, never fatal */ }
}

export function wireAnalytics(outEl) {
  outEl.addEventListener("submit", (e) => {
    const form = e.target.closest("[data-form='an-search']");
    if (!form) return;
    e.preventDefault();
    const q = form.querySelector("[data-field='q']").value.trim();
    if (!q) { toast("Type something to search for first."); return; }
    navigate("analytics", { q });
  });
}
