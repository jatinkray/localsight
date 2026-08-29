# LocalVision

Local-first, privacy-by-design video intelligence platform. Continuously ingests
RTSP/RTSPS streams from NVRs/IP cameras, detects and tracks people with **local**
AI models, optionally identifies known individuals via privacy-preserving biometric
processing, generates timestamped events, and serves a secure web dashboard for
searching and reviewing a day's activity.

Designed for 24/7 on-premises operation: **no video leaves the site by default**,
the system runs fully offline, and every security-sensitive action is encrypted at
rest and audited.

## Principles (non-negotiable)

1. **Local-first / cloud-optional** — all AI inference is local. Cloud is opt-in only.
2. **Privacy by design** — embeddings/identities are encrypted; recognition is off by default.
3. **Minimum video movement** — AI runs on the low-res substream; main stream is recorded.
4. **Security-first** — Argon2id, RBAC, envelope encryption, SSRF guard, audit log, signed URLs.
5. **Fail safe** — one camera dying never takes down the platform; automatic reconnect + backoff.

## Quick start (local, zero infra)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/gen_env.py        # writes .env with fresh random secrets
uvicorn apps.api.main:app --host 0.0.0.0 --port 8000 --reload
# open http://localhost:8000  (dashboard served at /)
```

The app **refuses to start** if `JWT_SECRET` / `MASTER_ENCRYPTION_KEY` are missing
or still placeholders — there are no insecure defaults.

Default DB is SQLite (`sqlite:///./localvision.db`); switch to PostgreSQL by setting
`DATABASE_URL=postgresql+psycopg://...` (see compose for a pgvector setup).

**Local debug login:** bootstrap admin is `admin@localvision.local` / `CHANGE_ME_STRONG_PASSWORD`
(from `.env.example`). See `docs/operations/runbook.md` → *Local debug access* for the
curl/Swagger flow.

Run the AI/video worker (separate process) in another shell:

```bash
python -m apps.worker          # processes cameras via synthetic source when no stream is configured
```

## Tests

```bash
pip install pytest
pytest -q                       # 23 tests: auth, RBAC, SSRF, encryption, aggregation, pipeline, API
```

## Capacity planning

```bash
python scripts/capacity.py --cameras 8 --main-bitrate-mbps 4 --sub-bitrate-mbps 0.4 --ai-fps 5
```

## Deployment (Docker Compose)

```bash
python scripts/gen_env.py
docker compose -f infrastructure/compose/docker-compose.yml --env-file .env up --build
```

Brings up PostgreSQL+pgvector, the API, the AI worker, and an nginx TLS proxy.

## Repository layout

```
apps/
  api/        FastAPI app: routers, config, db, bootstrap, dependencies
  worker/     AI/video worker: per-camera pipelines, stream gateway
packages/
  domain/     ORM models, Pydantic schemas, event aggregation, time utils
  security/   passwords, JWT, RBAC, crypto, SSRF, rate-limit, MFA, audit, headers
  ai/         swappable Detector/Tracker/Face/Embedder/Matcher + reference impls + pipeline
  video/      frame sources, safe FFmpeg invocation, resilient stream gateway
  storage/    StorageProvider (local + S3), signed expiring URLs, path-traversal safe
  observability/  Prometheus metrics + structured JSON logging
infrastructure/  docker, compose, nginx, monitoring
docs/             architecture, security, operations, api
scripts/          gen_env.py, capacity.py
ui/               static dashboard (served at /)
tests/            unit + security + integration
```

## Status vs. plan

Implemented and tested: auth (Argon2id, JWT rotation, MFA, lockout), RBAC,
envelope encryption, immutable audit log, SSRF egress guard, signed media URLs,
path-traversal-safe storage, event aggregation, pluggable AI pipeline (reference
impl runs without GPU/ffmpeg), camera/NVR management, persons/enrollment, events
search, timeline, capacity model, Docker Compose, and a runnable dashboard.

Deliberately **reference** (swappable, not production-tuned): person/face models
are deterministic placeholders so the system runs on any machine; drop in
YOLO/ONNX detectors and a real face model via the same interfaces. Recording of
real video requires FFmpeg (installed in the Docker image) and a camera substream.

See `docs/` for the architecture decision records, threat model, ERD, security
controls, and the operations runbook.
