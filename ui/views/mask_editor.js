// Privacy-mask editor — "privacy by design" made visible (Wave 3).
//
// The camera's snapshot (GET /api/cameras/{id}/snapshot, JPEG) is the canvas.
// The operator drags rectangles; each becomes a normalized {x,y,w,h} mask
// with a REQUIRED reason (compliance field). Masks are what the detector
// skips — the preview shows exactly that.
//
// CSP notes: the canvas width/height are element PROPERTIES (legal), and the
// overlay uses SVG attributes for geometry — no inline styles anywhere.

import { h, render, svgEl } from "../core/dom.js";
import { api, ApiError, can } from "../core/api.js";
import { toast } from "../core/toast.js";
import { label } from "../core/format.js";

const REASONS = [
  "Public sidewalk (no consent)",
  "Neighbor's property",
  "Cash handling area",
  "Workplace privacy (GDPR Art. 5)",
  "Children's play area",
  "Other (typed below)",
];

const SVG_W = 640;
const SVG_H = 360;

/** Render the masks tab for a camera into `body`. */
export function maskEditor(body, cam) {
  const editable = can("camera:configure");
  let masks = (cam.privacy_masks || []).map(cloneMask);
  let draft = null; // {x,y,w,h,reason,custom} while drawing
  let dragStart = null;
  // <img> loads can't carry the in-memory bearer token; the API mints a
  // 300 s HMAC-signed URL for canvas loads (same scheme as event media).
  // Fetched ONCE — re-fetching inside renderStage would loop.
  let snapshotSrc = "";
  api(`/api/cameras/${cam.id}/snapshot-url`)
    .then((r) => { snapshotSrc = r.url; renderStage(); })
    .catch(() => { /* canvas without a frame — still editable */ });

  const statusEl = h("p", { class: "muted", "data-role": "mask-status" },
    "Loading camera snapshot…");
  const canvasWrap = h("div", { class: "mask-stage", "data-role": "stage" });
  const listEl = h("div", { class: "mask-list", "data-role": "mask-list" });

  function cloneMask(m) {
    return { x: m.x, y: m.y, w: m.w, h: m.h,
      reason: m.reason || "Unlabeled (legacy)" };
  }

  function svgPoint(evt) {
    const svg = canvasWrap.querySelector("svg");
    if (!svg) return null;
    const r = svg.getBoundingClientRect();
    const x = Math.min(1, Math.max(0, (evt.clientX - r.left) / r.width));
    const y = Math.min(1, Math.max(0, (evt.clientY - r.top) / r.height));
    return { x, y };
  }

  function renderStage() {
    // Snapshot <img> + SVG overlay. Geometry via attributes only.
    const rects = masks.map((m, i) => svgEl("rect", {
      x: m.x * SVG_W, y: m.y * SVG_H, width: m.w * SVG_W, height: m.h * SVG_H,
      class: "mask-rect", "data-mask": String(i),
      rx: 4,
    }, svgEl("title", {}, `${m.reason} — ${(m.w * 100).toFixed(0)}% × ${(m.h * 100).toFixed(0)}%`)));
    if (draft && draft.w >= 0.01 && draft.h >= 0.01) {
      rects.push(svgEl("rect", {
        x: draft.x * SVG_W, y: draft.y * SVG_H,
        width: draft.w * SVG_W, height: draft.h * SVG_H,
        class: "mask-rect draft", "data-mask": "draft", rx: 4,
      }));
    }
    // The snapshot <img> is created once and kept across redraws: the drag
    // path re-renders geometry many times a second — re-creating the img
    // would re-request the expiring signed URL (and re-fire onError/onLoad)
    // on every mousemove. Geometry re-renders into the stable SVG overlay.
    let img = canvasWrap.querySelector("img.mask-snapshot");
    if (!img && snapshotSrc) {
      img = h("img", {
        src: snapshotSrc,
        alt: `Snapshot from ${cam.name}`,
        class: "mask-snapshot",
        onError: (e) => { e.currentTarget.classList.add("hidden"); statusEl.textContent =
          "Snapshot unavailable (camera offline or unreachable) — masks still editable below on the plain canvas."; },
        onLoad: () => { if (statusEl.textContent.startsWith("Loading")) statusEl.textContent =
          "Drag to draw a mask over what this camera must NOT analyze. Each mask needs a reason (compliance record)."; },
      });
      canvasWrap.append(img);
    }
    let svg = canvasWrap.querySelector("[data-role='overlay']");
    if (!svg) {
      svg = svgEl("svg", {
        viewBox: `0 0 ${SVG_W} ${SVG_H}`,
        class: "mask-overlay",
        "data-role": "overlay",
        preserveAspectRatio: "none",
      });
      canvasWrap.append(svg);
    }
    render(svg, ...rects);
    renderList();
  }

  function renderList() {
    const rows = masks.map((m, i) => h("li", { class: "mask-row", "data-row": String(i) },
      h("span", { class: "mask-swatch", "aria-hidden": "true" }, ""),
      h("span", { class: "mask-desc" },
        h("strong", {}, m.reason),
        h("span", { class: "muted mono text-xs" },
          `${(m.w * 100).toFixed(0)}% × ${(m.h * 100).toFixed(0)}% at (${(m.x * 100).toFixed(0)}%, ${(m.y * 100).toFixed(0)}%)`)),
      editable ? h("button", {
        class: "ghost", "data-del": String(i),
        "aria-label": `Delete mask ${i + 1}`,
        onClick: () => { masks.splice(i, 1); renderStage(); },
      }, "Remove") : null,
    ));
    render(listEl, h("ul", { class: "mask-rows" },
      rows.length ? rows : h("li", { class: "muted" },
        "No masks yet — everything this camera sees is analyzed.")));
  }

  // ── drawing (pointer events on the overlay) ──────────────────────────
  const overlayWire = h("div", { class: "mask-editor", "data-editor": cam.id },
    h("div", { class: "card" },
      h("h3", {}, "Privacy masks — what the detector skips"),
      statusEl,
      canvasWrap,
      editable ? h("form", {
        class: "form-col mask-form", "data-form": "mask",
        onSubmit: async (e) => {
          e.preventDefault();
          if (!draft || draft.w < 0.01 || draft.h < 0.01) {
            return toast("Draw a rectangle on the snapshot first", { tone: "warn" });
          }
          const reasonSel = e.currentTarget.querySelector("[data-field=reason]").value;
          const custom = e.currentTarget.querySelector("[data-field=custom]").value.trim();
          const reason = reasonSel === REASONS[REASONS.length - 1]
            ? (custom || "Other") : reasonSel;
          masks.push({ ...draft, reason });
          draft = null;
          e.currentTarget.reset();
          renderStage();
        },
      },
        h("label", { class: "field-hint", for: "mask-reason" }, "Reason (required — compliance record)",
          h("select", { id: "mask-reason", "data-field": "reason", required: true },
            REASONS.map((r) => h("option", { value: r }, r)))),
        h("label", { class: "field-hint", for: "mask-custom" }, "If “Other”: one-line description",
          h("input", { id: "mask-custom", "data-field": "custom", placeholder: "e.g. reception desk screen" })),
        h("button", { class: "primary", type: "submit" }, "Add mask"),
      ) : null,
    ),
    h("div", { class: "card" },
      h("h3", {}, "Active masks"),
      listEl,
      editable ? h("button", {
        class: "primary", "data-act": "save-masks",
        onClick: async (e) => {
          const btn = e.currentTarget;
          btn.disabled = true;
          try {
            await api(`/api/cameras/${cam.id}`, {
              method: "PUT", body: JSON.stringify({ privacy_masks: masks }),
            });
            toast(`Saved ${masks.length} mask${masks.length === 1 ? "" : "s"}`, { tone: "ok" });
          } catch (err) {
            toast(err.message || "Save failed", { tone: "error" });
          } finally { btn.disabled = false; }
        },
      }, "Save masks to camera") : h("p", { class: "muted" },
        "Read-only for your role — masks apply but can't be changed here."),
    ),
  );

  render(body, overlayWire);
  renderStage();

  if (!editable) return;

  // Pointer drawing on the overlay (mouse + touch).
  const overlay = () => canvasWrap.querySelector("[data-role=overlay]");
  const setPointer = (on) => {
    const el = overlay();
    if (el) el.classList.toggle("drawing", on);
  };
  overlayWire.addEventListener("pointerdown", (e) => {
    if (!e.target.closest("[data-role=overlay]") || e.button !== 0) return;
    const p = svgPoint(e);
    if (!p) return;
    dragStart = p;
    draft = { x: p.x, y: p.y, w: 0, h: 0 };
    setPointer(true);
    e.preventDefault();
  });
  overlayWire.addEventListener("pointermove", (e) => {
    if (!dragStart) return;
    const p = svgPoint(e);
    if (!p) return;
    const x = Math.min(dragStart.x, p.x), y = Math.min(dragStart.y, p.y);
    draft = { x, y, w: Math.abs(p.x - dragStart.x), h: Math.abs(p.y - dragStart.y) };
    renderStage();
  });
  const endDrag = () => { if (dragStart) { dragStart = null; setPointer(false); renderStage(); } };
  overlayWire.addEventListener("pointerup", endDrag);
  overlayWire.addEventListener("pointerleave", endDrag);
}
