// Privacy & data — the trust surface made visible (Wave 3).
//
// Every claim on this screen is verifiable in the codebase: envelope
// encryption (packages/security/crypto.py), per-camera retention
// (Camera.retention + the worker's sweep), audit trail (apps/api/audit.py).
// Where a number isn't derivable from the API, we say what IS true instead
// of inventing theater.

import { h, render } from "../core/dom.js";
import { api, can } from "../core/api.js";
import { skeletonRows, errorState } from "../core/states.js";
import { toast } from "../core/toast.js";
import { navigate } from "../core/router.js";
import { isTelemetryOn, setTelemetry, exportTelemetry, telemetrySnapshot }
  from "../core/telemetry.js";

export async function loadPrivacy(listEl) {
  skeletonRows(listEl, 5);
  let cams, people;
  try {
    [cams, people] = await Promise.all([
      api("/api/cameras"),
      api("/api/persons"),
    ]);
  } catch (err) {
    return render(listEl, errorState(err, { noun: "privacy data",
      onRetry: () => loadPrivacy(listEl) }));
  }
  cams = Array.isArray(cams) ? cams : [];
  people = Array.isArray(people) ? people : [];

  const masks = cams.map((c) => ({ cam: c, masks: c.privacy_masks || [] }));
  const totalMasks = masks.reduce((n, m) => n + m.masks.length, 0);
  const bareCams = masks.filter((m) => !m.masks.length).length;

  render(listEl, h("div", { class: "cam-detail", "data-view-root": "privacy" }, [
    dataLivesCard(),
    retentionCard(cams),
    maskInventoryCard(cams, masks, totalMasks, bareCams),
    erasureCard(people),
    telemetryCard(),
  ]));
}

function dataLivesCard() {
  return h("div", { class: "card" },
    h("h3", {}, "Where your data lives"),
    h("p", {},
      "All processing and storage happens on this host. No cloud services receive your video, faces or credentials. Camera stream URLs and face embeddings are encrypted at rest; exports are recorded in the audit trail."),
    h("dl", { class: "kv-grid" },
      h("dt", {}, "Encryption"), h("dd", {}, "AES envelope — master key never leaves this host"),
      h("dt", {}, "Cloud dependency"), h("dd", {}, "None"),
      h("dt", {}, "Audit trail"), h("dd", {}, "Every export and configuration change is recorded"),
      h("dt", {}, "Video processing"), h("dd", {}, "Local — cameras never upload anywhere")));
}

function retentionCard(cams) {
  const rows = cams.length
    ? cams.map((c) => {
        const days = c.retention && c.retention.days;
        return h("li", { class: "mask-row" },
          h("button", {
            class: "linklike",
            onClick: () => navigate("cameras", { id: c.id, tab: "retention" }),
          }, c.name),
          h("span", { class: "mask-desc" },
            h("strong", {}, days ? `${days} days` : "system default"),
            h("span", { class: "muted text-xs" },
              days ? "before the sweep deletes this camera's recordings, events and snapshots"
                : "no override — sweep uses the deployed defaults")));
      })
    : [h("li", { class: "muted" }, "No cameras configured.")];
  return h("div", { class: "card" },
    h("h3", {}, "Retention by camera"),
    h("p", { class: "muted" },
      "The worker's retention sweep deletes each camera's recordings, events and snapshots once they age past its window. Default windows are set per camera by the operator."),
    h("ul", { class: "mask-rows" }, rows));
}

function maskInventoryCard(cams, masks, totalMasks, bareCams) {
  const masked = masks.filter((m) => m.masks.length);
  const rows = totalMasks
    ? masked.map((m) => {
        const reasons = m.masks.map((x) => x.reason).filter(Boolean);
        return h("li", { class: "mask-row", "data-masked-cam": m.cam.name },
          h("button", {
            class: "linklike",
            onClick: () => navigate("cameras", { id: m.cam.id, tab: "masks" }),
          }, m.cam.name),
          h("span", { class: "mask-desc" },
            h("strong", {}, `${m.masks.length} mask${m.masks.length === 1 ? "" : "s"}`),
            h("span", { class: "muted text-xs" },
              reasons.length
                ? reasons.slice(0, 2).join(", ") + (reasons.length > 2 ? " …" : "")
                : "no reasons recorded (legacy)")));
      })
    : [h("li", { class: "muted" },
        bareCams
          ? `${bareCams} camera${bareCams === 1 ? "" : "s"} analyze${bareCams === 1 ? "s" : ""} their full field of view.`
          : "No cameras yet.")];
  return h("div", { class: "card", "data-mask-total": String(totalMasks) },
    h("h3", {}, "Privacy masks across cameras"),
    h("p", { class: "muted" },
      "Masks are what the detector skips: detections whose center falls inside a mask are dropped before tracking — the frames are never analyzed."),
    h("ul", { class: "mask-rows" }, rows),
    totalMasks && bareCams
      ? h("p", { class: "muted text-xs" },
          `${bareCams} of ${cams.length} cameras analyze their full field of view.`)
      : null);
}

