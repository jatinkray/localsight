# AGENTS.md — LocalVision Engineering Agent Guide

This document orients any engineering agent (AI or human) working in this
repository: what the system is, where things live, the invariants that must
never be violated, and the workflow expected of every change.

## What this system is

LocalVision is a **local-first video intelligence platform**: cameras on a
private LAN, AI inference on the customer's own hardware, no cloud dependency.
The security posture (local processing, envelope encryption, privacy by
design) is the product — treat regressions against it as functional bugs, not
style issues.

```
apps/api/        FastAPI app: routers, bootstrap (runtime), config, deps
apps/worker/     per-camera AI pipelines, recorder, retention, alert fan-out
packages/domain/ ORM models, schemas, timeutil, events
packages/security/ passwords, JWT, RBAC, crypto (envelope), SSRF, rate-limit, MFA, audit
packages/ai/     detector/tracker/face/matcher interfaces + reference impls + pipeline
                 detectors.py (ONNX/TensorRT/OpenVINO/TFLite), rules.py, anpr.py, vlm.py
packages/video/  frame sources, safe FFmpeg argv builder, stream gateway,
                 onvif, presets, recorder
packages/storage/ StorageProvider ABC + local + S3 implementations, signed URLs
packages/notify/ webhook/email/push/MQTT alert channels + routing
packages/observability/ metrics registry + structured logging
ui/              vanilla-JS dashboard (served at /)
infrastructure/  Dockerfile, compose stack, nginx, monitoring
docs/            architecture, security, operations, integrations, api, reviews
scripts/         gen_env.py, capacity.py, seed_dev_data.py, ui_audit*.py (Playwright)
tests/           unit + security + API + integration
```

## Architectural rules (do not break)

1. **Dependency direction**: `apps/` → `packages/`, never `packages/` → `apps/`.
   Cross-package work happens through interfaces (`StorageProvider`, `Detector`,
   `Notifier`), which are ABCs — implement ALL abstract methods in any new
   backend, and add new abstract methods only with default-free care (see
   `verify_signed_url`/`put_stream` on StorageProvider for why completeness is
   enforced).

2. **Secrets never leave the host in plaintext**:
   - RTSP URLs, plate text, embeddings → envelope-encrypted via `CryptoBox`
     before storage.
   - Media delivery → app-relative signed URLs (`/api/video/...?exp=...&sig=...`),
     HMAC-SHA256 over `key:exp` with the master key. Never return absolute S3
     URLs to a client; the app proxies remote objects.
   - Webhook/email/MQTT payloads → filter `Event.detail` through
     `_ALERT_DETAIL_KEYS` (worker) — ciphertext (`plate_enc`) never goes to
     third-party channels.

3. **Frontend is CSP-native and XSS-proof by construction** (`ui/`):
   - The server sends `style-src 'self'` — inline `style=` and `<style>` are
     BLOCKED. Data-driven geometry (timeline, charts) must use **SVG
     attributes** (`x`, `width`) or CSS classes, never inline styles. The old
     timeline rendered 0-width segments for exactly this reason (audit C-1).
   - No `innerHTML` with user/API data, ever. Build DOM via `ui/core/dom.js`
     (`h()`, `svgEl()`, `render()`); the `html` prop is forbidden by design.
     Stored XSS via person labels was live before this (audit C-2).
   - No build step, no framework: ES modules served statically. One allowed
     third-party asset: ui/vendor/hls.light.min.js (LL-HLS in Chromium,
     lazy-loaded only on Live view; Safari uses native HLS). Keep total
     payload small; performance on edge hardware is a product feature.
   - Session handling: access token in memory ONLY; refresh token in
     `sessionStorage` (rotates server-side on every use). Never persist an
     access token to `localStorage`. `ui/core/api.js` is the single fetch
     layer — all API calls go through it (silent refresh-on-401 included).

4. **Subprocess safety**: FFmpeg/ffprobe are invoked with argv lists built
   in-code (`packages/video/ffmpeg.py`), never `shell=True`, and every
   operator-supplied URL passes `validate_egress_url` first. Always
   `terminate()` **and** `wait()` — an unreaped child is a zombie (see
   `sources.py`, `recorder.py`).

5. **Storage streaming**: large media moves through `put_stream` — never read
   a recording into the heap. A 4 Mbps 300 s segment is ~150 MB; high-bitrate
   NVRs exceed 1 GB per segment.

6. **Privacy masks are load-bearing**: `Camera.privacy_masks` (normalized
   `{x,y,w,h}` rectangles) suppress detections whose center falls in the mask
   or with ≥50% bbox overlap (`CameraPipeline._is_masked`). If you touch the
   detection path, masks must still be applied BEFORE tracking, and
   `test_privacy_masks_suppress_detections` must pass.

7. **Retention is compliance**: the worker's `_sweep_retention` enforces every
   declared knob (recordings, events, snapshots, embeddings, audit) plus
   expired refresh tokens. New persistent data classes must join this sweep
   or get their own documented lifecycle.

8. **Deletion cascades are DB-enforced**: person → embeddings (GDPR erasure),
   user → refresh tokens, camera → detections/tracks/events/segments. New
   child tables of existing entities get `ondelete=` on the FK plus storage
   cleanup where media objects are involved (see `delete_camera`).

## Key runtime facts

