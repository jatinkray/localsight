// Login view (fixes C-7: error copy by status — 401 vs 423 vs network) and
// the two-step MFA reveal (code field appears only when the account has MFA,
// removing the confusing "if enabled" label).

import { h } from "../core/dom.js";
import { establishSession, clearSession, primeMe } from "../core/api.js";

/**
 * Wire the login form. onAuthed() runs after a successful login.
 */
export function wireLogin(form, onAuthed) {
  const errEl = document.getElementById("login-error");
  const mfaField = document.getElementById("mfa-field");
  const emailInput = document.getElementById("email");
  const passwordInput = document.getElementById("password");
  const mfaInput = document.getElementById("mfa");
  const submitBtn = form.querySelector("button[type=submit]");

  function setError(message, hint = "") {
    errEl.replaceChildren(
      h("span", { "aria-hidden": "true" }, "⚠"),
      h("span", {}, message),
      hint ? h("span", { class: "muted" }, hint) : null,
    );
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    submitBtn.disabled = true;
    setError("", "");
    const body = {
      email: emailInput.value.trim(),
      password: passwordInput.value,
      ...(mfaField.dataset.shown === "1" && mfaInput.value
        ? { mfa_code: mfaInput.value.trim() } : {}),
    };
    try {
      const r = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (r.status === 423) {
        setError("Account temporarily locked",
          "Too many failed attempts — try again in a few minutes.");
        return;
      }
      if (r.status === 401) {
        let detail = "";
        try { detail = (await r.json()).detail || ""; } catch { /* empty body */ }
        if (/mfa/i.test(detail)) {
          mfaField.dataset.shown = "1";
          mfaField.classList.remove("hidden");
          mfaInput.focus();
          setError("Enter your MFA code", "This account has two-factor authentication enabled.");
          return;
        }
        setError("Incorrect email or password");
        return;
      }
      if (!r.ok) {
        setError("Sign-in failed", `Server error (${r.status}) — try again.`);
        return;
      }
      const data = await r.json();
      establishSession(data);
      await primeMe(); // role-gated UI needs the role before first paint
      onAuthed();
    } catch {
      setError("Can't reach the server", "Check your connection and retry.");
    } finally {
      submitBtn.disabled = false;
    }
  });
}

export function resetLogin() {
  clearSession();
  const mfaField = document.getElementById("mfa-field");
  mfaField.dataset.shown = "0";
  mfaField.classList.add("hidden");
}
