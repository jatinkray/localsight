// Add Camera wizard — Discover → Select & Credentials → Verify (Wave 3).
//
// First-run onboarding: the cameras empty state links here (id "new").
// Steps: (1) ONVIF WS-Discovery on the LAN (or skip to manual entry),
// (2) pick a device + enter credentials (password write-only; the SSRF
// guard 400s unsafe addresses and the reason is surfaced verbatim),
// (3) verify by fetching one snapshot through the create API — the same
// endpoint the mask editor uses — then land on the new camera's detail.

import { h, render } from "../core/dom.js";
import { api, ApiError } from "../core/api.js";
import { toast } from "../core/toast.js";
import { navigate } from "../core/router.js";

const STEPS = ["Discover", "Select & credentials", "Verify"];

export async function cameraWizard(outEl, onDone) {
  let step = 0;
  let devices = []; // discovered xaddrs
  let picked = null; // {xaddr, uris: []}
  let created = null; // camera id from step 2
  let busy = false; // double-submit guard across steps

  const head = h("div", { class: "panel-head" },
    h("div", {},
      h("button", { class: "linklike", onClick: () => onDone() }, "Cameras"),
      h("h2", {}, "Add camera"),
    ));

  const stepsBar = h("ol", { class: "wz-steps", "data-role": "steps" });
  const bodyEl = h("div", { class: "card wz-body", "data-role": "wz-body" });

  function renderSteps() {
    render(stepsBar, STEPS.map((s, i) => h("li", {
      class: i === step ? "current" : i < step ? "done" : "",
      "aria-current": i === step ? "step" : null,
    }, h("span", { class: "wz-idx", "aria-hidden": "true" }, String(i + 1)), s)));
  }

  function guard(btn, fn) {
    return async (e) => {
      if (busy) return;
      busy = true;
      const b = btn || e.currentTarget;
      b.disabled = true;
      try { await fn(e); }
      catch (err) {
        toast(err.message || "Something failed — try again", { tone: "error", timeout: 6000 });
      } finally { busy = false; if (b) b.disabled = false; renderStep(); }
    };
  }

  function renderStep() {
    renderSteps();
    if (step === 0) return stepDiscover();
    if (step === 1) return stepSelect();
    return stepVerify();
  }

  // ── Step 1: Discover ────────────────────────────────────────────────
  function stepDiscover() {
    render(bodyEl, [
      h("h3", {}, "Find cameras on this network"),
      h("p", { class: "muted" },
        "ONVIF WS-Discovery broadcasts on the LAN — most cameras and NVRs answer within a few seconds. Discovery never sends credentials."),
      h("div", { class: "form-row" },
        h("button", {
          class: "primary", "data-act": "discover",
          onClick: guard(null, async () => {
            const out = bodyEl.querySelector("[data-role=dev-list]");
            out.textContent = "";
            out.append(h("p", { class: "muted" }, "Listening for devices…"));
            const res = await api("/api/onvif/discover", {
              method: "POST", body: JSON.stringify({ timeout: 5 }),
            });
            devices = res.xaddrs || [];
            const list = bodyEl.querySelector("[data-role=dev-list]");
            render(list, devices.length
              ? h("ul", { class: "wz-devs" }, devices.map((x) =>
                h("li", { class: "mono" }, x)))
              : h("p", { class: "muted" },
                "No devices answered. Cameras may still be addable manually — continue and enter the address yourself."));
          }),
        }, "Scan network"),
        h("button", { class: "ghost", "data-act": "to-select", onClick: () => { step = 1; renderStep(); } },
          "Enter manually →")),
      h("div", { "data-role": "dev-list" }),
      devices.length ? h("button", {
        class: "primary", "data-act": "pick-first",
        onClick: () => { picked = { xaddr: devices[0] }; step = 1; renderStep(); },
      }, `Continue with ${devices[0]}`) : null,
    ]);
  }

  // ── Step 2: Select & credentials ───────────────────────────────────
  function stepSelect() {
    const xaddrInput = h("input", {
      class: "mono", "data-field": "xaddr", value: picked?.xaddr || "",
      placeholder: "http://192.168.1.42:2020/onvif/device_service",
      "aria-label": "ONVIF device address",
    });
    render(bodyEl, [
      h("h3", {}, "Device address & credentials"),
      h("p", { class: "muted" },
        "Credentials are encrypted immediately and never displayed again — not even to admins."),
      h("form", {
        class: "form-col", "data-form": "wz-select",
        onSubmit: guard(null, async (e) => {
          e.preventDefault?.();
          const form = bodyEl.querySelector("[data-form=wz-select]");
          const xaddr = form.querySelector("[data-field=xaddr]").value.trim();
          const user = form.querySelector("[data-field=user]").value.trim();
          const pass = form.querySelector("[data-field=pass]").value;
          if (!xaddr) throw new ApiError(400, "device address required");
          let uris = [];
          if (/^https?:/.test(xaddr)) {
            const res = await api("/api/onvif/streams", {
              method: "POST",
              body: JSON.stringify({ xaddr, user: user || null, password: pass || null }),
            });
            uris = res.stream_uris || [];
          } else {
            uris = [xaddr]; // manual RTSP URL
          }
          picked = { xaddr, user, uris };
          step = 2;
        }),
      },
        h("label", { class: "field-hint", for: "wz-xaddr" },
          "ONVIF address (http://…) or RTSP URL (rtsp://…)", xaddrInput),
        h("div", { class: "form-row" },
          h("label", { class: "field-hint", for: "wz-user" }, "Username",
            h("input", { id: "wz-user", "data-field": "user", autocomplete: "off" })),
          h("label", { class: "field-hint", for: "wz-pass" }, "Password",
            h("input", { id: "wz-pass", "data-field": "pass", type: "password",
              autocomplete: "new-password" }))),
        h("div", { class: "form-row" },
          h("button", { class: "primary", type: "submit", "data-act": "wz-next" }, "Continue"),
          h("button", { class: "ghost", type: "button", onClick: () => { step = 0; renderStep(); } }, "← Back")),
      ),
    ]);
  }

  // ── Step 3: Verify & create ────────────────────────────────────────
  function stepVerify() {
    if (!picked) { step = 1; return stepSelect(); }
    const uriOptions = (picked.uris?.length ? picked.uris : [""]).map((u, i) =>
      h("option", { value: u, selected: i === 0 }, u || "type an RTSP URL"));
    render(bodyEl, [
      h("h3", {}, "Name it & verify"),
      h("p", { class: "muted" },
        "We create the camera, then pull one snapshot through the same path the privacy-mask editor uses. If the frame can't be fetched you'll see exactly why — nothing is faked."),
      h("form", {
        class: "form-col", "data-form": "wz-verify",
        onSubmit: guard(null, async (e) => {
          e.preventDefault?.();
          const form = bodyEl.querySelector("[data-form=wz-verify]");
          const name = form.querySelector("[data-field=name]").value.trim();
          const main = form.querySelector("[data-field=main]").value;
          if (!name) throw new ApiError(400, "camera name required");
          const res = await api("/api/cameras", {
            method: "POST",
            body: JSON.stringify({ name, stream_url: main || picked.uris?.[0] }),
          });
          created = res.id;
          let verified = false;
          let verifyMsg = "";
          try {
            await api(`/api/cameras/${created}/snapshot`);
            verified = true;
          } catch (err) {
            verifyMsg = err.message || "snapshot unavailable";
          }
          step = 3;
          renderStep();
          toast(verified
            ? `${name} added and verified — opening its settings`
            : `${name} added (snapshot: ${verifyMsg})`, {
            tone: verified ? "ok" : "warn", timeout: 6000 });
          if (verified) setTimeout(() => { onDone(); navigate("cameras", { id: created, tab: "masks" }); }, 900);
        }),
      },
        h("label", { class: "field-hint", for: "wz-name" }, "Camera name (what operators see)",
          h("input", { id: "wz-name", "data-field": "name",
            placeholder: "Loading Dock North", required: true })),
        h("label", { class: "field-hint", for: "wz-main" }, "Main stream",
          h("select", { id: "wz-main", "data-field": "main" }, uriOptions)),
        h("div", { class: "form-row" },
          h("button", { class: "primary", type: "submit", "data-act": "wz-create" }, "Create & verify"),
          h("button", { class: "ghost", type: "button", onClick: () => { step = 1; renderStep(); } }, "← Back")),
      ),
    ]);
  }

  render(outEl, h("div", { class: "cam-detail", "data-view": "wizard" }, [head, stepsBar, bodyEl]));
  renderStep();
}
