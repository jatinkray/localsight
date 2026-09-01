// Overview dashboard (Wave 2 rebuild).
//
// Wave 0 fixed the honest numbers (C-8/C-9). Wave 2 makes this the NOC's
// first screen: camera status strip (click → live), 24h events sparkline
// (CSP-safe inline SVG — attributes, never style props), recent events,
// alert feed, and 15s auto-refresh that PAUSES when the tab is hidden
// (nobody refreshes by clicking nav anymore; nobody burns CPU on a
// background tab either).
//
// Wave 0 also replaced 4 dashboard calls with 1 via /api/dashboard/summary
// (C-9) — the remaining calls here are the ones summary doesn't cover:
// camera list (strip), recent events, alert feed.

import { h, render, svgEl } from "../core/dom.js";
import { api } from "../core/api.js";
import { skeletonCards, errorState, emptyState } from "../core/states.js";
import { fmtRelative, fmtTime, label, tone, shortId } from "../core/format.js";
import { navigate } from "../core/router.js";

const REFRESH_MS = 15_000;
let refreshTimer = null;

function todayStart() {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d.toISOString();
}

/** CSP-safe SVG sparkline of counts per bucket (attributes only). */
function sparkline(counts, { w = 96, h = 28 } = {}) {
  if (!counts.length) return h("span", { class: "muted" }, "—");
  const max = Math.max(...counts, 1);
  const n = counts.length;
  const svg = svgEl("svg", {
    class: "spark", viewBox: `0 0 ${w} ${h}`, role: "img",
    "aria-label": "events trend",
  });
  const bw = w / n;
  counts.forEach((c, i) => {
    const bh = Math.max((c / max) * (h - 2), c > 0 ? 1.5 : 0);
    svg.append(svgEl("rect", {
      x: i * bw + 0.5, y: h - bh, width: Math.max(bw - 1, 0.5), height: bh,
      rx: 0.5, fill: "currentColor",
    }));
  });
  return svg;
}

/** Hourly event counts for the last 24h from the events list. */
function hourlyBuckets(events) {
  const now = new Date();
  const buckets = new Array(24).fill(0);
  for (const e of events) {
    const t = new Date(e.timestamp_start);
    const hoursAgo = Math.floor((now - t) / 3_600_000);
    if (hoursAgo >= 0 && hoursAgo < 24) buckets[23 - hoursAgo] += 1;
  }
  return buckets;
}

function camStrip(camList, nameOf) {
  return h("div", { class: "cam-strip" },
    ...camList.map((c) => h("button", {
      class: "cam-chip", type: "button",
      title: `Open live view for ${c.name}`,
      onClick: () => navigate("live", { camera: c.id }),
    },
      h("span", { class: `dot ${c.status === "ONLINE" ? "ok" : c.status === "DEGRADED" ? "warn" : "crit"}`, "aria-hidden": "true" }),
      h("span", {}, c.name),
    )),
  );
}

function recentEventRow(e, nameMap) {
  const camName = nameMap.get(e.camera_id) || `camera ${shortId(e.camera_id)}`;
  return h("button", {
    class: "recent-event", type: "button",
    onClick: () => navigate("events", {}, e.id),
  },
    h("span", { class: `pill ${tone(e.identity_status)}` }, label(e.identity_status || "unknown")),
    h("span", { class: "recent-cam" }, camName),
    h("span", { class: "recent-type" }, label(e.event_type)),
    h("span", { class: "recent-time muted" }, fmtTime(e.timestamp_start), " · ", fmtRelative(e.timestamp_start)),
  );
}

