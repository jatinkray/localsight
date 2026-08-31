# Operations Runbook

## Vendor integrations
- **TP-Link VIGI / Tapo**: RTSP/ONVIF-native. `POST /api/cameras/from-nvr` provisions a whole VIGI NVR in one call; `GET /api/cameras/presets` lists URL templates. Setup, ports, auth, and caveats: `docs/integrations/tplink-vigi.md`.

## Local run (no containers)
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/gen_env.py
uvicorn apps.api.main:app --host 0.0.0.0 --port 8000
python -m apps.worker          # in a second shell
```

## Docker Compose
```bash
python scripts/gen_env.py
docker compose -f infrastructure/compose/docker-compose.yml --env-file .env up --build
```
- Postgres+pgvector, API, worker, nginx (TLS) come up. Data persists in named volumes
  `pgdata` and `storage`.
- To use real models, mount `./models` (already mounted) and add a `ModelRegistry`
  entry; set `AI_DETECTOR` accordingly.

## Local debug access
The bootstrap admin is created once on first run from `BOOTSTRAP_ADMIN_EMAIL` /
`BOOTSTRAP_ADMIN_PASSWORD` in `.env` (default `admin@localvision.local` / `CHANGE_ME_STRONG_PASSWORD`
in `.env.example`). For local debugging:

- **UI**: open `http://localhost:8000` and sign in with those credentials.
- **Token (curl)**:
  ```bash
  TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
    -H 'Content-Type: application/json' \
    -d '{"email":"admin@localvision.local","password":"CHANGE_ME_STRONG_PASSWORD"}' \
    | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
  curl http://localhost:8000/api/auth/me -H "Authorization: Bearer $TOKEN"
  ```
- **Swagger**: `http://localhost:8000/docs` → click **Authorize** and paste the bearer token.

To change the bootstrap password, edit `.env` and restart; the admin is only created
when no users exist, so for an already-initialized DB use the `user:manage` endpoint
or reset the database. Rotate `JWT_SECRET`/`MASTER_ENCRYPTION_KEY` (and re-encrypt
embeddings) before any non-local deployment — never reuse the generated dev keys.

## Health & readiness
- `GET /health/live` — process alive.
- `GET /health/ready` — DB + storage reachable.
- `GET /api/system/health` — component statuses (authed).
- `GET /api/system/metrics` — Prometheus exposition (authed).

## Retention (configurable, automatic)
| Data | Default |
|------|---------|
| Recordings | 7 days |
| Events | 30 days |
| Snapshots | 14 days |
| Embeddings | 90 days |
| Audit | 365 days |

Retention is enforced **automatically** by the worker: a background sweeper deletes
expired `VideoSegment` rows **and their backing storage objects**, expired `Event`
rows, and expired `Snapshot` rows every hour, using the per-policy env vars above.
You do not need a separate cron job. All deletions are auditable. Per-camera overrides
are supported via the camera `retention` JSON field.

> Warning: if the worker is not running, retention is not applied and storage/DB grow.
> Keep the worker process up (it also runs recording, analytics, and the alert sender).

## Backup & restore (test before relying on it)
- **Database + identity metadata + encrypted embeddings + config + audit**: `pg_dump`
  (or SQLite `cp`). Restore into a fresh DB and verify row counts.
- **Encrypted embeddings are useless without `MASTER_ENCRYPTION_KEY`** — back up the
  key in your secrets manager separately from the DB.
- **Video**: large; back up per retention policy / cold storage. Document and test a
  restore at least once.

## Capacity
```bash
python scripts/capacity.py --cameras 8 --main-bitrate-mbps 4 --sub-bitrate-mbps 0.4 --ai-fps 5
```

## Troubleshooting
- **App won't start**: check for placeholder secrets (`JWT_SECRET`/`MASTER_ENCRYPTION_KEY`).
- **Camera OFFLINE**: gateway reconnects with backoff; check RTSP URL + SSRF allowlist
  (private/loopback blocked unless listed) and NVR reachability.
- **GPU missing**: reduce `AI_INFERENCE_FPS`. The `reference` detector needs no GPU;
  staged ONNX detectors use CUDA automatically when a GPU is present, CPU otherwise.
- **No recordings / live view**: both require **FFmpeg** on `PATH` (bundled in the
  Docker image). Confirm `ffmpeg` is installed locally and that the camera has a
  configured main/sub stream URL.
- **Alerts not arriving**: the worker runs the alert sender as a background service;
  verify routes via `GET /api/alerts/routes` and that webhook URLs are on the SSRF
  allowlist. Use `POST /api/alerts/test` to validate delivery.
- **Storage full**: alert fires at threshold; the worker retention sweeper deletes
  expired data automatically — keep the worker running; add capacity if needed.
- **Token reuse errors**: refresh rotation revoked a token; re-login.
- **Decoding failures**: ensure FFmpeg present (in Docker image); run worker non-root
  with resource limits.

## Secure deployment checklist
- [ ] Unique secrets generated (never placeholders); KEK backed up in secrets manager.
- [ ] TLS 1.3 + HSTS at the proxy; redirect HTTP→HTTPS.
- [ ] Cameras on isolated VLAN; only RTSP from camera→server; no internet egress.
- [ ] `SSRF_ALLOWLIST` set to the camera VLAN CIDR.
- [ ] Biometric recognition enabled only after lawful basis + approval.
- [ ] MFA enforced for admins/operators.
- [ ] Audit log shipped to SIEM; retention policy applied.
- [ ] Backups (DB+key) tested.
