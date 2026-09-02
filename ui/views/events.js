// Events view — Wave 1: the investigation list.
//
// Row click opens the detail drawer; ↑/↓ walk rows and Enter opens;
// filters serialize into the URL hash so an investigation is shareable
// (router.js owns view state now — the old JS-only state is gone).

import { h, render } from "../core/dom.js";
import { api, can } from "../core/api.js";
import { toast } from "../core/toast.js";
import { skeletonRows, emptyState, errorState } from "../core/states.js";
import { fmtTime, fmtDateTime, fmtIso, fmtRelative, fmtDuration, shortId, label, tone }
  from "../core/format.js";
import { navigate } from "../core/router.js";

const PAGE = 25;
let cameraNames = null;
let offset = 0;
let sortKey = "timestamp";     // M1/E-1: server-side sort state
let sortDir = "desc";
let lastItems = [];
let cursor = -1; // keyboard selection index
let listWrap = null;

async function names() {
  if (cameraNames) return cameraNames;
  try {
    const cams = await api("/api/cameras");
    cameraNames = new Map((Array.isArray(cams) ? cams : cams.items || []).map((c) => [c.id, c.name]));
  } catch { cameraNames = new Map(); }
  return cameraNames;
}

function openEvent(id) {
  navigate("events", currentFilters(), id);
}

function currentFilters() {
  const cameraId = document.getElementById("ev-camera").value.trim();
  const status = document.getElementById("ev-status").value;
  return { ...(cameraId ? { camera: cameraId } : {}), ...(status ? { status } : {}) };
}

function applyFiltersToInputs(params) {
  document.getElementById("ev-camera").value = params.camera || "";
  document.getElementById("ev-status").value = params.status || "";
}

function eventRow(e, nameMap, idx) {
  const camName = nameMap.get(e.camera_id) || `camera ${shortId(e.camera_id)}`;
  const durSec = (new Date(e.timestamp_end) - new Date(e.timestamp_start)) / 1000;
  return h("tr", {
    class: "event-row",
    dataset: { eventId: e.id, idx: String(idx) },
    onClick: () => openEvent(e.id),
  },
    h("td", {}, camName),
    h("td", {}, e.identity_id
      ? h("span", { class: `pill ${tone(e.identity_status)}` }, label(e.identity_status))
      : h("span", { class: "pill" }, "—")),
    h("td", {}, label(e.event_type)),
    h("td", { class: "mono" },
      fmtTime(e.timestamp_start),
      h("div", { class: "muted" }, fmtRelative(e.timestamp_start)),
    ),
    h("td", { class: "mono" }, Number.isFinite(durSec) ? fmtDuration(durSec) : "—"),
    h("td", { class: "mono" }, e.confidence.toFixed(2)),
    h("td", {},
      h("span", { class: "media-flags" },
        e.has_snapshot ? h("span", { class: "media-flag", title: "Has snapshot" }, "◉") : null,
        e.has_video ? h("span", { class: "media-flag", title: "Has video" }, "▶") : null,
        (!e.has_snapshot && !e.has_video) ? h("span", { class: "muted" }, "—") : null,
      ),
    ),
  );
}

function moveCursor(delta) {
  if (!lastItems.length || !listWrap) return;
  cursor = Math.min(Math.max(cursor + delta, 0), lastItems.length - 1);
  const rows = listWrap.querySelectorAll("tr.event-row");
  rows.forEach((r) => r.classList.toggle("cursor", Number(r.dataset.idx) === cursor));
  const sel = rows[cursor];
  if (sel) sel.scrollIntoView({ block: "nearest" });
}

