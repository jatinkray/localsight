# API Summary

Base path `/api`. All responses are JSON. All mutating/protected endpoints require
`Authorization: Bearer <access_token>`. Pagination: `limit` (≤500), `offset`.

## Auth
| Method | Path | Auth | Notes |
|--------|------|------|-------|
| POST | `/api/auth/login` | — | email+password(+mfa); rate-limited; returns access+refresh |
| POST | `/api/auth/refresh` | refresh token | rotates token; old refresh revoked |
| POST | `/api/auth/logout` | refresh token | revokes refresh |
| GET  | `/api/auth/me` | user | current user + permissions |
| POST | `/api/auth/mfa/setup` | user | returns TOTP secret + otpauth URI |
| POST | `/api/auth/mfa/verify` | user | enables MFA |

## Cameras / NVR
| Method | Path | Permission |
|--------|------|-----------|
| GET/POST | `/api/cameras` | `camera:view` / `camera:configure` |
| GET | `/api/cameras/presets` | `camera:view` (vendor RTSP/ONVIF URL templates, incl. TP-Link) |
| POST | `/api/cameras/from-nvr` | `camera:configure` (provision a VIGI NVR + all channels) |
| GET/PUT/DELETE | `/api/cameras/{id}` | `camera:view` / `camera:configure` |
| GET/POST | `/api/nvr` | `camera:configure` |

Camera stream URLs are **SSRF-validated** and **encrypted at rest**; they are never
returned to clients.

## People / identity
| Method | Path | Permission |
|--------|------|-----------|
| GET/POST | `/api/persons` | `person:view` / `person:enroll` |
| DELETE | `/api/persons/{id}` | `person:delete` |
| POST | `/api/persons/{id}/references` | `person:enroll` (upload reference image → local embedding) |

## Events / search / timeline
| Method | Path | Permission |
|--------|------|-----------|
| GET | `/api/events` | `events:view` (filters: camera/identity/status/time/confidence) |
| GET | `/api/events/{id}` | `events:view` (returns signed snapshot/video URLs) |
| GET | `/api/events/{id}/export` | `events:export` (audited, signed URL) |
| GET | `/api/timeline?date=&camera_id=` | `events:view` |

## Users (admin)
| Method | Path | Permission |
|--------|------|-----------|
| GET/POST | `/api/users` | `user:manage` |
| DELETE | `/api/users/{id}` | `user:manage` |

## Audit
| Method | Path | Permission |
|--------|------|-----------|
| GET | `/api/audit` | `audit:view` |

## System
| Method | Path | Auth |
|--------|------|------|
| GET | `/health/live` | — |
| GET | `/health/ready` | — |
| GET | `/api/system/health` | user |
| GET | `/api/system/metrics` | user (Prometheus text) |

## Media
| Method | Path | Permission |
|--------|------|-----------|
| GET | `/api/video/{key}?exp=&sig=` | `video:view` (HMAC-signed, expiring) |

Full interactive docs: `GET /docs` (FastAPI Swagger UI) when running.
