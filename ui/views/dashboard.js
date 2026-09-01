// Overview dashboard (fixes C-8: health renders schema-aware; C-9: real
// "events today" count via start filter — one endpoint, honest numbers).

import { h, render } from "../core/dom.js";
import { api } from "../core/api.js";
import { skeletonCards, errorState } from "../core/states.js";
import { fmtRelative, label } from "../core/format.js";

function todayStart() {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d.toISOString();
}

/** Health row: schema-driven (C-8 fix — old UI printed "undefined"). */
function healthRow(name, comp) {
  const status = comp && typeof comp === "object" ? comp.status : comp;
  const detail = comp && typeof comp === "object" && comp.name
    ? `${comp.name}${comp.version ? ` · ${comp.version}` : ""}`
    : "";
  const pillTone = status === "ok" ? "ok" : status === "down" || status === "degraded" ? "bad" : "warn";
  return h("div", { class: "health-row" },
    h("span", { class: "health-name" }, name),
    h("span", {},
      detail ? h("span", { class: "health-detail" }, `${detail} — `) : null,
      h("span", { class: `pill ${pillTone}` }, label(status ?? "unknown")),
    ),
  );
}

export async function loadDashboard(statCards, healthEl) {
  skeletonCards(statCards);
  try {
    const [health, cams, evsToday, evsUnknown, people] = await Promise.all([
      api("/api/system/health"),
      api("/api/cameras"),
      api(`/api/events?limit=1&start=${encodeURIComponent(todayStart())}`),
      api(`/api/events?limit=1&start=${encodeURIComponent(todayStart())}&identity_status=unknown`),
      api("/api/persons"),
    ]).catch(() => { throw new Error("One or more dashboard services are unreachable"); });

    const camList = Array.isArray(cams) ? cams : cams.items || [];
    const online = camList.filter((c) => c.status === "ONLINE").length;
    const degraded = camList.filter((c) => c.status === "DEGRADED" || c.status === "RECONNECTING").length;
    const peopleList = Array.isArray(people) ? people : [];

    render(statCards, [
      h("div", { class: "card stat" },
        h("h3", {}, "Cameras online"),
        h("div", { class: "big" }, `${online}/${camList.length}`),
        degraded ? h("div", { class: "stat-sub" }, `${degraded} degraded`) : null,
      ),
      h("div", { class: "card stat" },
        h("h3", {}, "Events today"),
        h("div", { class: "big" }, String(evsToday.total ?? 0)),
        h("div", { class: "stat-sub" }, `${evsUnknown.total ?? 0} unknown identity`),
      ),
      h("div", { class: "card stat" },
        h("h3", {}, "Enrolled identities"),
        h("div", { class: "big" }, String(peopleList.length)),
        h("div", { class: "stat-sub" }, "reference embeddings stored"),
      ),
      h("div", { class: "card stat" },
        h("h3", {}, "System"),
        h("div", { class: "big" }, label(health.status || "?").toUpperCase()),
        h("div", { class: "stat-sub" }, "database + AI model"),
      ),
    ]);

    const comps = health.components || {};
    render(healthEl,
      h("h2", {}, "Health"),
      h("div", { class: "health-grid" },
        ...Object.entries(comps).map(([name, comp]) => healthRow(name, comp)),
      ),
    );
  } catch (err) {
    render(statCards, errorState(err, { noun: "overview" }));
    render(healthEl, h("h2", {}, "Health"), h("p", { class: "muted" }, "Health unavailable."));
  }
}
