// Live View — the operator's home screen (Wave 2).
//
// Grid of camera tiles (1×1 / 2×2 / 3×3), each with the ticket flow from
// the secure live API: POST /api/live/ticket → GET /api/live/{id}/play →
// <video> plays the LL-HLS manifest under /live-media/. Tickets are
// camera-scoped, encrypted, and short-lived; the client re-issues on 401
// (expired ticket) exactly like the session refresh.
//
// Honest states everywhere (no permanently-dead players):
//  - 503 from /play = the transcode couldn't start (no ffmpeg / camera
//    unreachable). Shown as an explicit offline state with a Retry.
//  - native HLS in Safari; hls.js (vendored, CSP 'self') in Chromium,
//    lazy-loaded only when this view is first opened.
//
// Wall mode: fullscreen auto-cycle for NOC displays. F toggles, arrows
// step tiles, Esc exits.

import { h, render } from "../core/dom.js";
import { api, ApiError, can } from "../core/api.js";
import { fmtRelative, label, tone } from "../core/format.js";
import { emptyState, errorState } from "../core/states.js";

const LAYOUTS = [1, 2, 3]; // 1×1, 2×2, 3×3
const WALL_CYCLE_MS = 8000;

let layout = Number(sessionStorage.getItem("lv-layout")) || 2;
let wallMode = false;
let wallTimer = null;
let activeTile = -1;
let hlsLibPromise = null;

/** Lazy-load vendored hls.js only when Live View opens (plan §III.9). */
async function ensureHls() {
  if (hlsLibPromise) return hlsLibPromise;
  hlsLibPromise = new Promise((resolve) => {
    if (window.Hls) return resolve(window.Hls);
    const s = document.createElement("script");
    s.src = "/vendor/hls.light.min.js";
    s.onload = () => resolve(window.Hls || null);
    s.onerror = () => resolve(null);
    document.head.append(s);
  });
  return hlsLibPromise;
}

function tileClass() {
  // CSS grid columns driven by a class, never inline styles (CSP).
  return `live-grid cols-${layout}`;
}

async function startStream(cam, videoEl, stateEl) {
  // Ticket flow: issue (audited server-side) → exchange for manifest.
  stateEl.textContent = "Requesting stream…";
  try {
    const t = await api("/api/live/ticket", {
      method: "POST",
      body: JSON.stringify({ camera_id: cam.id, ttl_sec: 300 }),
    });
    const play = await api(`/api/live/${encodeURIComponent(cam.id)}/play?ticket=${encodeURIComponent(t.ticket)}`);
    const src = play.hls_manifest;
    stateEl.textContent = "";

    if (videoEl.canPlayType("application/vnd.apple.mpegurl")) {
      videoEl.src = src; // Safari: native HLS
      await videoEl.play().catch(() => {});
      return;
    }
    const Hls = await ensureHls();
    if (!Hls || !Hls.isSupported()) {
      stateEl.textContent = "This browser can't play HLS.";
      return;
    }
    const hls = new Hls({ liveDurationInfinity: true, backBufferLength: 30 });
    hls.on(Hls.Events.ERROR, (_e, data) => {
      if (data.fatal) {
        stateEl.textContent = "Stream interrupted";
        hls.destroy();
      }
    });
    hls.loadSource(src);
    hls.attachMedia(videoEl);
    hls.on(Hls.Events.MANIFEST_PARSED, () => videoEl.play().catch(() => {}));
    videoEl._hls = hls;
  } catch (err) {
    if (err instanceof ApiError && err.status === 503) {
      stateEl.textContent = "Stream unavailable";
      videoEl.closest(".live-tile")?.classList.add("offline");
    } else if (err instanceof ApiError && err.status === 404) {
      stateEl.textContent = "Camera not found";
    } else {
      stateEl.textContent = "Couldn't start stream";
    }
  }
}

function stopStream(videoEl) {
  if (videoEl._hls) { videoEl._hls.destroy(); videoEl._hls = null; }
  videoEl.removeAttribute("src");
  try { videoEl.load(); } catch { /* not loaded */ }
}

async function stopRemote(cam) {
  try {
    await api(`/api/live/${encodeURIComponent(cam.id)}/stop`, { method: "POST" });
  } catch { /* the tile state is local regardless */ }
}

