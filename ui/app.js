// LocalVision app shell — ES-module orchestrator.
//
// Wave 1: the hash router (core/router.js) owns view state. Views register
// loaders; navigation = hash change; back/forward and shareable URLs work.
// The shell only wires chrome (nav, login, logout) and boots the session.

import { $ } from "./core/dom.js";
import { api, can, restoreSession, logout as apiLogout, hasSession, startRefreshLoop, onAuthEvent } from "./core/api.js";
import { toast } from "./core/toast.js";
import { onView, navigate, parseHash, start as startRouter } from "./core/router.js";
import { openEventDrawer, requestClose } from "./views/event_drawer.js";
import { wireLogin, resetLogin } from "./views/login.js";
import { loadDashboard, startAutoRefresh } from "./views/dashboard.js";
import { loadCameras } from "./views/cameras.js";
import { loadEvents, wireEventsView } from "./views/events.js";
import { loadTimeline } from "./views/timeline.js";
import { loadLive, wireLiveView } from "./views/live.js";
import { loadPeople } from "./views/people.js";
import { loadAudit, wireAuditView } from "./views/audit.js";
import { loadAlertsAdmin } from "./views/alerts_admin.js";
import { loadUsers } from "./views/users.js";
import { loadPrivacy } from "./views/privacy.js";
import { loadAnalytics, wireAnalytics } from "./views/analytics.js";
import { wireShortcuts } from "./core/shortcuts.js";
import { wireDensity, restoreDensity } from "./core/density.js";
import { wireTelemetry, markView } from "./core/telemetry.js";

function showPanels(view) {
  document.querySelectorAll("#nav button").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === view);
    if (b.dataset.view === view) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  });
  document.querySelectorAll(".panel").forEach((p) =>
    p.classList.toggle("hidden", p.dataset.panel !== view));
  closeMobileNav();
}

function closeMobileNav() {
  const nav = $("#nav");
  if (nav && nav.classList.contains("open")) {
    nav.classList.remove("open");
    $("#nav-toggle").setAttribute("aria-expanded", "false");
  }
}

// ── view loaders (router calls these) ────────────────────────────────────
onView("dashboard", () => {
  showPanels("dashboard");
  loadDashboard($("#stat-cards"), $("#health"), {
    stripEl: $("#cam-strip"),
    trendEl: $("#trend"),
    recentEl: $("#recent-events"),
    alertsEl: $("#alerts-feed"),
  });
});
onView("live", (params) => {
  showPanels("live");
  loadLive($("#live-out"));
  if (params.camera) {
    // deep link: focus that camera's tile once rendered
    setTimeout(() => {
      const tile = document.querySelector(`.live-tile[data-cam-id="${CSS.escape(params.camera)}"]`);
      if (tile) tile.scrollIntoView({ block: "center" });
    }, 600);
  }
});
onView("cameras", (params) => {
  showPanels("cameras");
  loadCameras($("#cameras-list"), params);
});
onView("events", (params, eventId) => {
  showPanels("events");
  loadEvents($("#events-wrap"), { resetOffset: true, params });
  if (eventId) openEventDrawer(eventId, { onClose: () => navigate("events", params) });
  else requestClose(); // landing on events plain: ensure drawer shut
});
onView("timeline", (params) => {
  showPanels("timeline");
  if (params.date) $("#tl-date").value = params.date;
  if (params.camera) $("#tl-camera").value = params.camera;
  loadTimeline($("#timeline-out"), {
    date: params.date || $("#tl-date").value || new Date().toISOString().slice(0, 10),
    cameraId: params.camera || $("#tl-camera").value.trim() || undefined,
  });
});
onView("people", (params) => {
  showPanels("people");
  loadPeople($("#people-list"), params);
});
onView("alerts", (params) => {
  showPanels("alerts");
  loadAlertsAdmin($("#alerts-admin-list"), params);
});
onView("users", (params) => {
  showPanels("users");
  loadUsers($("#users-list"), params);
});
onView("privacy", () => {
  showPanels("privacy");
  loadPrivacy($("#privacy-list"));
});
onView("analytics", (params) => {
  showPanels("analytics");
  loadAnalytics($("#analytics-list"), params);
});
onView("audit", () => {
  showPanels("audit");
  loadAudit($("#audit-list"));
});

