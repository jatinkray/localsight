// Account view (M2 — "the account story"): profile, password rotation,
// MFA enrollment, active sessions, and the display timezone. The backend
// existed for MFA (E-5's finding: API with no UI); password change is new.
//
// Everything here is self-service: an operator manages their OWN
// credentials without an admin, and every action lands in the audit log.

import { h, render } from "../core/dom.js";
import { api } from "../core/api.js";
import { toast } from "../core/toast.js";
import { fmtDateTime, displayTz, setDisplayTz } from "../core/format.js";

const TZ_CHOICES = [
  ["UTC", "UTC (recommended for multi-site)"],
  ["Europe/London", "Europe/London"],
  ["Europe/Berlin", "Europe/Berlin"],
  ["America/New_York", "America/New_York"],
  ["America/Chicago", "America/Chicago"],
  ["America/Los_Angeles", "America/Los_Angeles"],
  ["Asia/Singapore", "Asia/Singapore"],
  ["Asia/Tokyo", "Asia/Tokyo"],
  ["Australia/Sydney", "Australia/Sydney"],
];

export async function loadAccount(listEl) {
  render(listEl, h("div", { class: "cam-detail", "data-view-root": "account" },
    profileCard(),
    passwordCard(),
    mfaCard(listEl),
    sessionsCard(listEl),
    timezoneCard(),
  ));
  refreshSessions(listEl);
}

function profileCard() {
  const dd_email = h("dd", { class: "mono" }, "—");
  const dd_role = h("dd", {}, "—");
  api("/api/auth/me").then((me) => {
    dd_email.textContent = me.email || "—";
    dd_role.textContent = me.role || "—";
  }).catch(() => {});
  return h("div", { class: "card" },
    h("h3", {}, "Profile"),
    h("dl", { class: "kv-grid" },
      h("dt", {}, "Signed in as"), dd_email,
      h("dt", {}, "Role"), dd_role,
      h("dt", {}, "Session"),
      h("dd", {}, "access token in memory only · refresh rotates on use"),
    ),
  );
}

function passwordCard() {
  let form;
  form = h("form", {
    class: "form-col", "data-form": "pw-change", novalidate: true,
    onSubmit: async (e) => {
      e.preventDefault();
      const oldPw = form.querySelector("[data-field=old]").value;
      const newPw = form.querySelector("[data-field=new]").value;
      const confirm = form.querySelector("[data-field=confirm]").value;
      if (newPw.length < 12) return toast("New password must be at least 12 characters", { tone: "warn" });
      if (newPw !== confirm) return toast("The two new-password fields don't match", { tone: "warn" });
      const btn = form.querySelector("button[type=submit]");
      btn.disabled = true; // C-13 double-submit guard
      btn.textContent = "Rotating…";
      try {
        const res = await api("/api/auth/password", {
          method: "POST",
          body: JSON.stringify({ old_password: oldPw, new_password: newPw }),
        });
        toast(res.sessions_revoked
          ? "Password rotated — other devices were signed out" : "Password rotated",
          { tone: "ok" });
        form.reset();
      } catch (err) {
        toast(err.status === 401 ? "Current password is incorrect"
          : err.status === 429 ? "Too many attempts — wait a moment"
          : "Couldn't rotate the password — try again", { tone: "error" });
      } finally {
        btn.disabled = false;
        btn.textContent = "Change password";
      }
    },
  },
    h("label", {}, "Current password",
      h("input", { type: "password", "data-field": "old", autocomplete: "current-password",
        required: true, minlength: 1 })),
    h("label", {}, "New password (12+ characters)",
      h("input", { type: "password", "data-field": "new", autocomplete: "new-password",
        required: true, minlength: 12 })),
    h("label", {}, "Repeat new password",
      h("input", { type: "password", "data-field": "confirm", autocomplete: "new-password",
        required: true, minlength: 12 })),
    h("button", { class: "primary", type: "submit", "data-act": "pw-submit" }, "Change password"),
  );
  return h("div", { class: "card" },
    h("h3", {}, "Password"),
    h("p", { class: "muted" },
      "Rotating signs out your other devices. The new password takes effect immediately."),
    form);
}

function mfaCard(listEl) {
  const body = h("div", { "data-role": "mfa-body" });
  const paint = () => {
    render(body, h("p", { class: "muted" }, "Loading MFA status…"));
    api("/api/auth/me").then((me) => {
      render(body, me.mfa_enabled
        ? mfaEnabledBody()
        : mfaEnrollBody(listEl));
    }).catch(() => render(body,
      h("p", { class: "muted" }, "Couldn't load MFA status — reload the view.")));
  };
  paint();
  return h("div", { class: "card" },
    h("h3", {}, "Two-factor authentication (TOTP)"),
    h("p", { class: "muted" },
      "Works with any authenticator app (Google Authenticator, 1Password, Aegis…). ",
      "Codes are verified locally — nothing leaves this host."),
    body);
}

function mfaEnabledBody() {
  return h("div", {},
    h("p", {}, h("span", { class: "pill ok" }, "MFA on"),
      "  Your login requires a 6-digit code."),
    h("p", { class: "muted" },
      "To switch devices, ask an administrator for an MFA reset, then re-enroll here."));
}

