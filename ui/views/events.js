// Events view — camera-name resolution, statuses as pills, relative times,
// pagination with 44px targets, skeleton/empty/error states.
// Event detail drawer ships in Wave 1 (the raw-JSON link is gone).

import { h, render } from "../core/dom.js";
import { api } from "../core/api.js";
import { skeletonRows, emptyState, errorState } from "../core/states.js";
import { fmtTime, fmtRelative, fmtDuration, shortId, label, tone } from "../core/format.js";

const PAGE = 25;
let offset = 0;
let cameraNames = null;

async function names() {
  if (cameraNames) return cameraNames;
  try {
    const cams = await api("/api/cameras");
    cameraNames = new Map((Array.isArray(cams) ? cams : cams.items || []).map((c) => [c.id, c.name]));
  } catch { cameraNames = new Map(); }
  return cameraNames;
}

function eventRow(e, nameMap) {
  const camName = nameMap.get(e.camera_id) || shortId(e.camera_id);
  const durSec = (new Date(e.timestamp_end) - new Date(e.timestamp_start)) / 1000;
  return h("tr", {},
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
  );
}

export async function loadEvents(listWrap, { resetOffset = false } = {}) {
  if (resetOffset) offset = 0;
  const cameraId = document.getElementById("ev-camera").value.trim();
  const status = document.getElementById("ev-status").value;
  skeletonRows(listWrap, 6);
  try {
    const [data, nameMap] = await Promise.all([
      api(`/api/events?${new URLSearchParams({ limit: PAGE, offset, ...(cameraId ? { camera_id: cameraId } : {}), ...(status ? { identity_status: status } : {}) })}`),
      names(),
    ]);
    const rows = (data.items || []).map((e) => eventRow(e, nameMap));
    render(listWrap, rows.length
      ? h("div", { class: "table-scroll" },
          h("table", { id: "events-table" },
            h("thead", {}, h("tr", {},
              h("th", {}, "Camera"), h("th", {}, "Identity"), h("th", {}, "Type"),
              h("th", {}, "Start"), h("th", {}, "Duration"), h("th", {}, "Conf"),
            )),
            h("tbody", {}, rows),
          ),
        )
      : emptyState({
          icon: "◌", title: "No events match",
          hint: "Adjust the camera or status filters, or clear them to see all events.",
        }));
    document.getElementById("ev-page").textContent =
      `${Math.floor(offset / PAGE) + 1}${data.total ? ` / ${Math.ceil(data.total / PAGE)}` : ""}`;
    document.getElementById("ev-prev").disabled = offset <= 0;
    document.getElementById("ev-next").disabled = !data.items || data.items.length < PAGE;
  } catch (err) {
    render(listWrap, errorState(err, { noun: "events", onRetry: () => loadEvents(listWrap) }));
  }
}

export function wireEventsPager(listWrap) {
  document.getElementById("ev-prev").addEventListener("click", () => {
    offset = Math.max(0, offset - PAGE);
    loadEvents(listWrap);
  });
  document.getElementById("ev-next").addEventListener("click", () => {
    offset += PAGE;
    loadEvents(listWrap);
  });
}
