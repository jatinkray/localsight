# Operations Runbook

## Vendor integrations
- **TP-Link VIGI / Tapo**: RTSP/ONVIF-native. `POST /api/cameras/from-nvr` provisions a whole VIGI NVR in one call; `GET /api/cameras/presets` lists URL templates. Setup, ports, auth, and caveats: `docs/integrations/tplink-vigi.md`.
- **Multi-vendor**: Any RTSP/ONVIF camera works. Use `POST /api/cameras/onvif/discover` to find devices on the LAN, then `POST /api/cameras/onvif/streams` to get RTSP URIs. Vendor presets available for Axis, Hanwha, Hikvision (ISAPI), Dahua (CGI), Reolink, Bosch, ONVIF, and GB/T 28181.

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
`BOOTSTRAP_ADMIN_PASSWORD` in `.env` (default `admin@localvision.local` /
`CHANGE_ME_STRONG_PASSWORD` in `.env.example`). For local debugging:

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
- `GET /api/live/streams` — active LL-HLS transcodes and their PIDs (authed).

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

## AI detector backends

Configure via `AI_DETECTOR` (environment):

| Value | Runtime | Model needed |
|-------|---------|---------------|
| `reference` (default) | CPU, no GPU | None — frame-differencing fallback |
| `onnx` | CPU / CUDA | YOLO/RT-DETR ONNX file staged via `ModelRegistry` |
| `tensorrt` | NVIDIA GPU | TensorRT engine file |
| `openvino` | Intel CPU/iGPU/NPU | OpenVINO IR |
| `tflite` | ARM / Coral TPU | TFLite model |

Set `AI_MODEL_NAME` and `AI_MODEL_VERSION` to match the registry entry. The
`reference` backend works out of the box on any CPU with no model download.

## Alert channels

LocalVision routes analytic events to four channels, each configured via
`POST /api/alerts/routes`:

### Webhook
```json
{
  "rule_type": "intrusion",
  "channel": "webhook",
  "config": {"url": "https://hooks.example.com/localsight"},
  "cooldown_sec": 300
}
```
HTTP POST with JSON body; URL is SSRF-validated against `SSRF_ALLOWLIST`.

### MQTT
```json
{
  "rule_type": "anpr",
  "channel": "mqtt",
  "config": {
    "host": "192.168.1.100", "port": 1883,
    "topic": "localsight/{camera_id}/alerts",
    "username": "mqtt_user", "password": "s3cret",
    "qos": 1, "retain": false
  },
  "cooldown_sec": 60
}
```
Publishes a JSON message per alert to the configured topic. Template variables
`{camera_id}` and `{rule_type}` are expanded.

### Push (ntfy.sh)
```json
{
  "rule_type": "*",
  "channel": "push",
  "config": {
    "server": "https://ntfy.sh",
    "topic": "localsight-alerts",
    "priority": 4,
    "tags": ["security", "camera"],
    "click": "https://dashboard.example.com/events"
  }
}
```
Delivers to ntfy.sh. Configure `auth_token` for private topics.

### Email
```json
{
  "rule_type": "intrusion",
  "channel": "email",
  "config": {
    "smtp_host": "smtp.example.com", "smtp_port": 587,
    "from": "alerts@example.com", "recipients": ["ops@example.com"]
  },
  "cooldown_sec": 120
}
```

### Alert cooldown
Every route has a `cooldown_sec` field. Within the cooldown window, the same
(channel × rule_type × camera_id) key suppresses re-firing. This prevents
alert storms when the same event fires repeatedly. A cooldown of `0` disables
suppression.

### Test alert delivery
```bash
# Verify all routes without touching a camera
curl -X POST http://localhost:8000/api/alerts/test -H "Authorization: Bearer $TOKEN"
# => {"delivered": 2}
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
- **Alert storm**: set `cooldown_sec` on routes to suppress rapid re-firing.
- **MQTT not connecting**: verify broker is reachable, credentials are correct,
  and topic template is valid. Unreachable broker is silently skipped (no crash).
- **ntfy push not working**: check `server` URL, `topic` name, and that the auth_token
  is set for private topics. An unreachable server returns 0 delivered.
- **Storage full**: alert fires at threshold; the worker retention sweeper deletes
  expired data automatically — keep the worker running; add capacity if needed.
- **Token reuse errors**: refresh rotation revoked a token; re-login.
- **Decoding failures**: ensure FFmpeg present (in Docker image); run worker non-root
  with resource limits.
- **Live stream PID 0 / not running**: ffmpeg may not be on PATH; check `/api/live/streams`
  returns `"running": false`. Install ffmpeg or check that the camera's substream URL is reachable.
- **Event clip returns no segments**: the event has no overlapping `VideoSegment` rows in its
  time window. Check that recording is enabled (`RECORD_ENABLED=true`) and segments exist.

## Secure deployment checklist
- [ ] Unique secrets generated (never placeholders); KEK backed up in secrets manager.
- [ ] TLS 1.3 + HSTS at the proxy; redirect HTTP→HTTPS.
- [ ] Cameras on isolated VLAN; only RTSP from camera→server; no internet egress.
- [ ] `SSRF_ALLOWLIST` set to the camera VLAN CIDR.
- [ ] Biometric recognition enabled only after lawful basis + approval.
- [ ] MFA enforced for admins/operators.
- [ ] Audit log shipped to SIEM; retention policy applied.
- [ ] Backups (DB+key) tested.
