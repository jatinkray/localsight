// DVR scrub bar for the Live view (post-redesign capability, human-steered).
//
// Bakes NVR-style time-shifted playback into each live tile:
//   * drag the handle back into the archive → plays the recorded segment
//     covering that wall-clock time, seeking to the in-file offset
//   * release / click LIVE → destroys the playback source and resumes the
//     live LL-HLS stream in the same <video> element
//
// Data source: GET /api/cameras/{id}/recordings?start&end (segment map with
// signed 300 s playback URLs) + GET /api/cameras/{id}/recordings/at?t=
// (single-segment resolve for the dragged moment). Both require video:view.
//
// CSP notes (WAVE3_CONVENTIONS.md): geometry uses SVG x/width attributes,
// never inline styles; all DOM via h()/svgEl(); no innerHTML anywhere.
// <video> src swaps use signed URLs only — the same delivery scheme as the
// event drawer; the RTSP URL never reaches the client.

import { h, svgEl } from "../core/dom.js";
import { api, ApiError } from "../core/api.js";

const SCRUB_WINDOW_SEC = 60 * 60; // how far back the bar represents (1 h)
const TICK_UNIT_SEC = 15 * 60;    // 15-min tick marks
const LIVE_EDGE_SEC = 20;         // within this of "now" = still live

/** One scrubber instance per tile; owns its segment cache + media swap. */
export class DvrScrubber {
  constructor(cam, { videoEl, onPlaybackState } = {}) {
    this.cam = cam;
    this.videoEl = videoEl;
    this.onPlaybackState = onPlaybackState || (() => {});
    this.segments = [];            // [{start,end,startMs,endMs,duration_sec,url,id}]
    this.segmentsFetchedAt = 0;
    this.mode = "live";            // live | archive
    this.playingSegId = null;
    this._build();
  }

  // ── DOM ────────────────────────────────────────────────────────────────
  _build() {
    // SVG track: viewBox 0..1000 across the scrub window; x/width attributes
    // (CSP-safe). The handle is a <g> moved via transform attribute.
    this.svg = svgEl("svg", {
      class: "dvr-svg",
      viewBox: "0 0 1000 34",
      preserveAspectRatio: "none",
      role: "slider",
      "aria-label": `Scrub ${this.cam.name} back up to one hour`,
      "aria-valuemin": "0", "aria-valuemax": "1000", "aria-valuenow": "1000",
      "aria-valuetext": "live",
    });

    this.track = svgEl("rect", { class: "dvr-track", x: 0, y: 12, width: 1000, height: 10, rx: 3 });
    this.recLayer = svgEl("g", { class: "dvr-recordings" });
    this.tickLayer = svgEl("g", { class: "dvr-ticks" });
    this.handle = svgEl("g", { class: "dvr-handle" },
      svgEl("circle", { cx: 0, cy: 17, r: 7 }));
    this.readoutLine = svgEl("line", {
      class: "dvr-readout-line hidden", x1: 0, x2: 0, y1: 4, y2: 30,
    });
    this.svg.append(this.track, this.recLayer, this.tickLayer,
      this.readoutLine, this.handle);

    this.badge = h("button", {
      class: "dvr-badge live", type: "button",
      "aria-label": "Return to live", title: "Return to live",
    }, "LIVE");
    this.readout = h("span", { class: "dvr-readout mono muted hidden" }, "");
    this.timeLabel = h("span", { class: "dvr-timelabel muted mono" }, this._hhmmss(new Date()));

    // Speed control: 1×/2×/4×/8× during archive playback (hidden live).
    this.speedBtn = h("button", {
      class: "ghost dvr-speed hidden", type: "button",
      "aria-label": "Playback speed",
      onClick: () => this._cycleSpeed(),
    }, "1×");
    this._speeds = [1, 2, 4, 8];
    this._speedIdx = 0;

    this.el = h("div", { class: "dvr-bar", dataset: { camId: this.cam.id } },
      this.badge,
      h("div", { class: "dvr-svg-wrap" }, this.svg),
      this.readout,
      this.speedBtn,
      this.timeLabel,
    );

    // pointer events
    this.svg.addEventListener("pointerdown", (e) => this._onDragStart(e));
    this.svg.addEventListener("pointermove", (e) => this._onHover(e));
    this.svg.addEventListener("pointerleave", () => this._hideReadout());
    this.svg.addEventListener("keydown", (e) => this._onKey(e));
    this.badge.addEventListener("click", () => this.resumeLive());

    this._renderTimer = setInterval(() => this._tick(), 1000);
  }

  destroy() {
    clearInterval(this._renderTimer);
    clearTimeout(this._keyDebounce);
    this._playToken = (this._playToken || 0) + 1; // orphan any in-flight fetch
    this._resetToLiveSource?.();
  }

  // ── time mapping ───────────────────────────────────────────────────────
  get windowStartMs() { return Date.now() - SCRUB_WINDOW_SEC * 1000; }