export async function loadEvents(wrapEl, { resetOffset = false, params = {} } = {}) {
  listWrap = wrapEl;
  // Export gate runs on every load — can() is only live after /api/auth/me,
  // so a boot-time toggle would hide the button for entitled roles.
  const eb = document.getElementById("ev-export");
  if (eb) eb.classList.toggle("hidden", !can("events:export"));
  applyFiltersToInputs(params);
  if (resetOffset) offset = 0;
  cursor = -1;
  const filters = currentFilters();
  skeletonRows(wrapEl, 6);
  try {
    const [data, nameMap] = await Promise.all([
      api(`/api/events?${new URLSearchParams({
        limit: PAGE, offset, sort: sortKey, direction: sortDir,
        ...(filters.camera ? { camera_id: filters.camera } : {}),
        ...(filters.status ? { identity_status: filters.status } : {}),
      })}`),
      names(),
    ]);
    lastItems = data.items || [];
    const rows = lastItems.map((e, i) => eventRow(e, nameMap, i));

    // M1/E-1: sortable headers. aria-sort announces state; the arrow is
    // CSS (::after on .sorted-asc/.sorted-desc). Clicking toggles direction.
    const th = (key, text) => {
      const active = sortKey === key;
      return h("th", {
        "aria-sort": active ? (sortDir === "asc" ? "ascending" : "descending") : "none",
        class: active ? (sortDir === "asc" ? "sorted-asc" : "sorted-desc") : "",
        scope: "col",
        ...(key ? { onClick: () => {
          if (sortKey === key) sortDir = sortDir === "asc" ? "desc" : "asc";
          else { sortKey = key; sortDir = key === "timestamp" ? "desc" : "asc"; }
          offset = 0;
          loadEvents(wrapEl);
        } } : {}),
      }, text);
    };

    render(wrapEl, rows.length
      ? h("div", { class: "table-scroll" },
          h("table", { id: "events-table" },
            h("thead", {}, h("tr", {},
              th("camera", "Camera"), th("identity", "Identity"), th("type", "Type"),
              th("timestamp", "Start"), th("duration", "Duration"), th("confidence", "Conf"),
              th(null, "Media"),
            )),
            h("tbody", {}, rows),
          ),
        )
      : emptyState({
          icon: "◌", title: "No events match",
          hint: "Adjust the filters — or clear them to see all events.",
        }));
    document.getElementById("ev-page").textContent =
      `${Math.floor(offset / PAGE) + 1}${data.total ? ` / ${Math.ceil(data.total / PAGE)}` : ""}`;
    document.getElementById("ev-prev").disabled = offset <= 0;
    document.getElementById("ev-next").disabled = !lastItems.length || lastItems.length < PAGE;
  } catch (err) {
    render(wrapEl, errorState(err, { noun: "events", onRetry: () => loadEvents(wrapEl, { params }) }));
  }
}

export function wireEventsView(wrapEl) {
  listWrap = wrapEl;
  document.getElementById("ev-search").addEventListener("click", () => {
    navigate("events", currentFilters()); // URL carries the investigation
  });
  const exportBtn = document.getElementById("ev-export");
  if (exportBtn) exportBtn.classList.toggle("hidden", !can("events:export"));
  if (exportBtn && !exportBtn.dataset.wired) {
    exportBtn.dataset.wired = "1";
    exportBtn.addEventListener("click", async () => {
      // M1/E-2: export the CURRENT result set — same filters + sort the table
      // shows, honored server-side; the export itself is audited. Goes through
      // the shared fetch layer so the auth header rides along, then a blob
      // download hands the file to the operator.
      exportBtn.disabled = true;
      const old = exportBtn.textContent;
      exportBtn.textContent = "Exporting…";
      try {
        const f = currentFilters();
        const qs = new URLSearchParams({
          ...(f.camera ? { camera_id: f.camera } : {}),
          ...(f.status ? { identity_status: f.status } : {}),
          sort: sortKey, direction: sortDir,
        });
        const csv = await api(`/api/events/export.csv?${qs}`);
        const blob = new Blob([csv], { type: "text/csv" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `events-${new Date().toISOString().slice(0, 10)}.csv`;
        a.click();
        URL.revokeObjectURL(url);
        toast(`Exported ${csv.split("\n").length - 1} rows — the download is yours`, { tone: "ok" });
      } catch (err) {
        toast(err.status === 403 ? "Your role can't export events" : "Export failed — try again",
          { tone: "error" });
      } finally {
        exportBtn.disabled = false;
        exportBtn.textContent = old;
      }
    });
  }
  document.getElementById("ev-prev").addEventListener("click", () => {
    offset = Math.max(0, offset - PAGE);
    loadEvents(wrapEl);
  });
  document.getElementById("ev-next").addEventListener("click", () => {
    offset += PAGE;
    loadEvents(wrapEl);
  });

  // Keyboard: ↑/↓ select, Enter opens — active only in the events view
  // and when focus isn't in a filter input.
  document.addEventListener("keydown", (e) => {
    const panel = document.querySelector('[data-panel="events"]');
    if (!panel || panel.classList.contains("hidden")) return;
    if (document.activeElement && ["INPUT", "SELECT"].includes(document.activeElement.tagName)) return;
    if (e.key === "ArrowDown") { e.preventDefault(); moveCursor(1); }
    if (e.key === "ArrowUp") { e.preventDefault(); moveCursor(-1); }
    if (e.key === "Enter" && cursor >= 0 && lastItems[cursor]) {
      e.preventDefault();
      openEvent(lastItems[cursor].id);
    }
  });
}
