// Command palette (M3/E-12) — Ctrl-K / Cmd-K to jump anywhere.
//
// Built over the existing GOTO story (g- keys, ? overlay): the palette is
// for people who don't memorize chords, and for jumping to ENTITIES
// (a camera by name, a person, a recent event), not just views.
//
// CSP-native: h()/render() DOM only, zero inline styles, no innerHTML.
// Full keyboard path: ↑↓ select, Enter go, Esc close. Fuzzy match is a
// simple subsequence score — no dependencies, ≤10 results by design.

import { h, render } from "./dom.js";
import { api } from "./api.js";
import { navigate } from "./router.js";

const MAX_RESULTS = 10;
let open = false;
let root = null;
let indexItems = [];   // built lazily on first open, cached per session
let selectedIndex = 0;
let lastQuery = "";

/** Build the searchable index: views (static) + cameras/persons/recent
 *  events (one API sweep). Failures degrade to views-only. */
async function buildIndex() {
  const items = [
    { kind: "View", label: "Overview (dashboard)", hint: "g o", go: () => navigate("dashboard") },
    { kind: "View", label: "Live wall", hint: "g l", go: () => navigate("live") },
    { kind: "View", label: "Events", hint: "g e", go: () => navigate("events") },
    { kind: "View", label: "Timeline", hint: "g t", go: () => navigate("timeline") },
    { kind: "View", label: "Cameras", hint: "g c", go: () => navigate("cameras") },
    { kind: "View", label: "Analytics", hint: "g a", go: () => navigate("analytics") },
    { kind: "View", label: "Users", hint: "g u", go: () => navigate("users") },
    { kind: "View", label: "Privacy", hint: "g p", go: () => navigate("privacy") },
    { kind: "View", label: "Audit log", hint: "g d", go: () => navigate("audit") },
    { kind: "View", label: "Account", hint: "nav", go: () => navigate("account") },
  ];
  const [cams, people, events] = await Promise.all([
    api("/api/cameras").catch(() => []),
    api("/api/persons").catch(() => ({ items: [] })),
    api("/api/events?limit=10&sort=timestamp&direction=desc").catch(() => ({ items: [] })),
  ]);
  for (const c of cams || []) {
    items.push({ kind: "Camera", label: c.name, hint: "camera",
      go: () => navigate("cameras", { focus: c.id }) });
  }
  const persons = Array.isArray(people) ? people : (people.items || []);
  for (const p of persons) {
    items.push({ kind: "Person", label: p.label || p.name || "person", hint: "identity",
      go: () => navigate("people", { q: p.label || p.name || "" }) });
  }
  for (const e of (events.items || []).slice(0, 10)) {
    const when = (e.timestamp_start || "").slice(0, 16).replace("T", " ");
    items.push({ kind: "Event", label: `${e.event_type} — ${when}`, hint: "recent event",
      go: () => navigate("event", {}, e.id) });
  }
  return items;
}

/** Subsequence fuzzy score: how well does `q` appear in-order in `s`?
 *  Higher = better. 0 = no match. Prefers contiguous runs + prefix hits. */
function fuzzyScore(q, s) {
  if (!q) return 1;
  const lower = s.toLowerCase();
  const query = q.toLowerCase();
  let si = 0, score = 0, run = 0;
  for (const ch of query) {
    const at = lower.indexOf(ch, si);
    if (at === -1) return 0;
    score += 1 + (at === si ? 2 : 0); // contiguity bonus
    run = at === si ? run + 1 : 0;
    score += run;
    si = at + 1;
  }
  if (lower.startsWith(query)) score += 10;
  return score;
}

function resultsFor(query) {
  if (!query) {
    return indexItems.filter((i) => i.kind === "View").slice(0, MAX_RESULTS);
  }
  return indexItems
    .map((i) => ({ i, s: fuzzyScore(query, i.label) + fuzzyScore(query, i.kind) * 0.5 }))
    .filter((x) => x.s > 0)
    .sort((a, b) => b.s - a.s)
    .slice(0, MAX_RESULTS)
    .map((x) => x.i);
}

function close() {
  open = false;
  if (root) { root.remove(); root = null; }
}

function resultRow(item, idx) {
  return h("li", {
    role: "option", id: `palette-opt-${idx}`, "aria-selected": String(idx === selectedIndex),
    class: idx === selectedIndex ? "palette-row selected" : "palette-row",
    "data-kind": item.kind,
    onMouseEnter: () => { selectedIndex = idx; paint(); },
    onClick: () => { item.go(); close(); },
  },
    h("span", { class: "palette-kind" }, item.kind),
    h("span", { class: "palette-label" }, item.label),
    h("span", { class: "palette-hint muted" }, item.hint || ""));
}

function paint() {
  if (!root) return;
  // paint into the results WRAPPER: the listbox itself is re-created per
  // query (loading state renders a <p>, results render the listbox).
  const list = root.querySelector("[data-role=palette-results]");
  const query = root.querySelector("[data-field=palette-q]").value.trim();
  const items = resultsFor(query);
  if (query !== lastQuery) { selectedIndex = 0; lastQuery = query; }
  render(list, items.length
    ? h("ul", { class: "palette-list", role: "listbox",
        "aria-label": "Results", id: "palette-listbox" },
        items.map(resultRow))
    : h("p", { class: "palette-empty muted" }, "Nothing matches — try a camera, person, or view name."));
  const box = root.querySelector("[data-field=palette-q]");
  box.setAttribute("aria-activedescendant",
    items.length ? `palette-opt-${selectedIndex}` : "");
}

export function togglePalette() {
  if (open) { close(); return; }
  open = true;
  selectedIndex = 0;
  lastQuery = "";
  const input = h("input", {
    type: "text", "data-field": "palette-q", class: "palette-input mono",
    placeholder: "Jump to… camera, person, event, or view",
    "aria-label": "Search commands", autocomplete: "off",
    role: "combobox", "aria-expanded": "true", "aria-controls": "palette-listbox",
    onInput: paint,
    onKeyDown: (e) => {
      const items = resultsFor(e.currentTarget.value.trim());
      if (e.key === "ArrowDown") { e.preventDefault();
        selectedIndex = Math.min(selectedIndex + 1, items.length - 1); paint(); }
      else if (e.key === "ArrowUp") { e.preventDefault();
        selectedIndex = Math.max(selectedIndex - 1, 0); paint(); }
      else if (e.key === "Enter") { e.preventDefault();
        const it = items[selectedIndex];
        if (it) { it.go(); close(); } }
      else if (e.key === "Escape") { e.preventDefault(); close(); }
    },
  });
  root = h("div", { class: "palette-scrim", onClick: (e) => {
    if (e.currentTarget === e.target) close();
  } },
    h("div", { class: "palette", role: "dialog", "aria-modal": "true",
        "aria-label": "Command palette" },
      input,
      h("div", { "data-role": "palette-results" },
        h("p", { class: "palette-empty muted", id: "palette-listbox" },
          "Loading index…"))));
  document.body.append(root);
  input.focus();
  // build/refresh the index, then paint with it
  buildIndex().then((items) => {
    indexItems = items;
    if (open) paint();
  });
}

/** Wire global Ctrl-K / Cmd-K. Call once from app boot. */
export function wirePalette() {
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K")) {
      e.preventDefault();
      togglePalette();
    }
  });
}