function mfaEnrollBody(listEl) {
  let zone;
  zone = h("div", { class: "form-col", "data-role": "mfa-enroll" });
  const btn = h("button", {
    class: "primary", "data-act": "mfa-setup",
    onClick: async () => {
      btn.disabled = true;
      btn.textContent = "Generating…";
      try {
        const res = await api("/api/auth/mfa/setup", { method: "POST" });
        renderEnroll(zone, res, listEl);
      } catch (err) {
        toast("Couldn't start MFA enrollment — try again", { tone: "error" });
        btn.disabled = false;
        btn.textContent = "Start enrollment";
      }
    },
  }, "Start enrollment");
  render(zone, btn);
  return h("div", {}, zone);
}

function renderEnroll(zone, setup, listEl) {
  const copy = (txt, what) => () => {
    navigator.clipboard.writeText(txt).then(
      () => toast(`${what} copied`, { tone: "ok" }),
      () => toast("Copy failed — select the text manually", { tone: "warn" }));
  };
  let codeInput;
  const verifyBtn = h("button", {
    class: "primary", "data-act": "mfa-verify",
    onClick: async () => {
      verifyBtn.disabled = true;
      verifyBtn.textContent = "Verifying…";
      try {
        await api("/api/auth/mfa/verify", {
          method: "POST", body: JSON.stringify({ code: codeInput.value.trim() }),
        });
        toast("MFA is on — your next login asks for a code", { tone: "ok" });
        render(zone, h("p", {}, h("span", { class: "pill ok" }, "MFA on"),
          "  Enrollment complete."));
      } catch (err) {
        toast(err.status === 400 ? "That code didn't verify — check the app and retry"
          : "Verification failed — try again", { tone: "error" });
        verifyBtn.disabled = false;
        verifyBtn.textContent = "Verify & enable";
      }
    },
  }, "Verify & enable");
  codeInput = h("input", {
    type: "text", inputmode: "numeric", "data-field": "mfa-code",
    autocomplete: "one-time-code", placeholder: "6-digit code",
    "aria-label": "Authenticator code", maxlength: 8,
  });
  render(zone,
    h("p", { class: "muted" }, "1. Add the key below to your authenticator app:"),
    h("div", { class: "form-row" },
      h("code", { class: "mono" }, setup.secret),
      h("button", { class: "ghost", type: "button", onClick: copy(setup.secret, "Key") }, "Copy key")),
    h("p", { class: "muted" },
      "Or paste the otpauth URI into an app that accepts URIs (1Password, Aegis):"),
    h("div", { class: "form-row" },
      h("code", { class: "mono text-xs" }, setup.otpauth_uri),
      h("button", { class: "ghost", type: "button", onClick: copy(setup.otpauth_uri, "URI") }, "Copy URI")),
    h("p", { class: "muted" }, "2. Enter the code the app shows now:"),
    h("div", { class: "form-row" }, codeInput, verifyBtn));
}

async function refreshSessions(listEl) {
  const host = listEl.querySelector("[data-role=sessions-body]");
  if (!host) return;
  try {
    const data = await api("/api/auth/sessions");
    render(host, data.sessions.length
      ? h("ul", { class: "mask-rows" },
          data.sessions.map((s) => h("li", { class: "mask-row", "data-session": s.id },
            h("span", { class: "mask-desc" },
              h("strong", {}, "Session"),
              h("span", { class: "muted text-xs" }, ` since ${fmtDateTime(s.created_at)}`)),
            h("button", {
              class: "ghost", "data-act": "session-revoke",
              onClick: async () => {
                try {
                  await api(`/api/auth/sessions/${s.id}/revoke`, { method: "POST" });
                  toast("Session revoked — that device signs out on next use", { tone: "ok" });
                  refreshSessions(listEl);
                } catch {
                  toast("Revoke failed — try again", { tone: "error" });
                }
              },
            }, "Revoke"))))
      : h("p", { class: "muted" }, "No other active sessions."));
  } catch {
    render(host, h("p", { class: "muted" }, "Couldn't load sessions — reload the view."));
  }
}

function sessionsCard(listEl) {
  return h("div", { class: "card" },
    h("h3", {}, "Active sessions"),
    h("p", { class: "muted" },
      "Every device where you're signed in. Revoking signs it out at its next request."),
    h("div", { "data-role": "sessions-body" },
      h("p", { class: "muted" }, "Loading…")));
}

function timezoneCard() {
  const sel = h("select", {
    "aria-label": "Display timezone", "data-field": "tz",
    onChange: () => {
      setDisplayTz(sel.value);
      toast(`Times now render in ${sel.value} — reload to refresh open views`,
        { tone: "ok" });
    },
  }, TZ_CHOICES.map(([v, lbl]) =>
    h("option", { value: v, ...(displayTz() === v ? { selected: true } : {}) }, lbl)));
  return h("div", { class: "card" },
    h("h3", {}, "Timezone"),
    h("p", { class: "muted" },
      "All times are stored in UTC; this chooses how they're displayed to you. ",
      "It's per-browser — no data leaves this machine."),
    sel);
}