function enterApp() {
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
  applyRbacGates();
  startRouter(); // resolve #/ — lands on the URL's view (or dashboard)
  // Opt-in marks: record each navigation (route() is synchronous in the
  // earlier-registered hashchange listener; the microtask measures the
  // dispatch tail — honest diagnostics, not a perf claim).
  window.addEventListener("hashchange", () => {
    const t0 = performance.now();
    queueMicrotask(() => markView(parseHash().view, performance.now() - t0));
  });
}

/** Hide chrome the current role can't use. Runs after the session (and
 *  therefore /api/auth/me — the can() source) is live. */
function applyRbacGates() {
  const gate = (sel, perm) => {
    const el = $(sel);
    if (el) el.classList.toggle("hidden", !can(perm));
  };
  gate("#add-camera", "camera:configure");
  gate("#person-add", "person:enroll");
  gate("#route-add", "alerts:manage");
  document.querySelectorAll('#nav button[data-view="alerts"]')
    .forEach((b) => b.classList.toggle("hidden", !can("alerts:manage")));
  document.querySelectorAll('#nav button[data-view="users"]')
    .forEach((b) => b.classList.toggle("hidden", !can("user:manage")));
}

function leaveApp({ notice = null } = {}) {
  $("#app").classList.add("hidden");
  $("#login").classList.remove("hidden");
  resetLogin();
  if (notice) toast(notice, { tone: "warn", timeout: 6000 });
}

// ── boot ────────────────────────────────────────────────────────────────
async function boot() {
  const restored = hasSession() ? await restoreSession() : false;

  wireLogin($("#login-form"), enterApp);
  document.querySelectorAll("#nav button").forEach((b) =>
    b.addEventListener("click", () => navigate(b.dataset.view)));
  $("#logout").addEventListener("click", async () => { await apiLogout(); leaveApp(); });
  $("#nav-toggle").addEventListener("click", () => {
    const nav = $("#nav");
    const open = nav.classList.toggle("open");
    $("#nav-toggle").setAttribute("aria-expanded", String(open));
  });
  wireEventsView($("#events-wrap"));
  wireLiveView($("#live-out"), $("#live-toolbar"));
  wireAnalytics($("#analytics-list"));
  wireShortcuts();
  wireTelemetry(); // opt-in local marks — a no-op until enabled in Privacy
  restoreDensity();
  wireDensity($("#density-toggle"));

  $("#add-camera").addEventListener("click", () => navigate("cameras", { id: "new" }));
  $("#person-add").addEventListener("click", () => navigate("people", { new: "1" }));
  $("#route-add").addEventListener("click", () => navigate("alerts", { new: "1" }));

  startAutoRefresh(() => {
    // refresh the Overview whenever it's the visible view (auto-refresh
    // pauses itself when the tab is hidden — visibilitychange listener
    // inside dashboard.js)
    const panel = document.querySelector('[data-panel="dashboard"]');
    if (panel && !panel.classList.contains("hidden")) {
      loadDashboard($("#stat-cards"), $("#health"), {
        stripEl: $("#cam-strip"),
        trendEl: $("#trend"),
        recentEl: $("#recent-events"),
        alertsEl: $("#alerts-feed"),
      });
    }
  });
  $("#tl-go").addEventListener("click", () => {
    navigate("timeline", {
      date: $("#tl-date").value || undefined,
      camera: $("#tl-camera").value.trim() || undefined,
    });
  });
  wireAuditView($("#audit-list"));

  onAuthEvent((ev) => {
    if (ev.type === "auth:expired") leaveApp({ notice: "Session expired — please sign in again." });
    if (ev.type === "auth:restored") toast("Session renewed", { tone: "ok", timeout: 2000 });
  });

  if (restored) {
    enterApp();
    startRefreshLoop();
  } else {
    $("#login").classList.remove("hidden");
  }
}

boot();
