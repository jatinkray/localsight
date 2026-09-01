// Cameras view — readable table with names (not bare hex), status dots,
// health pills, resolution + fps. Skeleton/empty/error states wired.

import { h, render } from "../core/dom.js";
import { api } from "../core/api.js";
import { skeletonRows, emptyState, errorState } from "../core/states.js";
import { shortId, label, tone } from "../core/format.js";

function cameraRow(c) {
  return h("tr", {},
    h("td", {},
      h("strong", {}, c.name),
      h("div", { class: "muted mono" }, shortId(c.id)),
    ),
    h("td", {},
      h("span", { class: `pill ${tone(c.status)}` },
        h("span", { class: `dot ${tone(c.status)}`, "aria-hidden": "true" }, ""),
        label(c.status),
      ),
    ),
    h("td", {}, label(c.health || "unknown")),
    h("td", { class: "mono" }, c.resolution || "—"),
    h("td", { class: "mono" }, c.fps ? `${c.fps} fps` : "—"),
    h("td", { class: "mono" }, c.last_seen ? new Date(c.last_seen).toLocaleTimeString() : "—"),
  );
}

export async function loadCameras(listEl) {
  skeletonRows(listEl, 4);
  try {
    const data = await api("/api/cameras");
    const cams = Array.isArray(data) ? data : data.items || [];
    render(listEl, cams.length
      ? h("div", { class: "table-scroll" },
          h("table", {},
            h("thead", {}, h("tr", {},
              h("th", {}, "Camera"), h("th", {}, "Status"), h("th", {}, "Health"),
              h("th", {}, "Resolution"), h("th", {}, "FPS"), h("th", {}, "Last seen"),
            )),
            h("tbody", {}, cams.map(cameraRow)),
          ),
        )
      : emptyState({
          icon: "◉", title: "No cameras configured",
          hint: "Cameras are added via the API or ONVIF discovery today; a setup wizard ships in Wave 3.",
        }));
  } catch (err) {
    render(listEl, errorState(err, { noun: "cameras", onRetry: () => loadCameras(listEl) }));
  }
}
