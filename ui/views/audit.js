// Audit view — the immutable log with skeleton/empty/error states.

import { h, render } from "../core/dom.js";
import { api } from "../core/api.js";
import { skeletonRows, emptyState, errorState } from "../core/states.js";
import { fmtDateTime, label, tone } from "../core/format.js";

export async function loadAudit(listEl) {
  skeletonRows(listEl, 6);
  try {
    const data = await api("/api/audit?limit=100");
    const rows = (data.items || []).map((a) => h("tr", {},
      h("td", { class: "mono" }, fmtDateTime(a.ts)),
      h("td", {}, a.username || "—"),
      h("td", { class: "mono" }, a.action),
      h("td", { class: "mono" }, a.resource || "—"),
      h("td", {}, h("span", { class: `pill ${tone(a.result)}` }, label(a.result))),
      h("td", { class: "mono" }, a.source_ip || "—"),
    ));
    render(listEl, rows.length
      ? h("div", { class: "table-scroll" },
          h("table", {},
            h("thead", {}, h("tr", {},
              h("th", {}, "Time"), h("th", {}, "User"), h("th", {}, "Action"),
              h("th", {}, "Resource"), h("th", {}, "Result"), h("th", {}, "IP"),
            )),
            h("tbody", {}, rows),
          ),
        )
      : emptyState({ icon: "☰", title: "No audit entries", hint: "Actions appear here as users interact with the system." }));
  } catch (err) {
    render(listEl, errorState(err, { noun: "audit log", onRetry: () => loadAudit(listEl) }));
  }
}