/** Opt-in UI marks (Wave 5): a LOCAL ring buffer, off by default, exported
 *  as a file the user keeps. No network. No storage. Honest by design. */
function telemetryCard() {
  const body = h("dl", { class: "kv-grid", "data-role": "telemetry-body" });
  const paint = () => {
    const snap = telemetrySnapshot();
    render(body,
      h("dt", {}, "UI marks"),
      h("dd", {}, `${snap.count} recorded — view loads, slow views, page errors`),
      h("dt", {}, "Storage"),
      h("dd", {}, "memory only, this tab; nothing is stored or sent"),
      h("dt", {}, "Export"),
      h("dd", {}, h("button", {
        class: "ghost", "data-act": "telemetry-export",
        onClick: () => { exportTelemetry(); toast("Marks downloaded — the file is yours", { tone: "ok" }); },
      }, "Download JSON")));
  };
  paint();

  const toggle = h("button", {
    class: "ghost", "data-act": "telemetry-toggle",
    "aria-pressed": String(isTelemetryOn()),
    onClick: () => {
      setTelemetry(!isTelemetryOn());
      toggle.setAttribute("aria-pressed", String(isTelemetryOn()));
      toggle.textContent = isTelemetryOn() ? "Turn off" : "Turn on";
      paint();
    },
  }, isTelemetryOn() ? "Turn off" : "Turn on");

  return h("div", { class: "card" },
    h("h3", {}, "UI marks (opt-in)"),
    h("p", { class: "muted" },
      "Optional, local-only diagnostics: which views load slowly, whether errors happen. ",
      "Disabled unless you turn it on; the data never leaves this browser tab, and the download is a file you keep."),
    toggle,
    body);
}

function erasureCard(people) {
  const results = h("ul", { class: "mask-rows", "data-role": "erase-results" });
  const input = h("input", {
    "data-field": "person-search", class: "mono", autocomplete: "off",
    placeholder: "Search a person by label or name",
    "aria-label": "Search a person" });
  const card = h("div", { class: "card" },
    h("h3", {}, "Data-subject erasure (GDPR)"),
    h("p", { class: "muted" },
      "Erasing a person permanently deletes their record and every face embedding. Surveillance events remain — they were only ever linked to an anonymous track id, never to a name."),
    h("div", { class: "form-row" },
      input,
      h("button", {
        class: "ghost", "data-act": "person-search",
        onClick: () => search(),
      }, "Search")),
    results);
  render(results, people.length
    ? [h("li", { class: "muted" },
        `${people.length} enrolled — search to narrow down.`)]
    : [h("li", { class: "muted" }, "No identities enrolled.")]);

  const search = () => {
    const q = input.value.trim().toLowerCase();
    const hits = q
      ? people.filter((p) => p.label.toLowerCase().includes(q)
        || (p.display_name || "").toLowerCase().includes(q))
      : people;
    render(results, hits.length
      ? hits.map((p) => eraseRow(p))
      : [h("li", { class: "muted" }, "No match.")]);
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); search(); } });
  return card;
}

function eraseRow(p) {
  const row = h("li", { class: "mask-row", "data-person": p.label },
    h("span", { class: "mask-desc" },
      h("strong", { class: "mono" }, p.label),
      h("span", { class: "muted text-xs" },
        ` ${p.display_name || ""} · ${p.faces_enrolled ?? 0} embedding${(p.faces_enrolled ?? 0) === 1 ? "" : "s"}`.trim())),
    can("person:delete")
      ? h("button", {
          class: "ghost", "data-act": "erase-person",
          onClick: (e) => {
            const zone = h("span", { class: "confirm-zone" },
              h("span", { class: "muted text-xs" },
                "Erasure is permanent: the person and all their embeddings are deleted. Type the label to confirm."),
              h("input", {
                "data-field": "erase-label", class: "mono", autocomplete: "off",
                placeholder: p.label, "aria-label": "Type the person's label to confirm" }),
              h("span", { class: "form-row" },
                h("button", {
                  class: "ghost", "data-act": "erase-confirm", disabled: true,
                  onClick: async () => {
                    const btn = row.querySelector("[data-act=erase-confirm]");
                    btn.disabled = true;
                    try {
                      await api(`/api/persons/${p.id}`, { method: "DELETE" });
                      toast(`${p.label} erased`, { tone: "ok" });
                      navigate("privacy");
                    } catch (err) {
                      toast(err.message, { tone: "error" });
                      btn.disabled = false;
                    }
                  },
                }, "Erase permanently"),
                h("button", {
                  class: "ghost",
                  onClick: (ev) => { zone.replaceWith(row.querySelector("[data-act=erase-person]") || row); },
                }, "Cancel")));
            zone.addEventListener("input", () => {
              const i = zone.querySelector("[data-field=erase-label]");
              zone.querySelector("[data-act=erase-confirm]").disabled =
                i.value.trim() !== p.label;
            });
            e.currentTarget.replaceWith(zone);
            zone.querySelector("input").focus();
          },
        }, "Erase…")
      : h("span", { class: "muted text-xs" }, "erasure requires person:delete"));
  return row;
}
