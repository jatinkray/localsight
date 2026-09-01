// Formatting helpers — display strings only; never trust for HTML (see dom.js).

/** "14:02:11" — always local time, tabular-friendly. */
export function fmtTime(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString(undefined, { hour12: false });
}

/** "2026-09-01 14:02" */
export function fmtDateTime(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return `${d.toLocaleDateString()} ${d.toLocaleTimeString(undefined, { hour12: false })}`;
}

/** "3m ago", "yesterday", "2d ago" — relative to now. */
export function fmtRelative(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const sec = (Date.now() - d.getTime()) / 1000;
  if (sec < 60) return "just now";
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  if (sec < 172800) return "yesterday";
  if (sec < 2592000) return `${Math.floor(sec / 86400)}d ago`;
  return d.toLocaleDateString();
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
