// Formatting helpers — display strings only; never trust for HTML (see dom.js).
//
// TIME (M1/E-4): every wall-clock render goes through one contract — the
// API sends UTC (Z or +00:00 suffix, enforced by packages.domain.timeutil.iso)
// and these helpers render it in the OPERATOR's chosen timezone with the tz
// name visible. No bare clocks, no ambiguous "5h ago" without an anchor.

const TZ_KEY = "lv-timezone"; // per-browser preference, default UTC (M2 adds the picker)

/** The active display timezone (IANA name). */
export function displayTz() {
  try { return localStorage.getItem(TZ_KEY) || "UTC"; } catch { return "UTC"; }
}

export function setDisplayTz(tz) {
  try { localStorage.setItem(TZ_KEY, tz); } catch { /* memory-only */ }
}

export function tzSuffix(d = new Date()) {
  const tz = displayTz();
  if (tz === "UTC") return "UTC";
  try {
    return new Intl.DateTimeFormat("en", { timeZone: tz, timeZoneName: "short" })
      .formatToParts(d).find((p) => p.type === "timeZoneName")?.value || tz;
  } catch { return tz; }
}

function _parts(d, opts) {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: displayTz(), hour12: false, ...opts,
  }).format(d);
}

/** "15:30:12 UTC" — clock + tz, never a bare clock. */
export function fmtTime(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return `${_parts(d, { hour: "2-digit", minute: "2-digit", second: "2-digit" })} ${tzSuffix(d)}`;
}

/** "2 Sep, 15:30" — for tight slots where seconds don't matter. */
export function fmtTimeShort(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return _parts(d, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

/** "2 Sep 2026, 15:30:12 UTC" — full unambiguous wall-clock (E-4 fix). */
export function fmtDateTime(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return `${_parts(d, { day: "numeric", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit", second: "2-digit" })} ${tzSuffix(d)}`;
}

/** ISO 8601 with explicit offset — for title tooltips and copy-exact needs. */
export function fmtIso(d = new Date()) {
  return d.toISOString();
}

/** "3m ago", "yesterday", "2d ago" — relative, but always next to an absolute
 *  render somewhere (drawer/tooltip). Kept for glanceable recency. */
export function fmtRelative(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const sec = (Date.now() - d.getTime()) / 1000;
  if (sec < 60) return "just now";
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  if (sec < 172800) return "yesterday";
  if (sec < 2592000) return `${Math.floor(sec / 86400)}d ago`;
  return fmtTimeShort(iso);
}

/** Short camera/identity id for dense tables: 9501d7a0 (not full hex). */
export function shortId(id) {
  if (!id) return "—";
  return id.slice(0, 8);
}

/** "2.4 GB", "37 MB" */
export function fmtBytes(bytes) {
  if (!bytes || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  return `${(bytes / 1024 ** i).toFixed(i > 1 ? 1 : 0)} ${units[i]}`;
}

/** "45s", "3m 20s" */
export function fmtDuration(seconds) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

/** Human label for internal enum values ("line_cross" → "Line cross"). */
const LABELS = {
  presence: "Presence",
  person_detected: "Person detected",
  line_cross: "Line crossing",
  anpr_hit: "Number plate",
  loitering: "Loitering",
  object_left: "Object left",
  crowd: "Crowd",
  intrusion: "Zone intrusion",
  known: "Known",
  unknown: "Unknown",
  uncertain: "Uncertain",
  ONLINE: "Online",
  OFFLINE: "Offline",
  DEGRADED: "Degraded",
  RECONNECTING: "Reconnecting",
  ok: "OK",
  warn: "Warning",
  bad: "Critical",
};

export function label(v) {
  if (v == null || v === "") return "—";
  return LABELS[v] ?? String(v);
}

/** Pill tone for status values — one color per meaning (consistency fix). */
export function tone(status) {
  switch (status) {
    case "ONLINE": case "ok": case "known": case "success": return "ok";
    case "DEGRADED": case "RECONNECTING": case "uncertain": case "warn": return "warn";
    case "OFFLINE": case "failure": case "critical": return "bad";
    case "unknown": return "info";
    default: return "warn";
  }
}
