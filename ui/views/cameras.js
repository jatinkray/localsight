// Cameras — from read-only table to management (Wave 3).
//
// List: card grid (thumbnail, name, status, resolution/fps, health,
// quick actions Live/Rules/Masks). Detail: tabs — Streams, Privacy Masks,
// Rules, Retention, Health. The route params decide which renders:
//   #/cameras                -> card grid
//   #/cameras?id=…&tab=masks -> detail on the Masks tab

import { h, render } from "../core/dom.js";
import { api, ApiError, can } from "../core/api.js";
import { skeletonCards, skeletonRows, emptyState, errorState } from "../core/states.js";
import { toast } from "../core/toast.js";
import { label, tone, shortId } from "../core/format.js";
import { navigate } from "../core/router.js";
import { maskEditor } from "./mask_editor.js";
import { rulesEditor } from "./rules_editor.js";

const TABS = [
  { id: "streams", label: "Streams", perm: "camera:view" },
  { id: "masks", label: "Privacy masks", perm: "camera:view" },
  { id: "rules", label: "Rules", perm: "rules:configure" },
  { id: "retention", label: "Retention", perm: "camera:configure" },
  { id: "health", label: "Health", perm: "camera:view" },
];

export async function loadCameras(outEl, params = {}) {
  if (params.id) return loadCameraDetail(outEl, params);
  return loadCameraGrid(outEl);
}

// ── card grid ────────────────────────────────────────────────────────────

function cameraCard(c) {
  const actions = [
    h("button", {
      class: "ghost",
      "data-act": "live",
      onClick: () => navigate("live", { camera: c.id }),
    }, "Live"),
  ];
  if (can("camera:configure")) {
    actions.push(
      h("button", {
        class: "ghost",
        "data-act": "masks",
        onClick: () => navigate("cameras", { id: c.id, tab: "masks" }),
      }, "Masks"),
      h("button", {
        class: "ghost",
        "data-act": "rules",
        onClick: () => navigate("cameras", { id: c.id, tab: "rules" }),
      }, "Rules"),
    );
  }
  return h("article", { class: "cam-card", "data-cam": c.name },
    h("div", { class: "cam-card-head" },
      h("div", {},
        h("button", {
          class: "linklike cam-name",
          "data-act": "detail",
          onClick: () => navigate("cameras", { id: c.id }),
        }, c.name),
        h("div", { class: "muted mono text-xs" }, shortId(c.id)),
      ),
      h("span", { class: `pill ${tone(c.status)}` },
        h("span", { class: `dot ${tone(c.status)}`, "aria-hidden": "true" }, ""),
        label(c.status)),
    ),
    h("dl", { class: "cam-card-meta" },
      h("div", {}, h("dt", {}, "Resolution"), h("dd", { class: "mono" }, c.resolution || "—")),
      h("div", {}, h("dt", {}, "Frame rate"), h("dd", { class: "mono" }, c.fps ? `${c.fps} fps` : "—")),
      h("div", {}, h("dt", {}, "Health"), h("dd", {}, label(c.health || "unknown"))),
      h("div", {}, h("dt", {}, "Masks"), h("dd", { class: "mono" },
        `${(c.privacy_masks || []).length} active`)),
    ),
    h("div", { class: "cam-card-actions" }, actions),
  );
}

async function loadCameraGrid(outEl) {
  skeletonCards(outEl, 5);
  try {
    const cams = await api("/api/cameras");
    const list = Array.isArray(cams) ? cams : cams.items || [];
    if (!list.length) {
      render(outEl, emptyState({
        icon: "◉", title: "No cameras configured",
        hint: "Add your first camera — ONVIF discovery finds most NVRs and cams in seconds.",
        action: can("camera:configure") ? h("button", {
          class: "primary", "data-act": "add-camera",
          onClick: () => navigate("cameras", { id: "new" }),
        }, "Add camera") : null,
      }));
      return;
    }
    render(outEl, h("div", { class: "cam-grid" }, list.map(cameraCard)));
  } catch (err) {
    render(outEl, errorState(err, { noun: "cameras", onRetry: () => loadCameraGrid(outEl) }));
  }
}

// ── detail ───────────────────────────────────────────────────────────────

