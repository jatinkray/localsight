// Hash router — shareable, bookmarkable investigations (Wave 1).
//
// The old app kept view state in JS only: no deep links, no back/forward,
// and a reload lost your place. The router owns:
//   #/events?camera=abc&status=unknown    -> events view + filters
//   #/event/<id>                          -> event drawer (on events view)
//   #/timeline?date=2026-09-01            -> timeline view + params
//
// Navigation is declarative: views register a loader; route() resolves the
// hash, diffs against the last route, and calls the loader with params.

const listeners = new Map(); // view -> loader(params)
let last = { view: null, params: {} };

/** Register a view loader: onView("events", (params) => …) */
export function onView(view, loader) {
  listeners.set(view, loader);
}

export function parseHash(hash = location.hash) {
  const raw = hash.replace(/^#\/?/, "");
  const [path, query] = raw.split("?");
  const parts = path.split("/").filter(Boolean);
  const params = new URLSearchParams(query || "");
  // Routes: [view] or [event, id]
  if (parts[0] === "event" && parts[1]) {
    return { view: "events", eventId: parts[1], params: {} };
  }
  const view = parts[0] || "dashboard";
  const out = {};
  for (const [k, v] of params.entries()) out[k] = v;
  return { view, eventId: null, params: out };
}

/** Serialize a route back into a hash string. Nullish params are dropped
 *  (URLSearchParams would stringify undefined as "undefined"). */
export function toHash(view, params = {}, eventId = null) {
  const clean = Object.fromEntries(
    Object.entries(params).filter(([, v]) => v != null && v !== ""));
  const qs = new URLSearchParams(clean).toString();
  const base = eventId ? `#/event/${encodeURIComponent(eventId)}`
    : `#/${view === "dashboard" ? "" : view}`;
  return qs ? `${base}?${qs}` : base;
}

/** Navigate (pushes history). */
export function navigate(view, params = {}, eventId = null) {
  const hash = toHash(view, params, eventId);
  if (location.hash === hash) {
    route(); // same hash: re-run (acts as refresh)
  } else {
    location.hash = hash;
  }
}

/** Replace current entry without pushing history (e.g. drawer close). */
export function replace(view, params = {}, eventId = null) {
  history.replaceState(null, "", toHash(view, params, eventId));
  route();
}

let routing = false;

/** Resolve the current hash and invoke the view loader. */
export function route() {
  if (routing) return; // loader-triggered navigations re-enter route()
  routing = true;
  try {
    const r = parseHash();
    const loader = listeners.get(r.view) || listeners.get("dashboard");
    if (loader) loader(r.params, r.eventId);
    last = r;
  } finally {
    routing = false;
  }
}

export function current() {
  return { ...last, params: { ...last.params } };
}

export function start() {
  window.addEventListener("hashchange", route);
  route();
}
