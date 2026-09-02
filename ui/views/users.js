// Users — administration screen (Wave 3).
//
// Table + create form (real roles, 12-char minimum surfaced up front,
// Argon2id note), delete with typed-email confirm and the session-revocation
// cascade explained, and a plain-language roles legend read straight from
// packages/security/rbac.py (never hand-copied into prose that can drift).

import { h, render } from "../core/dom.js";
import { api, can, getMe } from "../core/api.js";
import { skeletonRows, emptyState, errorState } from "../core/states.js";
import { toast } from "../core/toast.js";
import { navigate } from "../core/router.js";

const ROLES = [
  { id: "ADMIN", blurb: "everything, including user management and system config" },
  { id: "SECURITY_OPERATOR", blurb: "cameras, live view, enrollment, alert routes" },
  { id: "ANALYST", blurb: "read, search, timeline, exports, audit — no configuration" },
  { id: "VIEWER", blurb: "read-only: cameras, events, timeline, live" },
];

const roleTone = { ADMIN: "warn", SECURITY_OPERATOR: "info", ANALYST: "info", VIEWER: "" };

export async function loadUsers(listEl, params = {}) {
  if (!can("user:manage")) {
    return render(listEl, emptyState({
      icon: "⚿", title: "User administration",
      hint: "Requires the user:manage permission.",
    }));
  }
  if (params.new === "1") return listWithForm(listEl);
  return listUsers(listEl);
}

async function listUsers(listEl) {
  skeletonRows(listEl, 3);
  let users;
  try {
    users = await api("/api/users");
  } catch (err) {
    return render(listEl, errorState(err, { noun: "users", onRetry: () => listUsers(listEl) }));
  }
  const me = getMe();
  const others = users.filter((u) => u.id !== me?.id);
  const meRow = users.find((u) => u.id === me?.id);

  const wrapper = h("div", { "data-user-count": String(users.length) });
  render(listEl, wrapper);

  if (!users.length) {
    wrapper.append(emptyState({
      icon: "⚿", title: "No users", hint: "The host has no accounts.",
    }));
  } else {
    const meBadge = (u) => u.id === me?.id
      ? h("span", { class: "muted text-xs" }, " · you") : null;

    wrapper.append(h("div", { class: "table-scroll" },
      h("table", {},
        h("thead", {}, h("tr", {},
          h("th", {}, "Email"), h("th", {}, "Name"), h("th", {}, "Role"),
          h("th", {}, "MFA"), h("th", {}, "Status"))),
        h("tbody", {},
          users.map((u) => h("tr", { "data-user": u.email },
            h("td", { class: "mono" }, u.email, meBadge(u)),
            h("td", {}, u.full_name || "—"),
            h("td", {}, h("span", { class: `pill ${roleTone[u.role] || ""}` }, u.role)),
            h("td", {}, h("span", { class: `pill ${u.mfa_enabled ? "ok" : ""}` },
              u.mfa_enabled ? "MFA on" : "MFA off")),
            h("td", {}, h("span", { class: `pill ${u.is_active ? "ok" : "warn"}` },
              u.is_active ? "active" : "disabled")),
            h("td", {}, h("span", { class: "row-actions" },
              u.mfa_enabled && u.id !== me?.id ? mfaResetBtn(u, listEl) : null,
              u.id !== me?.id ? revokeSessionsBtn(u) : null,
              u.id === me?.id ? null : deleteBtn(u, listEl))),
          )),
        ))));
  }

  wrapper.append(h("div", { class: "card" },
    h("h3", {}, "Roles on this host"),
    h("dl", { class: "kv-grid" },
      ROLES.map((r) => [h("dt", { class: "mono" }, r.id), h("dd", {}, r.blurb)]).flat()),
  ));

  const addBtn = h("button", {
    class: "primary", "data-act": "user-add",
    onClick: () => { location.hash = "#/users?new=1"; },
  }, "Add a user");
  const head = wrapper.querySelector(".table-scroll") ? addBtn : h("button", {
    class: "primary", "data-act": "user-add",
    onClick: () => { location.hash = "#/users?new=1"; },
  }, "Add your first user");
  wrapper.prepend(head);
  return wrapper;
}

/** M2/E-5: admin-initiated MFA reset — typed-confirm (the app's
 *  destructive-action discipline; a security event deserves it too). */
function mfaResetBtn(u) {
  return h("button", {
    class: "ghost", "data-act": "mfa-reset", title: "Reset this user's MFA (they re-enroll)",
    onClick: (e) => {
      const td = e.currentTarget.closest("td");
      const input = h("input", {
        "data-field": "confirm-mfa", class: "mono", autocomplete: "off",
        placeholder: `type ${u.email}`, "aria-label": "Confirm MFA reset by typing the email",
      });
      const go = h("button", {
        class: "ghost", "data-act": "mfa-reset-confirm", disabled: true,
        onClick: async () => {
          go.disabled = true;
          try {
            await api(`/api/users/${u.id}/mfa-reset`, { method: "POST" });
            toast(`MFA reset for ${u.email} — they can re-enroll from Account`, { tone: "ok" });
            navigate("users"); // reload the list (fresh MFA pill)
          } catch (err) {
            toast(err.status === 400 ? "MFA isn't enabled for that user"
              : "Reset failed — try again", { tone: "error" });
            go.disabled = false;
          }
        },
      }, "Confirm reset");
      input.addEventListener("input", () => {
        go.disabled = input.value.trim() !== u.email;
      });
      render(td, h("span", { class: "confirm-zone" },
        h("span", { class: "muted text-xs" },
          "Resetting MFA signs the user out of MFA protection until they re-enroll."),
        input, go));
      input.focus();
    },
  }, "Reset MFA");
}