async function loadCameraDetail(outEl, params) {
  if (params.id === "new") {
    const { cameraWizard } = await import("./camera_wizard.js");
    return cameraWizard(outEl, () => navigate("cameras"));
  }
  skeletonRows(outEl, 6);
  let cam;
  try {
    cam = await api(`/api/cameras/${params.id}`);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      render(outEl, emptyState({
        icon: "◎", title: "Camera not found",
        hint: "It may have been deleted. Back to the camera grid.",
        action: h("button", { class: "ghost", onClick: () => navigate("cameras") }, "All cameras"),
      }));
      return;
    }
    render(outEl, errorState(err, { noun: "camera", onRetry: () => loadCameraDetail(outEl, params) }));
    return;
  }

  const visible = TABS.filter((t) => can(t.perm));
  const tab = visible.some((t) => t.id === params.tab) ? params.tab : visible[0].id;
  const el = h("div", { class: "cam-detail", "data-cam-id": cam.id },
    h("div", { class: "panel-head" },
      h("div", {},
        h("button", { class: "linklike", onClick: () => navigate("cameras") }, "Cameras"),
        h("h2", {}, cam.name),
        h("div", { class: "muted mono text-xs" }, shortId(cam.id)),
      ),
      h("span", { class: `pill ${tone(cam.status)}` },
        h("span", { class: `dot ${tone(cam.status)}`, "aria-hidden": "true" }, ""),
        label(cam.status)),
    ),
    h("div", { class: "tabs", role: "tablist", "aria-label": "Camera settings" },
      visible.map((t) => h("button", {
        class: t.id === tab ? "active" : "",
        role: "tab",
        "aria-selected": String(t.id === tab),
        "data-tab": t.id,
        onClick: () => navigate("cameras", { id: cam.id, tab: t.id }),
      }, t.label))),
    h("div", { class: "cam-detail-body", "data-tab-body": tab }),
  );
  render(outEl, el);
  const body = el.querySelector("[data-tab-body]");
  // A tab renders into a FRESH body — switching tabs must not stack the
  // previous tab's editors underneath (mask editor + rules editor both
  // draw on overlays; two live editors on one page is a bug, not a feature).
  body.replaceChildren();
  if (tab === "streams") renderStreams(body, cam);
  else if (tab === "masks") maskEditor(body, cam);
  else if (tab === "rules") {
    // rules live on their own endpoint; GET /api/cameras/{id} omits them.
    // Legacy seed data could hold a non-list shape — normalize honestly:
    // only array-shaped rule lists are editable; anything else is shown
    // as a read-only warning rather than silently mangled.
    try {
      const rules = await api(`/api/cameras/${cam.id}/rules`);
      const raw = rules.rules;
      const isList = Array.isArray(raw);
      rulesEditor(body, { ...cam, rules: isList ? raw : [],
        legacyRules: isList ? null : raw });
    } catch (err) {
      render(body, errorState(err, { noun: "rules",
        onRetry: () => loadCameraDetail(outEl, params) }));
    }
  }
  else if (tab === "retention") renderRetention(body, cam);
  else if (tab === "health") renderHealth(body, cam);
}