  _xOf(ms) {
    const frac = (ms - this.windowStartMs) / (SCRUB_WINDOW_SEC * 1000);
    return Math.max(0, Math.min(1000, frac * 1000));
  }

  _msOfX(x) {
    const frac = Math.max(0, Math.min(1000, x)) / 1000;
    return this.windowStartMs + frac * SCRUB_WINDOW_SEC * 1000;
  }

  _hhmmss(d) {
    const p = (n) => String(n).padStart(2, "0");
    return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  }

  // ── segment data ───────────────────────────────────────────────────────
  async refreshSegments() {
    // Cached ~30 s: the scrub bar reloads on every Live-view paint and the
    // segment list is only interesting when it grows.
    if (Date.now() - this.segmentsFetchedAt < 30_000 && this.segments.length) return;
    try {
      const start = new Date(Date.now() - SCRUB_WINDOW_SEC * 1000).toISOString();
      const end = new Date().toISOString();
      const res = await api(
        `/api/cameras/${encodeURIComponent(this.cam.id)}/recordings?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`);
      // Signed URLs (300 s TTL) are refreshed with every fetch — a bar left
      // open longer than that refetches on the next drag anyway.
      this.segments = (res.segments || []).map((s) => ({
        id: s.id,
        startMs: new Date(s.start_ts).getTime(),
        endMs: new Date(s.end_ts).getTime(),
        duration_sec: s.duration_sec,
        url: s.url,
      }));
      this.segmentsFetchedAt = Date.now();
      this._drawRecordings();
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        // video:view missing: scrubber degrades to a live-only state.
        this.el.classList.add("dvr-denied");
      }
      // else: keep whatever we had; the bar is non-fatal chrome.
    }
  }

  _drawRecordings() {
    this.recLayer.replaceChildren();
    for (const s of this.segments) {
      const x = this._xOf(s.startMs);
      const w = Math.max(2, this._xOf(s.endMs) - x);
      this.recLayer.append(svgEl("rect", {
        class: "dvr-seg",
        x: Number(x.toFixed(2)), y: 12,
        width: Number(w.toFixed(2)), height: 10, rx: 2,
      }));
    }
    this._drawTicks();
  }

  _drawTicks() {
    this.tickLayer.replaceChildren();
    const firstTick = Math.ceil(this.windowStartMs / (TICK_UNIT_SEC * 1000)) * TICK_UNIT_SEC * 1000;
    for (let ms = firstTick; ms <= Date.now(); ms += TICK_UNIT_SEC * 1000) {
      this.tickLayer.append(svgEl("line", {
        x1: this._xOf(ms), x2: this._xOf(ms), y1: 8, y2: 12,
      }));
    }
  }

  // ── render loop ────────────────────────────────────────────────────────
  _tick() {
    this.timeLabel.textContent = this._hhmmss(new Date());
    if (this.mode === "live") {
      this._setHandleX(1000);
      this.svg.setAttribute("aria-valuenow", "1000");
      this.svg.setAttribute("aria-valuetext", "live");
    }
  }

  _setHandleX(x) {
    this.handle.setAttribute("transform", `translate(${x.toFixed(2)}, 0)`);
  }

  // ── interaction ────────────────────────────────────────────────────────
  _svgX(e) {
    const rect = this.svg.getBoundingClientRect();
    return ((e.clientX - rect.left) / rect.width) * 1000;
  }

  _onHover(e, { force = false } = {}) {
    if (this._dragging && !force) return;
    const ms = this._msOfX(this._svgX(e));
    const covered = this.segments.some((s) => ms >= s.startMs && ms <= s.endMs);
    this.readoutLine.classList.remove("hidden");
    const x = this._xOf(ms);
    this.readoutLine.setAttribute("x1", x);
    this.readoutLine.setAttribute("x2", x);
    this.readout.classList.remove("hidden");
    this.readout.textContent = `${this._hhmmss(new Date(ms))}${covered ? "" : " · no footage"}`;
  }

  _hideReadout() {
    if (this._dragging) return;
    this.readoutLine.classList.add("hidden");
    this.readout.classList.add("hidden");
  }

  _onDragStart(e) {
    e.preventDefault();
    this.svg.setPointerCapture?.(e.pointerId);
    this._dragging = true;
    this.svg.classList.add("dragging");

    const move = (ev) => {
      const x = this._svgX(ev);
      this._setHandleX(x);
      const ms = this._msOfX(x);
      this._onHover(ev, { force: true });
      this.svg.setAttribute("aria-valuenow", String(Math.round(x)));
      this.svg.setAttribute("aria-valuetext", this._hhmmss(new Date(ms)));
      this._pendingMs = ms;
    };
    const up = (ev) => {
      this._dragging = false;
      this.svg.classList.remove("dragging");
      this.svg.removeEventListener("pointermove", move);
      this.svg.removeEventListener("pointerup", up);
      this.svg.removeEventListener("pointercancel", up);
      const targetMs = this._pendingMs ?? this._msOfX(this._svgX(ev));
      this._pendingMs = null;
      const fromLive = Date.now() - targetMs <= LIVE_EDGE_SEC * 1000;
      if (fromLive) {
        this.resumeLive();
      } else {
        this.playAt(targetMs);
      }
    };
    this.svg.addEventListener("pointermove", move);
    this.svg.addEventListener("pointerup", up);
    this.svg.addEventListener("pointercancel", up);
    move(e); // show state under the initial press
  }

  _onKey(e) {
    // Keyboard scrub: 30 s steps, Home = live. The slider role carries the
    // a11y contract; arrow keys are the power path (WCAG 2.1 AA).
    const step = 30_000;
    let ms = this._pendingMs ?? Date.now();
    if (e.key === "ArrowLeft") { e.preventDefault(); ms -= step; }
    else if (e.key === "ArrowRight") { e.preventDefault(); ms += step; }
    else if (e.key === "Home" || e.key === "End") { e.preventDefault(); this.resumeLive(); return; }
    else return;
    ms = Math.max(this.windowStartMs, Math.min(Date.now(), ms));
    this._pendingMs = ms;
    this._setHandleX(this._xOf(ms));
    clearTimeout(this._keyDebounce);
    this._keyDebounce = setTimeout(() => { this._pendingMs = null; }, 600);
    const fromLive = Date.now() - ms <= LIVE_EDGE_SEC * 1000;
    if (fromLive) this.resumeLive();
    else this.playAt(ms);
  }

  // ── playback ───────────────────────────────────────────────────────────
  async playAt(ms) {
    const t = new Date(ms).toISOString();
    this.onPlaybackState("loading");
    // Request token: a newer drag / resume-live supersedes this fetch; only
    // the most recent request may own the element when its answer arrives.
    const token = (this._playToken = (this._playToken || 0) + 1);
    try {
      const seg = await api(
        `/api/cameras/${encodeURIComponent(this.cam.id)}/recordings/at?t=${encodeURIComponent(t)}`);
      if (token !== this._playToken) return; // superseded
      this._swapToArchive(seg);
    } catch (err) {
      if (token !== this._playToken) return;
      if (err instanceof ApiError && err.status === 404) {
        this.onPlaybackState("no-footage");
      } else {
        this.onPlaybackState("error");
      }
    }
  }

  _cycleSpeed() {
    this._speedIdx = (this._speedIdx + 1) % this._speeds.length;
    const speed = this._speeds[this._speedIdx];
    this.speedBtn.textContent = `${speed}×`;
    try { this.videoEl.playbackRate = speed; } catch { /* not playing */ }
  }

  _swapToArchive(seg) {
    this._teardownMedia();
    this.mode = "archive";
    this.playingSegId = seg.segment_id;
    this.badge.textContent = this._hhmmss(new Date(seg.start_ts));
    this.badge.classList.remove("live");
    this.badge.classList.add("archive");
    this.badge.title = "Return to live";
    this.badge.setAttribute("aria-label", "Return to live");

    const offset = Number(seg.seek_offset_sec || 0);
    this.videoEl.src = seg.url;
    const onMeta = () => {
      if (offset > 0) this.videoEl.currentTime = offset;
      this.videoEl.playbackRate = this._speeds[this._speedIdx];
      this.videoEl.play().catch(() => {});
    };
    this.videoEl.addEventListener("loadedmetadata", onMeta, { once: true });
    this._resetToLiveSource = () => {
      this.videoEl.removeEventListener("loadedmetadata", onMeta);
    };
    // Speed control only makes sense on a recording.
    this.speedBtn.classList.remove("hidden");
    this.onPlaybackState("archive");
  }

  resumeLive() {
    // Hand the element back to live HLS by full re-request of the tile's
    // ticket flow; the tile owner wires resumeLive into its startStream.
    this._playToken = (this._playToken || 0) + 1; // cancel in-flight archive fetches
    this._teardownMedia();
    this.mode = "live";
    this.playingSegId = null;
    this._setHandleX(1000);
    this._speedIdx = 0;
    this.speedBtn.textContent = "1×";
    this.speedBtn.classList.add("hidden");
    try { this.videoEl.playbackRate = 1; } catch { /* not playing */ }
    this.badge.textContent = "LIVE";
    this.badge.classList.add("live");
    this.badge.classList.remove("archive");
    this.badge.title = "";
    this.badge.setAttribute("aria-label", "Currently live");
    this.onPlaybackState("live");
    this._resumeLive?.();
  }

  /** Tile owner injects the live-resume callback (ticket flow restart). */
  setLiveResume(fn) { this._resumeLive = fn; }

  /** Tile owner injects the live-teardown callback (destroys hls.js /
   * clears the manifest src) so archive playback owns the <video> cleanly. */
  setLiveTeardown(fn) { this._teardownLive = fn; }

  _teardownMedia() {
    try { this.videoEl.pause(); } catch { /* not playing */ }
    this._resetToLiveSource?.();
    this._resetToLiveSource = null;
    this._teardownLive?.();
  }
}
