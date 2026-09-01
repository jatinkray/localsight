# LocalVision User Guide

End-user documentation for operators of a LocalVision deployment: logging in,
managing cameras, watching live and recorded video, working with events and
alerts, and configuring privacy controls. For developer/agent documentation
see `AGENTS.md`; for operations (deployment, retention, troubleshooting) see
`docs/operations/runbook.md`.

## Contents
1. [Logging in](#logging-in)
2. [Dashboards at a glance](#dashboards-at-a-glance)
3. [Managing cameras](#managing-cameras)
   - [Privacy masks](#privacy-masks)
   - [Behavior rules](#behavior-rules)
   - [Per-camera retention](#per-camera-retention)
4. [Live view](#live-view)
5. [Events, search & clips](#events-search--clips)
6. [Alerts](#alerts)
7. [People & enrollment](#people--enrollment)
8. [What is encrypted, and where](#what-is-encrypted-and-where)

---

## Logging in

1. Browse to the deployment URL (the dashboard is served at `/`).
2. Sign in with the account your administrator created (first boot creates
   the bootstrap admin from `BOOTSTRAP_ADMIN_EMAIL`/`BOOTSTRAP_ADMIN_PASSWORD`).
3. Sessions are 15-minute access tokens with rotating refresh tokens — if a
   session "logs out" while you're working, that's rotation working; log in again.
4. Multi-factor authentication (TOTF-compatible authenticator apps) can be
   enabled per-account under Account → MFA. Admin accounts should enable it.

Failed logins lock the account for `LOCKOUT_MINUTES` after
`MAX_LOGIN_ATTEMPTS` failures. Contact your administrator to unlock.

## Dashboards at a glance

The web UI is a single-page dashboard served from the deployment URL:

- **Events** — detections and analytic events with filters by camera, time,
  identity status.
- **Live** — start/stop live views per camera (requires the `live:view`
  permission).
- **Cameras** — add/edit cameras, configure privacy masks and rules.
- **Audit** — every sensitive action (logins, exports, camera changes) with
  who/when/source IP (requires the `audit:view` permission).

What you see depends on your role's permissions; endpoints you lack
permission for return 403.

## Managing cameras

Cameras are added by an administrator (`camera:configure` permission) via
`POST /api/cameras` or the dashboard. Stream URLs (RTSP, with or without
embedded credentials) are validated against the deployment's egress allowlist,
then **encrypted at rest** — they are never returned by the API.

Quick discovery helpers:
- `POST /api/cameras/onvif/discover` — WS-Discovery on the camera VLAN.
- `POST /api/cameras/presets/build` — build a vendor RTSP URL from a preset
  (Axis, Hanwha, Hikvision, Dahua, Reolink, Bosch, TP-Link VIGI, ...).
- `POST /api/cameras/from-nvr` — provision every channel of a TP-Link VIGI NVR.

### Privacy masks

Privacy masks exclude regions of the frame from **all** processing: a
detection whose center falls inside a mask — or whose box overlaps a mask by
≥50% — is dropped before tracking. Masked areas produce no detections, no
snapshots, no events, no recordings metadata — nothing downstream.

Configure on a camera (`PUT /api/cameras/{id}`):

```json
{
  "privacy_masks": [
    {"x": 0.0, "y": 0.0, "w": 0.3, "h": 0.25},
    {"x": 0.6, "y": 0.7, "w": 0.2, "h": 0.3}
  ]
}
```

Coordinates are normalized (0.0–1.0) against the full frame, origin
top-left. Typical uses: a neighbor's property line, a public sidewalk beyond
the fence, a monitor screen in view.

**Verify masks took effect**: the change applies to new frames immediately
(worker picks it up on camera reload); run a test detection and confirm no
events appear from the masked region.

### Behavior rules

Each camera can carry JSON rules evaluated per frame by the worker
(`camera:configure` permission):

| Rule type | Fires when | `detail` carried on the event |
|-----------|------------|-------------------------------|
| `zone_intrusion` | A track enters the polygon | `zone` |
| `loitering` | Presence inside a zone exceeds the threshold | `dwell_sec`, `zone` |
| `line_cross` | A track crosses the segment (optional direction) | `direction` |
| `object_left` | A stationary object appears and persists | `stationary_sec` |
| `crowd` | Track count in a zone exceeds N | `count`, `zone` |

```json
{
  "rules": [
    {"type": "zone_intrusion", "rule_id": "backyard", "zone": [[0.5,0.3],[1.0,0.3],[1.0,1.0],[0.5,1.0]]},
    {"type": "line_cross", "rule_id": "entry-tripwire", "a": [0.5,0.0], "b": [0.5,1.0], "direction": 1}
  ]
}
```

`direction`: `1` left-to-right, `-1` right-to-left, omitted = both.

### Per-camera retention

`PUT /api/cameras/{id}` accepts a `retention` object (e.g.
`{"days": 30}`) to override global policy for that camera's data. Global
defaults: recordings 7 days, events 30 days, snapshots 14 days, embeddings
90 days, audit 365 days.

## Live view

Live view streams the camera's substream, transcoded locally to LL-HLS.
The RTSP URL and camera credentials never reach your browser.

1. Open Live in the dashboard and pick a camera (or call
   `POST /api/live/ticket` then `GET /api/live/{camera_id}/play?ticket=...`).
2. The player points at `/live-media/{camera_id}/index.m3u8`.
3. Stop button (or `POST /api/live/{camera_id}/stop`) ends the transcode
   immediately.

You don't need to stop streams manually — the server reaps transcodes that
nobody has watched for 5 minutes (configurable) or that have run for 4 hours
(hard ceiling), and it stops everything on restart. This keeps CPU
proportional to actual viewing.

## Events, search & clips

- **Browse**: `GET /api/events` with filters (camera, identity, status,
  time range, confidence floor).
- **Event detail**: `GET /api/events/{id}` — includes short-lived signed
  URLs to the snapshot and any recorded segment (default 300 s expiry;
  links are HMAC-signed and expire — don't archive them).
- **Clips**: `GET /api/events/{id}/clip` assembles every recording segment
  overlapping the event window (requires `events:export`; the action is audited).
- **Natural-language search**: `GET /api/analytics/search?q=person+by+the+gate`
  — a reference (deterministic) semantic search until a real VLM is staged.
- **Timeline**: `GET /api/timeline?date=YYYY-MM-DD&camera_id=...` renders
  merged recording intervals with presence events and analytic markers.

Rule and ANPR events carry a `detail` object (direction, dwell, counts;
for ANPR: the encrypted plate and its anonymized hash — see
[What is encrypted](#what-is-encrypted-and-where)).

## Alerts

Administrators (`alerts:manage`) configure routes: channel (`webhook`,
`email`, `mqtt`, `push`), matching `rule_type` (or `*`), optional camera
scope, and `cooldown_sec` to suppress re-firing storms.

- Webhook URLs are SSRF-validated; the server refuses to POST to private
  ranges unless the operator allowlists them.
- Alert payloads sent to third-party channels are filtered: they carry the
  rule context (direction, dwell, count, zone) but **never** ciphertext like
  the encrypted plate — that data stays on the host.
- `POST /api/alerts/test` sends a synthetic alert through every configured
  route so you can verify delivery end-to-end.

## People & enrollment

Enrollment (biometric identity recognition) is **off by default** and should
be enabled only with a lawful basis. When enabled:

1. Create a person (`POST /api/persons`, `person:enroll` permission).
2. Enroll reference embeddings — stored envelope-encrypted, carrying their
   model version.
3. Recognition runs at `AI_RECOGNIZE_INTERVAL_SEC` intervals per track and
   labels presence events `known` / `uncertain` / `unknown` against the
   similarity threshold.

Deleting a person is a **complete erasure** (GDPR-style): their embeddings
cascade-delete with the person row. Events keep their `identity_status` label
but the link to the person is removed.

## What is encrypted, and where

| Data | At rest | In transit to you |
|------|---------|-------------------|
| Camera RTSP URLs | Envelope-encrypted (per-record data key under the deployment KEK) | Never returned by the API |
| Plate numbers (ANPR) | `Event.detail.plate_enc` (envelope-encrypted) + `plate_hash` (anonymized digest) | Only via authenticated host API |
| Enrollment embeddings | Envelope-encrypted | Never returned |
| Snapshots / recordings | Storage keys encrypted; objects served via short-lived signed URLs | Signed URL (≤ `expires_sec`) |
| Alert route secrets | Encrypted at rest | Never returned |
| Passwords | Argon2id hashes | n/a |
| Audit log | Append-only rows (retention-bounded) | `audit:view` permission |

Signed URLs are HMAC-SHA256 over `key:exp` with the deployment master key —
they cannot be forged, only issued by the server, and they expire.
