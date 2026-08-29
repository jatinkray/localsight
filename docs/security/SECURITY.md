# Security Architecture & Controls

Maps the plan's security requirements to concrete implementations in this codebase.

## Authentication
- **Argon2id** password hashing (`packages/security/passwords.py`).
- **JWT access + refresh with rotation**: access short-lived (15m) carries roles +
  permissions; refresh tokens are server-side tracked, single-use, and rotate on
  every use (`packages/security/jwt.py`, `routers/auth.py`). Replayed refreshes are
  rejected (`replaced_by`).
- **Account lockout** after `MAX_LOGIN_ATTEMPTS` (5) for `LOCKOUT_MINUTES` (15).
- **TOTP MFA** (RFC 6238, stdlib-only) with step-up login; secret encrypted at rest.
- **No plaintext secrets**: passwords/tokens/MFA secrets are never logged.

## Authorization (RBAC, enforced server-side)
- Roles: `ADMIN | SECURITY_OPERATOR | ANALYST | VIEWER` with atomic permissions
  (`camera:view`, `person:enroll`, `video:export`, `audit:view`, `user:manage`, …).
- Enforcement via `require_permission(...)` dependency — never client-supplied.
- A viewer cannot enroll/delete identities; only `user:manage` can grant roles.

## Encryption
- **Envelope encryption** (`packages/security/crypto.py`): per-record data key wrapped
  by a KEK from env/secrets. Used for embeddings, snapshots, stream URLs, NVR creds,
  MFA secrets. Keys are kept separate from the DB. KEK rotation supported.
- **In transit**: TLS everywhere (nginx terminates; HSTS in prod). Never transmit
  credentials over plaintext.

## Audit logging
- Immutable-style `audit_logs` for login, enrollment, deletion, export, config
  changes, etc. Captures `user, action, resource, result, source_ip, request_id`.
- No passwords/tokens/biometrics in logs. Viewable only with `audit:view`.

## SSRF / egress guard
- `packages/security/ssrf.py` rejects loopback/private/link-local/metadata
  destinations for operator-supplied camera URLs unless explicitly allow-listed.
- Pair with network-level egress restrictions (see threat model).

## API & media security
- Rate limiting (per-IP token bucket), request size/timeout limits, pagination caps.
- **Path-traversal-safe storage**: keys validated; no `../` escapes (`storage/local.py`).
- **Signed, expiring media URLs**; no permanent public links (`/api/video/{key}`).
- Security headers (CSP, HSTS, X-Frame-Options, etc.) via middleware.
- FFmpeg invoked with **structured argv, no shell** → no command injection
  (`packages/video/ffmpeg.py`).

## AI security
- Models are dependencies: each carries name/version/hash/source/license and is
  verified against the registry before load (`packages/ai/registry.py`).
- Inference isolated from public network; runs locally.

## Hardening defaults
- App refuses to boot without `JWT_SECRET`/`MASTER_ENCRYPTION_KEY`.
- Biometric recognition disabled by default (`AI_IDENTITY_RECOGNITION_ENABLED=false`).
- No automatic identity creation from footage; explicit enrollment only.