function liveTile(cam, idx) {
  const video = h("video", {
    class: "live-video", muted: true, playsinline: true,
    "aria-label": `Live stream: ${cam.name}`,
    onClick: () => focusTile(idx),
  });
  const stateEl = h("span", { class: "live-state" }, "");
  const statusDot = h("span", {
    class: `dot ${cam.status === "online" ? "ok" : cam.status === "degraded" ? "warn" : "crit"}`,
    "aria-hidden": "true",
  });

  const tile = h("div", { class: "live-tile", dataset: { camId: cam.id, idx: String(idx) } },
    h("div", { class: "live-video-wrap" }, video, stateEl),
    h("div", { class: "live-tile-bar" },
      statusDot,
      h("span", { class: "live-tile-name" }, cam.name),
      h("span", { class: "live-tile-actions" },
        h("button", {
          class: "ghost live-stop", type: "button", "aria-label": `Stop stream for ${cam.name}`,
          onClick: async (e) => {
            e.stopPropagation();
            stopStream(video);
            await stopRemote(cam);
            stateEl.textContent = "Stopped";
          },
        }, "■"),
      ),
    ),
  );
  return { tile, video, stateEl };
}

function focusTile(delta = null) {
  const tiles = [...document.querySelectorAll(".live-tile")];
  if (!tiles.length) return;
  if (delta != null) {
    activeTile = (activeTile + delta + tiles.length) % tiles.length;
  } else if (activeTile < 0) {
    activeTile = 0;
  }
  tiles.forEach((t, i) => t.classList.toggle("focused", i === activeTile));
}

function startWall() {
  wallMode = true;
  document.body.classList.add("wall-mode");
  const grid = document.getElementById("live-grid");
  if (grid && grid.requestFullscreen) grid.requestFullscreen().catch(() => {});
  const advance = () => {
    focusTile(1);
    const tiles = [...document.querySelectorAll(".live-tile")];
    const t = tiles[activeTile];
    if (t) t.scrollIntoView({ block: "nearest" });
  };
  advance();
  wallTimer = setInterval(advance, WALL_CYCLE_MS);
}

function stopWall() {
  wallMode = false;
  document.body.classList.remove("wall-mode");
  clearInterval(wallTimer);
  wallTimer = null;
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  focusTile(null);
}

export function wireLiveView(gridEl, toolbarEl) {
  // Event delegation: the toolbar holds several [data-cols] buttons.
  toolbarEl.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-cols]");
    if (!btn) return;
    const cols = Number(btn.dataset.cols);
    if (!LAYOUTS.includes(cols)) return;
    layout = cols;
    sessionStorage.setItem("lv-layout", String(cols));
    loadLive(gridEl);
  });
  toolbarEl.querySelector('[data-role="wall"]').addEventListener("click", () =>
    wallMode ? stopWall() : startWall());

  document.addEventListener("keydown", (e) => {
    const panel = document.querySelector('[data-panel="live"]');
    if (!panel || panel.classList.contains("hidden")) return;
    if (e.key === "f" || e.key === "F") { e.preventDefault(); wallMode ? stopWall() : startWall(); }
    if (e.key === "ArrowRight") { e.preventDefault(); focusTile(1); }
    if (e.key === "ArrowLeft") { e.preventDefault(); focusTile(-1); }
    if (e.key === "Escape" && wallMode) stopWall();
  });
}

export async function loadLive(gridEl) {
  if (!can("live:view")) {
    render(gridEl, emptyState({
      icon: "◎", title: "Live view unavailable",
      hint: "Your role doesn't include live viewing.",
    }));
    return;
  }
  render(gridEl, h("div", { class: "skeleton skeleton-card" }));
  let cams;
  try {
    cams = await api("/api/cameras");
  } catch (err) {
    render(gridEl, errorState(err, { noun: "cameras", onRetry: () => loadLive(gridEl) }));
    return;
  }
  const list = (Array.isArray(cams) ? cams : cams.items || []);
  if (!list.length) {
    render(gridEl, emptyState({
      icon: "◎", title: "No cameras yet",
      hint: "Live view appears once cameras are added.",
    }));
    return;
  }

  const grid = h("div", { id: "live-grid", class: tileClass() });
  const tiles = list.map((c, i) => liveTile(c, i));
  tiles.forEach(({ tile }) => grid.append(tile));
  render(gridEl, [
    grid,
    h("p", { class: "muted live-hint" },
      "Streams auto-stop after idle; tickets are per-camera, encrypted, and audited."),
  ]);

  // Start streams only for visible tiles (3×3 starts all; wall focus starts one).
  tiles.forEach(({ video, stateEl }, i) => {
    const cam = list[i];
    startStream(cam, video, stateEl);
  });

  // Reaper awareness: poll active streams and surface idle age on tiles.
  refreshStreamMeta(grid);
}

async function refreshStreamMeta(grid) {
  try {
    const meta = await api("/api/live/streams");
    const byId = new Map((meta.active || []).map((a) => [a.camera_id, a]));
    grid.querySelectorAll(".live-tile").forEach((t) => {
      const a = byId.get(t.dataset.camId);
      const badge = t.querySelector(".live-meta");
      if (badge) {
        badge.textContent = a ? `idle ${a.idle_sec}s` : "";
      }
    });
  } catch { /* metadata is a nice-to-have; player states carry the truth */ }
}
