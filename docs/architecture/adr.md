# Architecture Decision Records (ADR)

## ADR-1: Modular monolith + dedicated workers (not microservices)
**Decision:** Ship a FastAPI modular monolith for the control/API plane plus a
separate AI/video worker process. Share Postgres + object storage; communicate via
the DB (and WebSocket/SSE later for live events).
**Rationale:** The spec itself recommends this for the first production version.
It minimizes operational complexity and avoids distributed-systems overhead while
still isolating GPU/FFmpeg failures from the API.
**Consequence:** Worker and API must agree on the schema; use Alembic for evolution.

## ADR-2: SQLite by default, PostgreSQL/pgvector via one switch
**Decision:** Same SQLAlchemy 2.0 models run on SQLite (zero-dependency local run)
and PostgreSQL+pgvector (production). Selected by `DATABASE_URL`.
**Rationale:** Lets the system boot and be tested with no external services, while
production gets real concurrency, partitioning, and vector search.
**Consequence:** Avoid DB-specific types in core code; pgvector is an optional
upgrade path for the embeddings index.

## ADR-3: AI is a set of swappable interfaces
**Decision:** Define `Detector`, `Tracker`, `FaceDetector`, `FaceEmbedder`,
`IdentityMatcher`. Ship a reference implementation that runs with no GPU/model.
**Rationale:** Avoids hard-coding the app to one model; lets us benchmark and
replace models without API changes.
**Consequence:** Production must supply a real model + registry entry; the reference
detector must never be used where accurate detection is required.

## ADR-4: Envelope encryption with an external KEK
**Decision:** Encrypt embeddings, snapshots, stream URLs, and NVR credentials with
per-record data keys wrapped by a master KEK supplied via env/secrets manager.
**Rationale:** Key/data separation; a DB dump or stolen disk reveals only ciphertext.
**Consequence:** KEK must be managed securely (Vault/Docker secrets); rotation
re-wraps without re-encrypting bulk data.

## ADR-5: No insecure secret defaults
**Decision:** App refuses to boot if `JWT_SECRET`/`MASTER_ENCRYPTION_KEY` are empty
or placeholders.
**Rationale:** Prevents accidental insecure deployments.
**Consequence:** Operators must generate secrets (`scripts/gen_env.py`).

## ADR-6: SSRF guard on all operator-supplied URLs
**Decision:** Validate camera/NVR destinations before connecting; reject
loopback/private/link-local/metadata unless explicitly allow-listed.
**Rationale:** A camera config UI is a natural SSRF pivot.
**Consequence:** Must be paired with network-level egress restrictions.

## ADR-7: Reference detector + synthetic frame source for runnability
**Decision:** When no FFmpeg/model is available, use a synthetic frame source and a
deterministic reference detector so the full pipeline runs and is testable.
**Rationale:** Demonstrates and tests the architecture end-to-end without GPUs.
**Consequence:** Not a substitute for a real model in production.

## ADR-8: Signed, expiring media URLs; no public links
**Decision:** Video/snapshot delivery uses HMAC-signed URLs with short TTL; access
also requires an authenticated session with `video:view`.
**Rationale:** Prevents permanent/guessable video exposure.
**Consequence:** Clients must re-request URLs; add a CDN/short-link pattern if needed.
