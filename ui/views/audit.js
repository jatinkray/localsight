// Audit view — the immutable log with skeleton/empty/error states.
//
// M1 (E-1/E-2/E-14): sortable columns, filter bar (user, action, result,
// date window), pagination, and CSV export of the current filtered set.
// The compliance reviewer lives here — it's a data workhorse now.

import { h, render } from "../core/dom.js";
import { api } from "../core/api.js";
import { toast } from "../core/toast.js";
import { skeletonRows, emptyState, errorState } from "../core/states.js";
import { fmtDateTime, label, tone } from "../core/format.js";
import { navigate, replace } from "../core/router.js";

const PAGE = 25;
let offset = 0;
let sortKey = "ts";
let sortDir = "desc";
let filters = { username: "", action: "", result: "", start: "", end: "" };

// Actions known to exist (audit vocabulary grows with features; the select
// is a convenience — unknown typed values still filter fine).
const ACTIONS = ["login", "logout", "login_failure", "video.export", "video.clip.assemble",
  "events.export_csv", "audit.export_csv", "camera.create", "camera.delete",
  "camera.update", "person.enroll", "person.erase", "user.create", "user.delete",
  "route.create", "route.delete", "route.test_fire"];

function qs() {
  return new URLSearchParams({
    limit: PAGE, offset, sort: sortKey, direction: sortDir,
    ...(filters.username ? { username: filters.username } : {}),
    ...(filters.action ? { action: filters.action } : {}),
    ...(filters.result ? { result: filters.result } : {}),
    ...(filters.start ? { start: filters.start } : {}),
    ...(filters.end ? { end: filters.end } : {}),
  });
}

export async function loadAudit(listEl, { resetOffset = false, params = {} } = {}) {
  if (resetOffset) offset = 0;
  // E-10: deep-linkable audit state — hash restores filters + sort.
  // (Router loads pass params; in-view reloads pass none — keep state.)
  if (params && Object.keys(params).length) {
    filters = {
      username: params.username || "", action: params.action || "",
      result: params.result || "", start: params.start || "", end: params.end || "",
    };
    if (params.sort) sortKey = params.sort;
    if (params.direction) sortDir = params.direction;
    for (const [id, v] of [["au-user", filters.username], ["au-action", filters.action],
      ["au-result", filters.result], ["au-start", filters.start], ["au-end", filters.end]]) {
      const el = document.getElementById(id);
      if (el && el.value !== v) el.value = v;
    }
  }
  skeletonRows(listEl, 6);
  try {
    const data = await api(`/api/audit?${qs()}`);
    const rows = (data.items || []).map((a) => h("tr", {},
      h("td", { class: "mono", title: a.ts }, fmtDateTime(a.ts)),
      h("td", {}, a.username || "—"),
      h("td", { class: "mono" }, a.action),
      h("td", { class: "mono" }, a.resource || "—"),
      h("td", {}, h("span", { class: `pill ${tone(a.result)}` }, label(a.result))),
      h("td", { class: "mono" }, a.source_ip || "—"),
    ));

    const th = (key, text) => {
      const active = sortKey === key;
      return h("th", {
        "aria-sort": active ? (sortDir === "asc" ? "ascending" : "descending") : "none",
        class: active ? (sortDir === "asc" ? "sorted-asc" : "sorted-desc") : "",
        scope: "col",
        ...(key ? { onClick: () => {
          if (sortKey === key) sortDir = sortDir === "asc" ? "desc" : "asc";
          else { sortKey = key; sortDir = "asc"; }
          offset = 0;
          replace("audit", { ...filters, sort: sortKey, direction: sortDir });
          loadAudit(listEl);
        } } : {}),
      }, text);
    };

    render(listEl, rows.length || data.total
      ? h("div", { class: "table-scroll" },
          h("table", {},
            h("thead", {}, h("tr", {},
              th("ts", "Time"), th("user", "User"), th("action", "Action"),
              th("resource", "Resource"), th("result", "Result"), th("ip", "IP"),
            )),
            h("tbody", {}, rows.length ? rows
              : [h("tr", {}, h("td", { colspan: 6 },
                  h("div", { class: "muted" }, "No entries match these filters — clear them to see the full trail.")))]),
          ),
        )
      : emptyState({ icon: "☰", title: "No audit entries", hint: "Actions appear here as users interact with the system." }));

    const page = document.getElementById("au-page");
    if (page) {
      page.textContent = data.total
        ? `${Math.floor(offset / PAGE) + 1} / ${Math.ceil(data.total / PAGE)}`
        : "0";
      document.getElementById("au-prev").disabled = offset <= 0;
      document.getElementById("au-next").disabled = offset + PAGE >= data.total;
    }
  } catch (err) {
    render(listEl, errorState(err, { noun: "audit log", onRetry: () => loadAudit(listEl) }));
  }
}

export function wireAuditView(listEl) {
  // populate the action select once
  const actionSel = document.getElementById("au-action");
  if (actionSel && !actionSel.dataset.filled) {
    actionSel.dataset.filled = "1";
    for (const a of ACTIONS) {
      actionSel.append(h("option", { value: a }, a));
    }
  }
  const bind = (id, key) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("change", () => {
      filters[key] = el.value.trim();
      // E-10: filters live in the hash — the trail is shareable
      navigate("audit", { ...filters, sort: sortKey, direction: sortDir });
    });
  };
  bind("au-user", "username");
  bind("au-action", "action");
  bind("au-result", "result");
  bind("au-start", "start");
  bind("au-end", "end");

  const prev = document.getElementById("au-prev");
  const next = document.getElementById("au-next");
  if (prev) prev.addEventListener("click", () => {
    offset = Math.max(0, offset - PAGE);
    loadAudit(listEl);
  });
  if (next) next.addEventListener("click", () => {
    offset += PAGE;
    loadAudit(listEl);
  });

  const copyBtn = document.getElementById("au-link");
  if (copyBtn && !copyBtn.dataset.wired) {
    copyBtn.dataset.wired = "1";
    copyBtn.addEventListener("click", () => {
      const url = `${location.origin}${location.pathname}#/audit?` +
        new URLSearchParams({ ...filters, sort: sortKey, direction: sortDir }).toString();
      navigator.clipboard.writeText(url).then(
        () => toast("Link copied — it reproduces this filtered trail", { tone: "ok" }),
        () => toast("Copy failed — the URL bar has the same link", { tone: "warn" }));
    });
  }

  const exportBtn = document.getElementById("au-export");
  if (exportBtn && !exportBtn.dataset.wired) {
    exportBtn.dataset.wired = "1";
    exportBtn.addEventListener("click", async () => {
      exportBtn.disabled = true;
      const old = exportBtn.textContent;
      exportBtn.textContent = "Exporting…";
      try {
        const params = qs();
        params.delete("limit");
        params.delete("offset");
        const csv = await api(`/api/audit/export.csv?${params}`);
        const blob = new Blob([csv], { type: "text/csv" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `audit-${new Date().toISOString().slice(0, 10)}.csv`;
        a.click();
        URL.revokeObjectURL(url);
        toast("Audit export downloaded — the export itself is on the record", { tone: "ok" });
      } catch (err) {
        toast(err.status === 403 ? "Your role can't export the audit trail" : "Export failed — try again",
          { tone: "error" });
      } finally {
        exportBtn.disabled = false;
        exportBtn.textContent = old;
      }
    });
  }
}
