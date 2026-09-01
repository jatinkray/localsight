// State components: skeleton, empty, error (fixes C-12 — never-blank views).
// All built from nodes (dom.js) — CSP-safe and XSS-safe by construction.

import { h } from "./dom.js";

/** Rows of shimmering placeholders while first data loads. */
export function skeletonRows(container, rows = 5) {
  container.replaceChildren(
    ...Array.from({ length: rows }, () =>
      h("div", { class: "skeleton skeleton-row" }),
    ),
  );
}

/** Skeleton block for stat cards. */
export function skeletonCards(container, cards = 4) {
  container.replaceChildren(
    ...Array.from({ length: cards }, () =>
      h("div", { class: "card stat" },
        h("div", { class: "skeleton skeleton-card" }),
      ),
    ),
  );
}

/**
 * Empty state: icon, title, hint, optional action button.
 * emptyState({icon: "◉", title: "No cameras yet", hint: "…", action: {label, onClick}})
 */
export function emptyState({ icon = "◌", title, hint = "", action = null } = {}) {
  return h("div", { class: "state-box" },
    h("div", { class: "state-icon", "aria-hidden": "true" }, icon),
    h("div", { class: "state-title" }, title),
    hint ? h("div", { class: "state-hint" }, hint) : null,
    action ? h("button", {
      class: "primary", type: "button",
      onClick: action.onClick,
    }, action.label) : null,
  );
}

/**
 * Error state with retry. errorState(err, {onRetry, noun})
 */
export function errorState(err, { onRetry = null, noun = "content" } = {}) {
  const box = h("div", { class: "state-box" },
    h("div", { class: "state-icon", "aria-hidden": "true" }, "⚠"),
    h("div", { class: "state-title" }, `Couldn't load ${noun}`),
    h("div", { class: "state-hint" },
      err && err.message ? err.message : "The server didn't respond."),
    onRetry ? h("button", { class: "primary", type: "button", onClick: onRetry },
      "Retry") : null,
  );
  return box;
}

/** Inline error banner for above-table placement. */
export function errorBanner(message, { onRetry = null } = {}) {
  return h("div", { class: "error-banner", role: "alert" },
    h("span", { "aria-hidden": "true" }, "⚠"),
    h("span", { class: "grow" }, message),
    onRetry ? h("button", { class: "ghost", type: "button", onClick: onRetry }, "Retry") : null,
  );
}