/** M2/E-13: revoke every session of one user — same typed confirm. */
function revokeSessionsBtn(u) {
  return h("button", {
    class: "ghost", "data-act": "sessions-revoke", title: "Sign out all of this user's devices",
    onClick: (e) => {
      const td = e.currentTarget.closest("td");
      const input = h("input", {
        "data-field": "confirm-sessions", class: "mono", autocomplete: "off",
        placeholder: `type ${u.email}`, "aria-label": "Confirm by typing the email",
      });
      const go = h("button", {
        class: "ghost", "data-act": "sessions-revoke-confirm", disabled: true,
        onClick: async () => {
          go.disabled = true;
          try {
            const res = await api(`/api/users/${u.id}/sessions/revoke-all`, { method: "POST" });
            toast(`Revoked ${res.revoked} session(s) for ${u.email}`, { tone: "ok" });
          } catch {
            toast("Revoke failed — try again", { tone: "error" });
            go.disabled = false;
          }
        },
      }, "Sign out everywhere");
      input.addEventListener("input", () => {
        go.disabled = input.value.trim() !== u.email;
      });
      render(td, h("span", { class: "confirm-zone" },
        h("span", { class: "muted text-xs" },
          "Every device where this user is signed in gets revoked."),
        input, go));
      input.focus();
    },
  }, "Sign out all");
}

function deleteBtn(u, listEl) {
  return h("button", {
    class: "ghost", "data-act": "delete-user",
    onClick: (e) => {
      const td = e.currentTarget.parentElement;
      const zone = h("span", { class: "confirm-zone" },
        h("span", { class: "muted text-xs" },
          "Deleting a user revokes all their sessions immediately. This cannot be undone."),
        h("input", {
          "data-field": "confirm-email", class: "mono", autocomplete: "off",
          placeholder: `type ${u.email}`, "aria-label": "Confirm by typing the email" }),
        h("span", { class: "form-row" },
          h("button", {
            class: "ghost", "data-act": "delete-user-confirm", disabled: true,
            onClick: async (ev) => {
              ev.currentTarget.disabled = true;
              try {
                await api(`/api/users/${u.id}`, { method: "DELETE" });
                toast(`${u.email} deleted`, { tone: "ok" });
                listUsers(listEl);
              } catch (err) {
                toast(err.message, { tone: "error" });
                ev.currentTarget.disabled = false;
              }
            },
          }, "Delete user"),
          h("button", {
            class: "ghost", "data-act": "delete-cancel",
            onClick: () => zone.replaceWith(deleteBtn(u, listEl)),
          }, "Cancel")));
      zone.addEventListener("input", () => {
        const input = zone.querySelector("[data-field=confirm-email]");
        zone.querySelector("[data-act=delete-user-confirm]").disabled =
          input.value.trim() !== u.email;
      });
      e.currentTarget.replaceWith(zone);
      zone.querySelector("input").focus();
    },
  }, "Delete");
}

async function listWithForm(listEl) {
  await listUsers(listEl);
  const form = h("form", { class: "card form-col", "data-form": "user-new", novalidate: true },
    h("h3", {}, "Add a user"),
    h("p", { class: "muted" },
      "The password is hashed with Argon2id on this host and never stored or logged in plaintext."),
    h("label", { class: "field-hint", for: "nu-email" }, "Email",
      h("input", { id: "nu-email", "data-field": "email", type: "email",
        required: true, autocomplete: "off" })),
    h("label", { class: "field-hint", for: "nu-name" }, "Full name",
      h("input", { id: "nu-name", "data-field": "name", autocomplete: "off" })),
    h("label", { class: "field-hint", for: "nu-role" }, "Role",
      h("select", { id: "nu-role", "data-field": "role", required: true },
        ROLES.map((r) => h("option", { value: r.id }, r.id)))),
    h("label", { class: "field-hint", for: "nu-pass" },
      "Password (minimum 12 characters)",
      h("input", { id: "nu-pass", "data-field": "password", type: "password",
        minlength: "12", required: true, autocomplete: "new-password" })),
    h("div", { class: "form-row" },
      h("button", { class: "primary", type: "submit", "data-act": "user-create" }, "Create user"),
      h("button", { class: "ghost", type: "button", onClick: () => { location.hash = "#/users"; } }, "Cancel")),
  );
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = form.querySelector("button[type=submit]");
    const pass = form.querySelector("[data-field=password]").value;
    if (pass.length < 12) return toast("Password must be at least 12 characters", { tone: "warn" });
    btn.disabled = true; // C-13
    try {
      const email = form.querySelector("[data-field=email]").value.trim();
      await api("/api/users", {
        method: "POST",
        body: JSON.stringify({
          email,
          password: pass,
          role: form.querySelector("[data-field=role]").value,
          full_name: form.querySelector("[data-field=name]").value.trim(),
        }),
      });
      toast(`${email} created`, { tone: "ok" });
      location.hash = "#/users";
      listUsers(listEl);
    } catch (err) {
      toast(err.status === 409 ? "Email already exists" : err.message, { tone: "error" });
    } finally { btn.disabled = false; }
  });
  listEl.prepend(form);
  form.querySelector("[data-field=email]").focus();
}