export async function loadDashboard(statCards, healthEl, extra) {
  // extra = { stripEl, trendEl, recentEl, alertsEl } (Wave 2 panels)
  skeletonCards(statCards);
  try {
    const [health, cams, people, summary] = await Promise.all([
      api("/api/system/health").catch(() => null),
      api("/api/cameras").catch(() => null),
      api("/api/persons").catch(() => null),
      api("/api/dashboard/summary").catch(() => null),
    ]);

    const camList = cams ? (Array.isArray(cams) ? cams : cams.items || []) : [];
    const peopleList = people ? (Array.isArray(people) ? people : []) : [];
    const online = camList.filter((c) => c.status === "ONLINE").length;
    const degraded = camList.filter((c) => c.status === "DEGRADED" || c.status === "RECONNECTING").length;

    const evToday = summary?.events_today?.total ?? 0;
    const evUnknown = summary?.events_today?.unknown ?? 0;

    render(statCards, [
      h("div", { class: "card stat" },
        h("h3", {}, "Cameras online"),
        h("div", { class: "big" }, `${online}/${camList.length}`),
        degraded ? h("div", { class: "stat-sub" }, `${degraded} degraded`) : null,
      ),
      h("div", { class: "card stat" },
        h("h3", {}, "Events today"),
        h("div", { class: "big" }, String(evToday)),
        h("div", { class: "stat-sub" }, `${evUnknown} unknown identity`),
      ),
      h("div", { class: "card stat" },
        h("h3", {}, "Enrolled identities"),
        h("div", { class: "big" }, String(peopleList.length)),
        h("div", { class: "stat-sub" }, "reference embeddings stored"),
      ),
      h("div", { class: "card stat" },
        h("h3", {}, "System"),
        h("div", { class: "big" }, label(health?.status || "?").toUpperCase()),
        h("div", { class: "stat-sub" }, "database + AI model"),
      ),
    ]);

    if (health) {
      const comps = health.components || {};
      render(healthEl, [
        h("h2", {}, "Health"),
        h("div", { class: "health-grid" },
          ...Object.entries(comps).map(([name, comp]) => {
            const status = comp && typeof comp === "object" ? comp.status : comp;
            const detail = comp && typeof comp === "object" && comp.name
              ? `${comp.name}${comp.version ? ` · ${comp.version}` : ""}` : "";
            const pillTone = status === "ok" ? "ok" : status === "down" || status === "degraded" ? "bad" : "warn";
            return h("div", { class: "health-row" },
              h("span", { class: "health-name" }, name),
              h("span", {},
                detail ? h("span", { class: "health-detail" }, `${detail} — `) : null,
                h("span", { class: `pill ${pillTone}` }, label(status ?? "unknown")),
              ),
            );
          }),
        ),
      ]);
    } else {
      render(healthEl, [h("h2", {}, "Health"), h("p", { class: "muted" }, "Health unavailable.")]);
    }

    // ── Wave 2 panels ────────────────────────────────────────────────
    if (!extra) return;

    const nameMap = new Map(camList.map((c) => [c.id, c.name]));
    render(extra.stripEl, camList.length
      ? camStrip(camList)
      : emptyState({ icon: "◉", title: "No cameras", hint: "Add cameras to monitor them here." }));

    // recent events + trend (single fetch powers both)
    let recent = [];
    try {
      const evs = await api(`/api/events?limit=50&start=${encodeURIComponent(
        new Date(Date.now() - 86_400_000).toISOString())}`);
      recent = evs.items || [];
    } catch { recent = []; }
    render(extra.trendEl, [
      h("h3", {}, "Events — last 24 hours"),
      h("div", { class: "spark-wrap" },
        sparkline(hourlyBuckets(recent)),
        h("span", { class: "muted spark-note" }, `${recent.length} events`),
      ),
    ]);
    render(extra.recentEl, [
      h("h3", {}, "Recent events"),
      recent.length
        ? h("div", { class: "recent-list" },
            ...recent.slice(0, 5).map((e) => recentEventRow(e, nameMap)))
        : emptyState({ icon: "◌", title: "No events yet", hint: "Detections appear here in real time." }),
    ]);

    // alert feed (analytic events = the non-presence stream)
    let alerts = [];
    try { alerts = await api("/api/alerts/events?limit=20"); } catch { alerts = []; }
    render(extra.alertsEl, [
      h("h3", {}, "Alert feed"),
      alerts.length
        ? h("div", { class: "alert-feed" },
            ...alerts.slice(0, 8).map((a) => h("div", { class: "alert-item" },
              h("span", { class: `pill ${tone(a.identity_status)}` }, label(a.event_type)),
              h("span", { class: "recent-cam" }, nameMap.get(a.camera_id) || `camera ${shortId(a.camera_id)}`),
              h("span", { class: "recent-time muted" }, fmtRelative(a.timestamp_start)),
            )))
        : emptyState({ icon: "◔", title: "No alerts", hint: "Rule and ANPR events will land here." }),
    ]);
  } catch (err) {
    render(statCards, errorState(err, { noun: "overview" }));
  }
}

/** 15s auto-refresh, paused while the tab is hidden (Wave 2). */
export function startAutoRefresh(load) {
  clearInterval(refreshTimer);
  const tick = () => { if (document.visibilityState === "visible") load(); };
  refreshTimer = setInterval(tick, REFRESH_MS);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") load(); // catch up immediately on return
  });
  return () => clearInterval(refreshTimer);
}