// Streams: URLs are encrypted write-only — show "configured", never the value.
function renderStreams(body, cam) {
  const editable = can("camera:configure");
  body.append(h("div", { class: "card" },
    h("h3", {}, "Stream endpoints"),
    h("p", { class: "muted" },
      "Stream URLs are encrypted at rest and never displayed. Replace by entering a new value; leave blank to keep."),
    editable ? h("form", {
      class: "form-col", "data-form": "streams",
      onSubmit: async (e) => {
        e.preventDefault();
        const form = e.currentTarget;
        const btn = form.querySelector("button[type=submit]");
        const main = form.querySelector("[data-field=main]").value.trim();
        const sub = form.querySelector("[data-field=sub]").value.trim();
        const name = form.querySelector("[data-field=name]").value.trim();
        const patch = {};
        if (name && name !== cam.name) patch.name = name;
        if (main) patch.stream_url = main;
        if (sub) patch.substream_url = sub;
        if (!Object.keys(patch).length) return toast("Nothing to update", { tone: "info" });
        btn.disabled = true;
        try {
          await api(`/api/cameras/${cam.id}`, { method: "PUT", body: JSON.stringify(patch) });
          toast("Camera updated", { tone: "ok" });
          form.reset();
          navigate("cameras", { id: cam.id, tab: "streams" });
        } catch (err) {
          toast(err.message || "Update failed — check the URL", { tone: "error", timeout: 6000 });
        } finally { btn.disabled = false; }
      },
    },
      h("label", { class: "field-hint", for: `nm-${cam.id}` }, "Name",
        h("input", { id: `nm-${cam.id}`, "data-field": "name", value: cam.name || "" })),
      h("label", { class: "field-hint", for: `su-${cam.id}` },
        "Main stream URL (rtsp://…, encrypted on save)",
        h("input", { id: `su-${cam.id}`, "data-field": "main", class: "mono",
          type: "password", autocomplete: "new-password", placeholder: "leave blank to keep current" })),
      h("label", { class: "field-hint", for: `ss-${cam.id}` },
        "Sub stream URL (used for snapshots and low-bandwidth live)",
        h("input", { id: `ss-${cam.id}`, "data-field": "sub", class: "mono",
          type: "password", autocomplete: "new-password", placeholder: "leave blank to keep current" })),
      h("button", { class: "primary", type: "submit" }, "Save changes"),
    ) : h("p", { class: "muted" }, "Your role can view this camera but not change it."),
    h("dl", { class: "cam-card-meta" },
      h("div", {}, h("dt", {}, "Main stream"), h("dd", { class: "muted" }, "🔒 encrypted — write-only")),
      h("div", {}, h("dt", {}, "Sub stream"), h("dd", { class: "muted" },
        cam.substream_configured === false ? "not set" : "🔒 encrypted — write-only")),
    ),
  ));
}

// Retention: per-camera override on the same PUT.
function renderRetention(body, cam) {
  const editable = can("camera:configure");
  const days = (cam.retention && cam.retention.days) || "";
  body.append(h("div", { class: "card" },
    h("h3", {}, "Retention"),
    h("p", { class: "muted" },
      "How long this camera's recordings, events and snapshots are kept before the retention sweep deletes them. Leave empty to use the system default."),
    editable ? h("form", {
      class: "form-col", "data-form": "retention",
      onSubmit: async (e) => {
        e.preventDefault();
        const input = e.currentTarget.querySelector("[data-field=days]");
        const btn = e.currentTarget.querySelector("button[type=submit]");
        const v = input.value.trim();
        const patch = v === "" ? { retention: null } : { retention: { days: Math.max(1, parseInt(v, 10) || 0) } };
        btn.disabled = true;
        try {
          await api(`/api/cameras/${cam.id}`, { method: "PUT", body: JSON.stringify(patch) });
          toast("Retention updated", { tone: "ok" });
        } catch (err) {
          toast(err.message || "Update failed", { tone: "error" });
        } finally { btn.disabled = false; }
      },
    },
      h("label", { class: "field-hint", for: `rt-${cam.id}` }, "Days (blank = system default)",
        h("input", { id: `rt-${cam.id}`, "data-field": "days", class: "mono",
          type: "number", min: "1", value: String(days) })),
      h("button", { class: "primary", type: "submit" }, "Save retention"),
    ) : h("p", { class: "muted" }, "Read-only for your role."),
  ));
}

function renderHealth(body, cam) {
  body.append(h("div", { class: "card" },
    h("h3", {}, "Health"),
    h("dl", { class: "cam-card-meta" },
      h("div", {}, h("dt", {}, "Status"), h("dd", {}, label(cam.status))),
      h("div", {}, h("dt", {}, "Health"), h("dd", {}, label(cam.health || "unknown"))),
      h("div", {}, h("dt", {}, "Last seen"), h("dd", {}, cam.last_seen
        ? new Date(cam.last_seen).toLocaleString() : "never")),
      h("div", {}, h("dt", {}, "Timezone"), h("dd", { class: "mono" }, cam.timezone || "UTC")),
    ),
    h("p", { class: "muted text-xs" },
      "Health history charts arrive with the Wave-4 analytics pass; today this is the live truth."),
  ));
}
