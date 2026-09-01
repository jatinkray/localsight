// LocalVision app shell — ES-module orchestrator (~140 lines).
//
// Wave-0 rebuild of the original 164-line IIFE. View logic lives in
// ui/views/*; session in ui/core/api.js; DOM safety in ui/core/dom.js.
// The old styles.css is retired (tokens/base/components replace it).

import { $ } from "./core/dom.js";
import { api, restoreSession, logout as apiLogout, hasSession, startRefreshLoop, onAuthEvent } from "./core/api.js";
import { toast } from "./core/toast.js";
import { wireLogin, resetLogin } from "./views/login.js";
import { loadDashboard } from "./views/dashboard.js";
import { loadCameras } from "./views/cameras.js";
import { loadEvents, wireEventsPager } from "./views/events.js";
import { loadTimeline } from "./views/timeline.js";
import { loadPeople, wireEnrollForm } from "./views/people.js";
import { loadAudit } from "./views/audit.js";

let currentView = "dashboard";

function show(view) {
  currentView = view;
  document.querySelectorAll("#nav button").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === view);
    if (b.dataset.view === view) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  });
  document.querySelectorAll(".panel").forEach((p) =>
    p.classList.toggle("hidden", p.dataset.panel !== view));
  closeMobileNav();

  if (view === "dashboard") loadDashboard($("#stat-cards"), $("#health"));
  if (view === "cameras") loadCameras($("#cameras-list"));
  if (view === "events") loadEvents($("#events-wrap"), { resetOffset: true });
  if (view === "timeline") loadTimeline($("#timeline-out"), timelineParams());
  if (view === "people") loadPeople($("#people-list"));
  if (view === "audit") loadAudit($("#audit-list"));
}

function timelineParams() {
  return {
    date: $("#tl-date").value || new Date().toISOString().slice(0, 10),
    cameraId: $("#tl-camera").value.trim() || undefined,
  };
}

function closeMobileNav() {
  const nav = $("#nav");
  if (nav.classList.contains("open")) {
    nav.classList.remove("open");
    $("#nav-toggle").setAttribute("aria-expanded", "false");
  }
}

function enterApp() {
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
  show("dashboard");
}

function leaveApp({ notice = null } = {}) {
  $("#app").classList.add("hidden");
  $("#login").classList.remove("hidden");
  resetLogin();
  if (notice) toast(notice, { tone: "warn", timeout: 6000 });
}

// ── boot ────────────────────────────────────────────────────────────────
async function boot() {
  // Session restore first (C-6): refresh-token survival across reload.
  const restored = hasSession() ? await restoreSession() : false;

  wireLogin($("#login-form"), enterApp);
  document.querySelectorAll("#nav button").forEach((b) =>
    b.addEventListener("click", () => show(b.dataset.view)));
  $("#logout").addEventListener("click", async () => { await apiLogout(); leaveApp(); });
  $("#nav-toggle").addEventListener("click", () => {
    const nav = $("#nav");
    const open = nav.classList.toggle("open");
    $("#nav-toggle").setAttribute("aria-expanded", String(open));
  });
  $("#ev-search").addEventListener("click", () => loadEvents($("#events-wrap"), { resetOffset: true }));
  wireEventsPager($("#events-wrap"));
  $("#tl-go").addEventListener("click", () => loadTimeline($("#timeline-out"), timelineParams()));
  wireEnrollForm($("#person-form"), $("#people-list"));
  $("#audit-load").addEventListener("click", () => loadAudit($("#audit-list")));

  // Session events: expire with a notice (never a silent dump — C-6).
  onAuthEvent((ev) => {
    if (ev.type === "auth:expired") leaveApp({ notice: "Session expired — please sign in again." });
    if (ev.type === "auth:restored") toast("Session renewed", { tone: "ok", timeout: 2000 });
  });

  if (restored) {
    enterApp();
    startRefreshLoop(); // proactive rotation before expiry
  } else {
    $("#login").classList.remove("hidden");
  }
}

boot();