- **Dev**: SQLite, tests run against an in-memory-ish session-scoped app
  (`conftest.py`); `.venv` at repo root; `pytest tests/ -q` must pass
  (currently 77 tests).
- **Dev-parity FK enforcement**: `bootstrap.build` enables
  `PRAGMA foreign_keys=ON` on SQLite so cascade/integrity behavior matches
  PostgreSQL. Never remove this — it's what keeps dev bugs from hiding until
  production.
- **Prod (compose)**: PostgreSQL + pgvector, `DATABASE_URL=postgresql+psycopg://`
  (driver installed via `requirements-prod.txt`), nginx TLS frontend.
- **Schema evolution**: `Base.metadata.create_all` + `bootstrap._ensure_columns`
  for additive columns on existing tables. Each ALTER runs in its own
  transaction; only "duplicate column"/"already exists" errors are swallowed.
  Alembic is the intended destination (see docs/reviews report D-5) — additive
  changes must be added to `_ensure_columns` until then.
- **Worker model**: one thread per camera, ffmpeg per thread; SIGTERM sets the
  stop event so recorders flush and children are reaped. The alert sender and
  retention sweeper run on their own daemon threads.
- **Live view**: transcodes are tracked in `_live_streams` with idle/max-age
  reaping; `LOCALVISION_LIVE_DIR` sets the shared root for both the ffmpeg
  output and the `/live-media` mount (single source: `apps/api/domain_live_cfg.py`).

## Authentication & authorization quick reference

- Argon2id passwords; JWT access (15 min) + rotating refresh tokens tracked
  server-side; TOTP MFA (stdlib, RFC 6238).
- Login always performs exactly ONE Argon2 verify (fixed `_DUMMY_HASH` for
  nonexistent accounts) — do not "optimize" this into a branch skip; it's the
  user-enumeration defense.
- RBAC: roles → permissions (`packages/security/rbac.py`); endpoints declare
  `require_permission("...")`. Permission names live in the RBAC tables.
- Rate limiting: in-process token bucket keyed by client IP; `X-Forwarded-For`
  is only honored where a trusted proxy front sits.

## Quality gates

- `pytest tests/ -q` — all green (77+).
- `ruff check .` — `ruff.toml` defines the rule set; keep changed files clean,
  don't mass-reformat untouched files.
- `mypy packages apps --ignore-missing-imports` — keep new code typed
  (`Mapped[]`, `| None` unions).
- CI (`.github/workflows/ci.yml`): lint, unit, PostgreSQL integration, dep
  audits, CodeQL + Semgrep SAST, Trivy container scan.

## Workflow for any change

1. Branch from `main` (`fix/...`, `feat/...`, `docs/...`).
2. Read the relevant module top-to-bottom before editing; docstrings carry
   the security rationale (e.g. why argv lists, why dummy hashes).
3. Write the fix + the test that would have caught the bug. The existing
   suite missed real production defects because paths were unexercised —
   regression tests for any fix are mandatory (see the F-01 tests).
4. Run the full suite; check `git status` for accidental artifacts (dbs,
   coverage files — now gitignored).
5. Update docs for user-visible changes: README capabilities, `docs/api/`
   when endpoints change, `docs/operations/runbook.md` for ops procedures.
6. Conventional Commits (`fix:`, `feat:`, `security:`, `perf:`, `docs:`, ...).

## Known reference implementations (intentional placeholders)

Detection (`ReferenceMotionDetector`), ANPR OCR, embeddings (`ReferenceEmbedder`),
and VLM search are deterministic placeholders — functional but not
production-accurate. Real backends arrive via the `ModelRegistry`
(`models/registry.json`, SHA-256-verified). Do NOT "fix" reference
implementations to be smarter; swap them via the interfaces.

## Where the bodies are buried

`docs/reviews/CODE_ANALYSIS_REPORT.md` is the full architectural review with
evidence, impact, and fix rationale for every recent change (F-01 … F-14,
D-1 … D-7, M-1 … M-38). Read it before large refactors; it explains why the
code looks the way it does now.

`docs/reviews/UI_UX_AUDIT_AND_REDESIGN_PLAN.md` is the UI/UX counterpart:
a Playwright-measured audit (findings C-1 … C-14) and the phased enterprise
redesign. Waves 0 (trust repairs), 1 (investigation loop), 2 (Monitor:
live grid + wall mode, auto-refreshing overview), and 3 (Manage: camera
grid + detail tabs, mask/rules editors, add-camera wizard, identities,
alerts admin, users, privacy dashboard) are DONE. Wave 4 (analytics &
polish) follows the same pattern. Each wave ships its own Playwright probe:
`scripts/ui_probe_flows.py` (W0), `ui_probe_wave1.py`, `ui_probe_wave2.py`,
`ui_probe_wave3.py` — run the matching probe for any view you touch; they
are self-provisioning (create their own throwaway users) and assert the
audit findings stay fixed. Frontend conventions for new views live in
`ui/WAVE3_CONVENTIONS.md` (CSP-safe SVG geometry, h()/render() DOM
construction, RBAC gating, honest empty/error states, write-only
credentials, typed-confirm deletes). The
audit scripts reproduce any finding: `scripts/ui_audit.py`,
`scripts/ui_design_metrics.py`, `scripts/ui_probe_flows.py` (see the report's
Appendix B).
