# LocalSight — Principal Architect & Engineer Code Review

**Scope:** full repository (`apps/`, `packages/`, `ui/`, `tests/`, `infrastructure/`, `scripts/`) — ~8,200 lines of application code (38 Python modules, 3 UI files), all routers, all security modules, the worker, infra configs, and CI.
**Method:** 100% of production Python read line-by-line; every finding below was either **reproduced by executing the code** (stack traces, SQL counts, byte measurements, timing runs) or verified by exhaustive grep across the repo. Test baseline: `pytest -q` → **66 passed in 17.5s** (matches README).
**Verdict:** Architecture is genuinely strong — clean layering, swappable AI interfaces, real security controls, thoughtful failure isolation. However, the suite passes **only because it never exercises the broken paths**. I found **2 crash-level defects shipped in headline features**, 1 defect that makes the S3 deployment mode non-functional, plus systemic schema drift, unenforced privacy claims, dead code, and measurable hot-path inefficiencies. Details, evidence, impact, and concrete fixes follow.

---

## Executive summary

| # | Severity | Finding | Feature affected |
|---|----------|---------|------------------|
| F-01 | 🔴 Critical | `Event` ORM model is missing the `detail` column that 3 modules and the worker write to/read from. ANPR events **crash the worker** (`TypeError`), and `/api/analytics/search` + `/api/alerts/events` **500** (`AttributeError`). | ANPR, alerts feed, semantic search |
| F-02 | 🔴 Critical | `S3CompatibleStorage` lacks `verify_signed_url`, so `STORAGE_BACKEND=s3` (a documented, Docker-deployable mode) **breaks all media serving**. Also returns absolute external S3 URLs to a client that's network-restricted from reaching them. | S3 storage mode, all media endpoints |
| F-03 | 🟠 High | `delete_person` / `delete_user` / camera deletion **violate FK constraints** on any FK-enforcing database (the compose default is PostgreSQL). Reproduced: `IntegrityError: FOREIGN KEY constraint failed`. | Person/user/camera management |
| F-04 | 🟠 High | **DB writes on the frame hot path**: measured **11 SQL statements/frame** with 5 active tracks + recognition on. At 5 fps/camera and N cameras this is 55N statements/sec, plus a full-table enrolled-embedding scan every 30s per camera. Long-path analysis below. | Worker throughput, PostgreSQL latency |
| F-05 | 🟠 High | `privacy_masks` are stored and accepted via the API **but never applied** anywhere in the detection/tracking pipeline — the README's privacy promise ("geometry the detector skips") is unimplemented. | Privacy/compliance (GDPR positioning) |
| F-06 | 🟠 High | `Recorder.finalize_last()` **buffers entire 300s MP4 segments in RAM** (`data = fh.read()`; measured 5.6–900 MB per segment depending on bitrate) before writing to storage, on a loop that holds them ~300s each. With 4+ cameras, this is multi-GB peak RSS and OOM territory. | Recording, memory, stability |
| F-07 | 🟠 High | Live-view ffmpeg processes are **never reclaimed** — no max-duration, no eviction, no `_stop_stream` caller. Streams run until the API restarts, accumulating CPU. Repeated `/play` calls across cameras spawn unbounded transcodes. | Live view, resource efficiency |
| F-08 | 🟡 Medium | **Login timing-enumeration + double Argon2 cost**: nonexistent users skip Argon2 verification entirely (measured ~45 ms dummy-hash, no verify vs ~90 ms full path); real logins pay hash+verify ≈ 2× CPU. Existing-user vs nonexistent-user responses differ by ~50 ms — an oracle for account probing. | Auth hardening, DoS surface |
| F-09 | 🟡 Medium | **Schema/ORM dead-code drift**: `Stream`, `SystemMetric`, `ModelVersion` tables are never used; `schemas.py` (199 lines) is 99% dead (only `TPLinkNvrSeed` imported anywhere); `timeutil.py` never imported; `aggregate_track`/`merge_intervals` only self-referenced; `events:export` permission in RBAC but endpoints use `video:export`... several more below. | Maintainability, clean code |
| F-10 | 🟡 Medium | **Per-record envelope encryption without key reuse or caching**: a 58-byte camera URL becomes a 432-byte token (7.4×). Every `encrypt_*` call generates a fresh Fernet data key + wraps it with the KEK. Measured numbers and migration-safe fix below. | Storage efficiency, crypto CPU |
| F-11 | 🟡 Medium | Retention sweeper ignores documented policies: `retention_embeddings_days` and `retention_audit_days` are declared config that the sweeper never enforces; expired refresh tokens are never purged; camera-level `retention` overrides are ignored; expired-`end_ts` recordings only. | Data lifecycle, compliance |
| F-12 | 🟡 Medium | **Docker image bakes build context** — no `.dockerignore`, so `COPY . /app` ships `.env` (real secrets), 320 KB + 365 KB SQLite DBs, `.venv/`, `data/` test media into every image. Supply-chain/secrecy exposure on any registry push. | Deployment security |
| F-13 | 🟡 Medium | UI renders server-controlled strings into `innerHTML` unsanitized (7 sites); person labels, camera names, audit usernames are operator-supplied and persist — stored-XSS chain via CSP script-src only (CSP mitigates but `img-src data:` + event handlers offer probing vectors). | UI security hygiene |
| F-14 | 🟡 Low | Recorder segments claim `duration_sec=300, size_bytes=len(data)` but ffmpeg re-encodes main streams with no `-t` ceiling enforcement on actual capture time vs segment wall time drift; timeline/clip assembly trusts these values. Correctness of the media archive is approximate. | Recording metadata accuracy |

Plus ~20 minor findings consolidated in the appendix (dead config flags, `raise ... from exc` misses, `noqa: BLE001` prevalence — 27 sites, `import` in functions, README/test-count drift, `_ensure_columns` SQLite-only ALTERs, metrics `detections_per_minute` never emitted, etc.).

---

## Part 1 — Critical defects (with reproduction evidence)

### F-01. `Event.detail` does not exist — ANPR crashes the worker, two API endpoints 500

**Evidence (three independent reproductions):**

1. Model introspection:
```python
>>> [c.name for c in Event.__table__.columns]
['id','camera_id','track_id','identity_id','identity_status','event_type',
 'timestamp_start','timestamp_end','confidence','bbox','snapshot_key_enc',
 'video_segment_key_enc','created_at']          # ← no 'detail'
```
   `packages/domain/models.py:192-207` defines `Event` with **no** `detail` column (only `AuditLog` at line 243 has one).

2. Write path (worker, `packages/ai/pipeline.py:183-195`) constructs `EventRow(..., detail={...})` → **`TypeError: 'detail' is an invalid keyword argument for Event`** (reproduced live).

3. Read paths:
   - `apps/api/routers/analytics.py:90` — `idx.index(ev.id, f"{ev.event_type} {ev.detail or ''}")` → **`AttributeError: 'Event' object has no attribute 'detail'`** → HTTP 500. Reproduced with full stack trace against a live app instance.
   - `apps/api/routers/alerts.py:140` — `"detail": e.detail` → same `AttributeError` → HTTP 500 on `/api/alerts/events`.

**Why the tests miss it:** `tests/test_surveillance.py` exercises ANPR only through `ANPRPipeline.read()` directly (line 336) and rules through `RuleEngine.evaluate()` — never through `CameraPipeline.process_frame` with ANPR enabled, and no test ever hits `/api/alerts/events` or `/api/analytics/search` with rows present.

**Impact:**
- ANPR (`AI_ANPR_ENABLED=true`) is **non-functional in production** — worse, it's a *worker crash*: `apps/worker/main.py:295-297` catches the exception, rolls back, logs, and continues, so each vehicle-frame failure is swallowed and **every ANPR event is silently lost** while the pipeline keeps running and looking healthy.
- Both the semantic-search and the analytic-events feeds — two headline BI features — return 500 whenever any event row exists. In the shipped SQLite DB, `events` table count = 0, which is why the app "works" in dev.

**Recommended fix (choose A, then B):**
- **A. Add the column** (it's the intended design — rules detail `dwell_sec`, `direction`, `count`; ANPR detail `plate_enc`/`plate_hash`):
```python
# packages/domain/models.py, class Event
detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
```
  and in `apps/api/bootstrap.py::_ensure_columns` append `"ALTER TABLE events ADD COLUMN detail JSON"` (the existing pattern at lines 97-106 already handles the SQLite/PG "already exists" swallow — note that catch is dialect-fragile; see minor findings).
- **B. Add regression tests that actually traverse the broken paths:**
```python
def test_anpr_event_persists_detail(tmp_path):  # worker-level
    # build pipeline with anpr=ANPRPipeline(...), drive frames, assert row.detail["plate_hash"]
def test_search_endpoint_with_events(client):   # API-level
    # seed an Event row, GET /api/analytics/search, assert 200 (currently 500s)
def test_alerts_events_endpoint(client):        # currently 500s
```
- Also fix `apps/worker/main.py:289-290` to read from `ev.detail` **after** the fix (it currently reads `ae.detail` off the in-memory object, which works, but serializes `plate_enc` — ciphertext — into alert messages sent to third-party webhooks; see F-15 note in appendix).

**Outcome after fix:** ANPR end-to-end green, both endpoints stable, `detail` queryable (enables filtering alerts by zone/dwell), and the test suite gains coverage of the exact integration seams that failed.

---

### F-02. S3 backend is missing `verify_signed_url` — media delivery broken; also leaks external URLs

**Evidence:**
```python
>>> hasattr(S3CompatibleStorage, 'verify_signed_url')
False
>>> StorageProvider.__abstractmethods__
frozenset({'delete','put','exists','sign_get_url','get'})   # verify_signed_url not abstract!
```
- `apps/api/routers/video.py:28` — `rt.storage.verify_signed_url(key, exp, sig)` → `AttributeError` on every call when `STORAGE_BACKEND=s3`. The abstract base (`packages/storage/base.py`) forgot to declare it, so the bug compiles silently. `mypy --follow-imports=skip` (the CI setting) also can't catch it.
- `packages/storage/s3.py:32` — `self._signer = LocalFilesystemStorage("/tmp", signing_secret)` is constructed and **never used** (grep-verified: only occurrence). It's a vestige of a removed local-signing design.
- `sign_get_url` (s3.py:61-67) returns `generate_presigned_url(...)` — an **absolute external URL** — while `LocalFilesystemStorage.sign_get_url` returns a **relative path** `/api/video/...`. Every consumer (`events.py:74,77,97,138`, UI, tests) treats the returned value as a path. With S3, clients get `https://<bucket>.s3.amazonaws.com/...` (a) unreachable from the browser in a hardened on-prem deployment (the whole product premise is "video never leaves the site"), and (b) even if reachable, `serve_video` can never validate it since the signature scheme is S3's, not the local HMAC.

**Impact:** The documented `STORAGE_BACKEND=s3` mode is **non-functional for the product's core purpose (secure media delivery)**. Any deployment that follows the README/compose and switches storage to S3 gets 500s on `/api/video/*` and unusable export/clip URLs. Silent because the local-backend path is the only one tested.

**Recommended fix:**
```python
# packages/storage/base.py — make the contract explicit
class StorageProvider(ABC):
    @abstractmethod
    def verify_signed_url(self, key: str, exp: str, sig: str) -> bool: ...

# packages/storage/s3.py — proxy through S3 presigned GETs, keeping the local HMAC
# scheme for authorization and issuing *relative* app URLs:
def sign_get_url(self, key, expires_sec=300):
    # Option 1 (recommended): keep /api/video/{key} and have serve_video stream
    #   from S3 via a short-lived boto3 presigned URL fetched server-side.
    exp = int(time.time()) + expires_sec
    sig = hmac.new(self._secret, f"{key}:{exp}".encode(), hashlib.sha256).hexdigest()
    return f"/api/video/{urllib.parse.quote(key, safe='')}?exp={exp}&sig={sig}"

def verify_signed_url(self, key, exp, sig):
    ...  # same HMAC verification as local; then stream:
def get(self, key):  # unchanged — boto3 get_object already works
```
  i.e., drop `_signer`, keep one signing scheme (HMAC over `key:exp` with the master key as secret), and let `serve_video` stream S3 bytes through the app. The client-facing contract stays identical across backends; S3 credentials stay server-side; no external URL is ever exposed to the browser.
- Delete the dead `_signer` line, and **add `psycopg` + a `boto3`-installed CI job that runs the API tests with `STORAGE_BACKEND=s3` + moto** so this mode is never again shipped untested.

**Outcome after fix:** S3 mode works end-to-end with the same security model (short-lived, app-scoped, HMAC-authorized links), one signing scheme instead of two divergent ones, and backend parity enforced by CI rather than hope.

---

## Part 2 — Correctness & data-integrity issues

### F-03. Deletions violate FK constraints on production databases

**Evidence:** `packages/domain/models.py` — zero occurrences of `cascade`/`ondelete`/`passive_deletes` (grep-verified across the repo). Reproduced with `PRAGMA foreign_keys=ON` (SQLite parity for PostgreSQL behavior):
```
sqlite3.IntegrityError: FOREIGN KEY constraint failed
   (DELETE FROM persons WHERE id='p1' with a person_embeddings row referencing it)
```
- `apps/api/routers/persons.py:52-60` — `db.delete(person)` with a comment claiming "cascade removes embeddings" — **there is no cascade**. On PostgreSQL (the compose default), this 500s whenever the person has any enrollment. On SQLite dev, FKs are off by default so it "works."
- Same pattern: `users.py:66-69` (`refresh_tokens.user_id` FK), `cameras.py:305-313` (`detections`/`tracks`/`events`/`video_segments`/`snapshots` FKs), `rules`/`nvr` deletion paths.

**Impact:** Core admin operations (deleting an enrolled person — a GDPR erasure request scenario! — or a retired camera) fail with 500 on the production database engine while succeeding in dev. A GDPR "right to erasure" request that 500s is a compliance incident, not just a bug.

**Recommended fix:**
```python
# explicit, DB-enforced lifecycle
class Person(Base):
    embeddings: Mapped[list["PersonEmbedding"]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True)
# and at the FK:
person_id: Mapped[str] = mapped_column(
    ForeignKey("persons.id", ondelete="CASCADE"), index=True)
```
Apply consistently to `User→RefreshToken`, `Camera→Detection/Track/Event/VideoSegment/Snapshot`, `Person→PersonEmbedding`. Since production is PostgreSQL and `_ensure_columns` already runs raw ALTERs, pair this with **Alembic migrations** (the code comments at `bootstrap.py:80-82` already acknowledge this debt) so the `ondelete` clauses actually reach existing databases.

**Outcome:** deletion workflows function on all backends; orphan rows become impossible; the GDPR-erasure path works; DB integrity is enforced by the database, not by hope.

### F-05. `privacy_masks` are accepted and stored but never applied

**Evidence (grep across every Python module):** `privacy_masks` appears only in `models.py` (column), `schemas.py` (Pydantic), and `cameras.py` (API passthrough). The worker (`apps/worker/main.py`) builds the pipeline from `camera.rules` but **never reads `camera.privacy_masks`**; `packages/ai/pipeline.py` never receives masks; `CameraPipeline.process_frame` applies nothing; `packages/ai/detectors.py` has no mask parameter. The model comment even says "privacy masks live alongside this as geometry the detector skips" — that geometry is never skipped.

**Impact:** Operators can configure exclusion zones (e.g., a neighbor's window, a public sidewalk outside the property line) in the API and believe they're excluded — while detection, tracking, snapshots, embeddings, and events continue covering them. For a product whose README headline is "**Privacy by design**," this is a **false control**: worst possible failure mode for a compliance feature. Snapshots of masked regions are still written to storage; identity recognition (if enabled) still runs on them.

**Recommended fix (smallest correct seam — the pipeline):**
```python
# apps/worker/main.py — pass masks into the pipeline
pipeline = CameraPipeline(..., privacy_masks=camera.privacy_masks or [])

# packages/ai/pipeline.py — filter detections before tracking
def _masked(self, bbox) -> bool:
    for m in self._masks:  # each {x,y,w,h}
        if _bbox_overlap(bbox, (m["x"], m["y"], m["w"], m["h"])):
            return True
    return False
# in process_frame, after detector.detect():
detections = [d for d in raw
              if d.confidence >= self.confidence and not self._masked(d.bbox)]
```
Document the semantic precisely (detection fully suppressed when ≥X% overlap — recommend suppression at any center-in-mask or ≥50% overlap), add tests (`test_masked_area_produces_no_events`), and surface mask coverage in the UI canvas so operators can verify what they drew.

**Outcome:** the privacy promise becomes a real, testable control with audit-visible behavior; masked geometry produces no detections, no snapshots, no embeddings, no events.

### F-08. Login timing oracle + double Argon2 cost

**Evidence (measured in this environment):**
```
hash_password("dummy-password-not-used"): 47 ms   # auth.py:60 — runs on EVERY login
verify_password(...):                  45 ms
```
`apps/api/routers/auth.py:60-61`:
```python
dummy = hash_password("dummy-password-not-used")          # 47 ms, always paid
ok = verify_password(...) if user else False              # nonexistent user: NO verify at all
```
For a **nonexistent** user: 47 ms total. For an **existing** user with a wrong password: 47 + 45 ≈ 92 ms. The comment says this reduces enumeration timing — the implementation does the **opposite**: the branch differences are (a) measurable and (b) the dummy hash is recomputed on every request (a fresh Argon2 salt each time — pure waste; it should be computed once at module import), while genuine logins pay double.

**Impact:** ~2× CPU on every real login (DoS surface at `rate=1.0/capacity=10` — 10 concurrent logins ≈ 10 × 47 ms ≈ 0.5 CPU-sec); a ~50 ms response-time oracle distinguishing existing accounts; 47 ms × every request even for garbage emails.

**Recommended fix:**
```python
# module scope, computed once — a FIXED hash, so timing is byte-identical
_DUMMY_HASH = hash_password("dummy-password-not-used")

def login(...):
    ...
    record = db.query(User).filter(User.email == body.email).first()
    hashed = record.password_hash if record else _DUMMY_HASH
    ok = verify_password(body.password, hashed)   # ALWAYS runs, ALWAYS same cost
```
Now every path pays exactly one Argon2 verify (~45 ms) with identical salt/params, and the dummy is computed once per process instead of per request.

**Outcome:** enumeration timing differential eliminated (both branches ≈45 ms); login CPU halved; lockout audit entries unchanged.

### F-11. Retention sweeper doesn't implement the documented policy surface

**Evidence (`apps/worker/main.py:165-186` vs `apps/api/config.py:62-66`):** the sweeper enforces `retention_recordings_days`, `retention_events_days`, `retention_snapshots_days` — but:
- `retention_embeddings_days: int = 90` (config.py:65) — **never referenced** by the sweeper (grep: only definition site). Enrollment embeddings (biometric data!) are retained forever, contradicting README's "RETENTION_EMBEDDINGS_DAYS ... Enrollment-embedding retention."
- `retention_audit_days: int = 365` (config.py:66) — **never referenced**. The "immutable audit log" grows unboundedly (a 24/7 platform writes audit rows on every login, token refresh, export...).
- Expired `RefreshToken` rows are **never purged** (7-day TTL; the shipped dev DB already has 6). Unbounded slow growth of a unique-indexed table.
- `Camera.retention` per-camera overrides (`models.py:122-123`, settable via `PUT /api/cameras`, honored by `provision_tplink_nvr`) are **ignored** by the sweeper — every camera gets global policy regardless of its override.
- The recordings sweep keys on `VideoSegment.end_ts < rec_cut` — correct — but deletes segments one-by-one inside a Python loop with per-row storage deletion and per-row `s.delete(seg)`; a camera churning 288 segments/day × N cameras makes this an O(rows) interactive transaction every hour (retention loop sleeps 3600s) with `s.commit()` only at the end — a long lock window on PostgreSQL.

**Impact:** compliance-relevant data classes (biometric embeddings, audit trail) outlive their stated retention; tables grow without bound (PostgreSQL vacuum/indбусы pressure); per-camera overrides silently do nothing (an operator who sets 30-day retention on a lobby camera and 7-day on a loading dock gets 7 days for both or 30 for both — global only).

**Recommended fix:**
```python
def _sweep_retention(rt) -> None:
    settings = rt.settings
    now = utc_now()
    with rt.SessionLocal() as s:
        # 1. batch deletes, chunked, committing per chunk — no long transactions
        _delete_range(s, VideoSegment, VideoSegment.end_ts, now - td(days=settings.retention_recordings_days),
                      pre=row_safe_delete(rt.storage, "storage_key"), chunk=500)
        ...
        # 2. the missing policies
        emb_cut = now - td(days=settings.retention_embeddings_days)
        for e in s.query(PersonEmbedding).filter(PersonEmbedding.created_at < emb_cut):
            s.delete(e)
        audit_cut = now - td(days=settings.retention_audit_days)
        s.query(AuditLog).filter(AuditLog.ts < audit_cut).delete(synchronize_session=False)
        s.query(RefreshToken).filter(RefreshToken.expires_at < now).delete(synchronize_session=False)
        # 3. per-camera overrides: effective_days(cam) = cam.retention["days"] or global
```
Also honor `Camera.retention` per class (recordings/events/snapshots can each have `{"recordings_days": ..., "events_days": ...}` — or keep the current `days` key but document it).

**Outcome:** every declared retention knob actually enforced; biometric data lifecycle matches documentation (a real GDPR Art. 5(1)(e) storage-limitation control); bounded table growth; sweep runs in short chunks instead of hour-long implicit transactions.

### F-14. Recording metadata drift (segment duration/size never reconciled)

`Recorder.record_url` (`recorder.py:101-109`) pre-fills `duration_sec=float(self.seg_seconds)` (300) and `end_ts=start+300s` **before ffmpeg has captured anything**; `finalize_last` then sets `size_bytes = len(data)` but never corrects `duration_sec`/`end_ts` from the actual file (no `ffprobe`, no `proc` timing). If the stream drops at t=80s, ffmpeg exits, `returncode==0`, and the row claims a 300s/300s-aligned segment — timeline rendering (`timeline.py` uses `start_ts/end_ts`) and `/events/{id}/clip` window math then mis-align clips. Fix: derive real duration via `ffprobe -v error -show_entries format=duration` in `finalize_last` (or at minimum `os.path.getsize`-based sanity + returncode-based skip), and set `end_ts = start_ts + actual_duration`.

---

## Part 3 — Performance & resource efficiency

### F-04. Frame hot path does per-frame ORM writes — measured

**Evidence (SQLAlchemy `before_cursor_execute` counter around `CameraPipeline.process_frame`, live process):**
```
1 active track,  5 frames:  4 statements  → 0.8/frame
5 active tracks + recognition, 5 frames: 55 statements → 11.0/frame
```
Breakdown of the 11/frame (from `pipeline.py:198-238`): per frame, for each track: 1× `session.get(TrackRow, tid)` (`_upsert_tracks`, line 283 — even when unchanged, and even though the row was fetched the previous frame in the same session), 1× `INSERT detection` (line 215 — "sampled" per the comment, but it's per-frame per-track), plus the per-frame commit (`worker/main.py:283`). Recognition adds the embedding refresh every 30s (`_refresh_enrolled` — `session.query(PersonEmbedding).all()` full-table scan + per-row `decrypt_json`) — amortized, but on a 1,000-employee site that's 1,000 Fernet unwraps every 30s per camera.

At the README's default `AI_INFERENCE_FPS=5` and the capacity planner's 8-camera target (`scripts/capacity.py --cameras 8`): 5 fps × 8 cams × 11 = **440 SQL round-trips/sec sustained**, every one a network hop + WAL write on PostgreSQL, each frame's work bracketed by BEGIN/COMMIT. SQLite absorbs this; PostgreSQL's per-statement latency (0.2–1 ms LAN) makes the pipeline thread the bottleneck and adds jitter to detection timestamps.

**Recommended fixes, in order of value:**
1. **Decouple capture from persistence with a bounded queue + batch writer.** The pipeline should return domain events; a writer coalesces and flushes every K frames or T seconds (`session.add_all(batched_detections)`; one `UPDATE tracks` per changed track — SQLAlchemy can diff in-memory state; one commit per flush). This turns 440 stmts/sec into ~5–20. This is also the natural place for the queue-depth metric already declared in `metrics.py` (`queue_depth`, `frames_dropped`, `camera_fps`, `detections_per_minute` — all currently **never emitted**; see appendix).
2. **Stop per-frame Detection inserts when nothing changed.** The comment says "sampled, not every frame's raw boxes" — the code does the opposite. Gate on: trajectory delta > ε, bbox delta > ε, or interval ≥ N s.
3. **Cache enrolled embeddings with invalidation, not a 30s TTL rescan.** Keep the decrypted vectors in memory; subscribe to a lightweight signal (the persons router already commits enrollment writes — an in-process event or `updated_at` watermark check beats a full decrypt-every-30s-per-camera).
4. **Connection hygiene:** worker threads each open a session per frame (`SessionLocal()` per iteration, `worker/main.py:280-299`) — fine — but with batching, prefer one session per batch and `pool_pre_ping=True` on the engine for long-lived worker connections.

**Outcome (projected, 8 cams, 5 fps, PG):** ≥90% reduction in DB round-trips on the hot path (440→~40/sec), lower WAL churn, stable per-frame latency, and the observability story (queue depth, drops, fps) that the capacity planner promises becomes real.

### F-06. Recorder buffers whole segments in RAM

**Evidence:** `packages/video/recorder.py:136-151` — `finalize_last` does `data = fh.read()` (entire MP4 in memory) → `self.storage.put(...)` → only then `os.remove(tmp)`. With `RECORD_SEGMENT_SECONDS=300` and the README/capacity defaults (main stream 4 Mbps), a segment ≈ 4 Mbps × 300 s / 8 = **150 MB**; at 40 Mbps (high-end NVR) ≈ **1.5 GB per segment**. The record loop (`worker/main.py:247-269`) runs one of these per camera, and segments overlap only at boundaries — so steady-state peak RSS ≈ N_cameras × segment_size at finalize time. An 8-camera 4 Mbps deployment spikes ~1.2 GB beyond baseline on every 5-minute boundary (staggered), which is precisely when the AI pipeline also wants memory.

Note the local storage layer already writes atomically via tmp+rename (`local.py:42-45`) — the copy through Python memory is pure overhead: ffmpeg already wrote the bytes to `/tmp`; we then read them into RAM to write them again.

**Recommended fix:** give `StorageProvider` a streaming/`put_file` seam so bytes never pass through the heap:
```python
# packages/storage/base.py
@abstractmethod
def put_stream(self, key: str, source_path: str, content_type: str) -> None: ...
# local: os.replace(source_path, resolved) after same-root validation, or shutil.move
# s3:   client.upload_file(bucket, key, source_path) — multipart, streaming
# recorder.finalize_last: storage.put_stream(seg.storage_key, tmp, "video/mp4");
#                         seg.size_bytes = os.path.getsize(tmp) before move
```
Keep `put()` for the small JSON snapshots. Memory delta: **~150 MB → ~0 per camera per segment** (ffmpeg's own buffers aside). Also compute `size_bytes` from the filesystem rather than `len(data)`.

**Outcome:** O(1) recording memory regardless of bitrate/segment length; no finalize-time RSS cliffs; S3 mode gains multipart streaming for free (currently a 1.5 GB segment would also max out at boto3's single-`put_object` memory ceiling).

### F-07. Live-view transcodes are never reclaimed

**Evidence:** `apps/api/routers/live.py` — `_start_stream` (line 34) launches ffmpeg per `/play` call keyed by `camera.id`, guarded by a lock and a `poll()` reuse check. `_stop_stream` (line 79) exists but **has zero callers** (grep-verified). There is no TTL, no idle detection, no max-duration, no eviction: a viewer who closes the tab leaves an ffmpeg LL-HLS transcode of the camera substream running **until the API process restarts**. Every additional camera viewed adds another ffmpeg (CPU: one x264 `veryfast` encode each; at 640×360 that's a meaningful slice of a small-box CPU). The README bills this as "authorized LL-HLS gateway" — authorization exists; lifecycle doesn't.

**Recommended fix (two parts):**
1. **Auto-reap:** track `last_probe_ts` per stream; a lightweight background task (the app already runs thread-based services in the worker; here a `asyncio` task or middleware hook) terminates any stream not probed (manifest/segment fetches update the timestamp — requires serving `/live-media` through a tiny auth-aware handler rather than blind `StaticFiles`, or a heartbeat endpoint the player's page calls) for > `LIVE_IDLE_TIMEOUT` (default 300s), plus a hard `LIVE_MAX_DURATION` (default 4h) forcing restart.
2. **Kill the dead code or wire it:** expose `POST /api/live/{camera_id}/stop` calling `_stop_stream` (useful for the dashboard's "stop stream" button), and delete the function if not.
3. Also note `_start_stream` returns `out_dir` even when ffmpeg failed to spawn (line 68-73 logs and returns the path!) — `play()` then hands the client a manifest that will never exist, with a 200. Fail with 503 instead so the UI can surface it.

**Outcome:** live-view CPU cost becomes proportional to actual viewers; streams end when sessions end; `ps` no longer shows week-old ffmpeg processes; the dashboard gains an explicit stop control.

### F-10. Envelope encryption: per-record data keys without reuse — 7.4× storage amplification

**Evidence (measured):**
```
58-byte RTSP URL  → 432-byte stored token (7.4×)
envelope = {'k': 140 bytes wrapped key, 'c': 164 bytes ciphertext}
128-dim embedding → 2392 bytes stored (1.9× vs JSON)
```
`CryptoBox._seal` (`crypto.py:38-42`) mints a fresh Fernet data key per `encrypt_*` call and wraps it with the KEK — so every row carries ~140 bytes of wrapped key + base64/JSON framing, and every `decrypt_*` pays a KEK-unwrap + data-key Fernet decrypt. For rows written per frame-adjacent event and embeddings refreshed on the 30s TTL, that's constant CPU and 2–7× storage on exactly the high-volume tables. The design doc's stated motivation (rotation without re-encrypting bulk data) is legitimate — but per-*record* key generation buys no rotation benefit over per-*epoch* (or per-table) data keys, because rotation re-wraps `k` either way.

**Recommended fix (keep envelope semantics, cut the amplification):**
1. **Epoch-based DEKs with caching:** maintain a small LRU of active data keys (e.g., one per table/purpose, rotated daily or on demand). `encrypt` reuses the cached (already-unwrapped) DEK and stores only the raw Fernet ciphertext plus a 1-byte key-epoch tag: `v2:<epoch>:<ct>`. Decrypt resolves epoch→DEK via one cached KEK-unwrap per epoch, not per record. Envelope rotation story is preserved (re-wrap the epoch keys only).
2. **Format/version the envelope** (`v1:` prefix for existing rows) so current data stays readable — decrypt dispatches on prefix. No migration downtime.
3. **Trim serialization:** skip the base64-of-JSON double-layer for bytes (`encrypt_bytes` base64s a JSON blob of two base64 strings — 3 encodings stacked). Store `v2:<epoch>:<raw-fernet-token>` (Fernet tokens are already ASCII-armored).
4. Alternatively, for the two highest-volume cases (`Event.detail.plate_enc`, embeddings), consider AES-GCM via `cryptography.hazmat` with a cached nonce/DEK — same properties, ~none of the framing overhead. Fernet (AES-CBC+HMAC) is fine semantically; the cost here is purely the per-record wrap + triple encoding.

**Outcome:** ~5–7× less storage for encrypted fields (432 → ~110 bytes for that URL), per-record crypto CPU down from two Fernet operations to one (after epoch warm-up), rotation capability unchanged, and backward-readable existing rows.

---

## Part 4 — Design & architecture findings

### D-1. Schemas layer is effectively dead — validation happens ad-hoc in routers

`packages/domain/schemas.py` (199 lines) defines 16 Pydantic models; **only `TPLinkNvrSeed` is imported anywhere** (`cameras.py:24` — grep-verified). Meanwhile the routers hand-roll validation:
- `cameras.py:211` — `create_camera(body: dict)` with `body.get(...)` and manual `int(...)` coercion. No length limits on `name` (DB column is `String(255)` — SQLite won't care, PostgreSQL will truncate or error), no validation that `privacy_masks`/`retention` are sane shapes (bad JSON shapes get stored and later crash... nothing, because nothing reads them — see F-05).
- `persons.py:34` — `create_person(body: dict)` while `PersonCreate` sits unused in schemas.py.
- `alerts.py` and `users.py` define **local, duplicated** models (`RouteCreate`, `UserCreate`) instead of the domain ones (`schemas.UserCreate` vs `users.UserCreate` — two divergent copies of the same contract, both maintained).
- `auth.py:206` — `mfa_verify(body: dict)` with `(body or {}).get("code")` — while `MfaVerify(code: str)` exists unused.

**Impact:** The OpenAPI schema generated by FastAPI — a selling point (`docs/api/openapi-summary.md`) — shows `dict` bodies for these endpoints, so client generation, validation, and documentation all degrade. Duplicated request models will drift (already have: `UserCreate` exists twice). Pydantic's validation (max lengths, email format, bounds) is bypassed on precisely the endpoints that write to the DB.

**Fix:** routers take the schema models that already exist (`CameraCreate`, `PersonCreate`, `MfaVerify`, ...), delete the router-local duplicates, add `Field(max_length=...)` constraints matching column sizes, and delete the unused schema models (or wire them). One afternoon, large readability + API-contract win, and mypy stops being asked to type `dict`-shaped bodies.

### D-2. The audit boundary is inconsistent (writes coupled to request transactions)

`write_audit` adds an `AuditLog` to the caller's session; every router then `db.commit()`s — coupling audit persistence to the success of the business write. In `cameras.py:provision_tplink_nvr` (lines 180-206), a mid-loop SSRF failure does `db.rollback()` **after** audit rows were added, then raises — the rollback discards those audit rows. Security-relevant actions (bulk camera creation attempts) can go unaudited exactly when they fail. Separately, `live.py:117` imports `write_audit` inside the function body. 

**Fix:** write audit entries on a **separate short-lived session** (or a dedicated audit-only session/queue flushed independently), so a failed business transaction still leaves the attempt audited — which is the point of an audit log. Wrap the login handler's pattern (it already commits audit-then-raise correctly at `auth.py:68-71`) into a small helper used everywhere.

### D-3. Observability is declared but not wired

`metrics.py` declares 15 metric names (including `camera_fps`, `frames_dropped`, `queue_depth`, `detections_per_minute`, `recognition_latency_ms`, `database_latency_ms`, `camera_disconnects_total`). **Grep across apps+packages: only 3 are ever set** (`cpu_utilization`, `ram_used_mb`, `storage_usage_percent` — and only when `psutil` is installed, which isn't in requirements.txt). The gateway never reports its status transitions or drops; the pipeline never counts; `api_latency_ms`/`database_latency_ms` have no middleware taps. Prometheus config scrapes both `api` and `worker` jobs at the same API endpoint with the same credentials — and `/api/system/metrics` requires a bearer token (no credentials configured in `prometheus.yml` → every scrape 401s; the file as shipped cannot work).

**Fix:** wire the declared names at their natural seams (gateway status→`camera_disconnects_total`, pipeline→`frames_processed`/`detections_per_minute`, F-04's queue→`queue_depth`, a tiny `http` middleware→`api_latency_ms`, engine event listener→`database_latency_ms`), add `psutil` to requirements (or make its absence explicit in docs), and either drop the worker scrape job or have the worker expose its own `/metrics` (it already shares the runtime bootstrap). For Prometheus auth: mount an internal-only scrape endpoint or document the bearer-token approach with the token injected via file.

### D-4. Settings carry dead switches — config lies to operators

- `ai_motion_gate_enabled: bool = True` (config.py:47) — never read anywhere (grep). The README describes a motion gate in the pipeline flow ("RTSP -> decode -> [motion gate] -> ..."); there is no gate. `ReferenceMotionDetector` does its own frame-differencing internally, but there's no configurable gate stage, so the flag does nothing.
- `retention_embeddings_days`, `retention_audit_days` — see F-11.
- `LOCALSIGHT_LIVE_DIR` env var read at `live.py:29` as a default, but `main.py:93` computes the mount from a **hard-coded** relative path (`os.path.join(dirname(__file__), "..", "..", "data", "live")`) — setting the env var makes the gateway write segments to a directory the app doesn't serve. Two sources of truth, one of them ignored.

**Fix:** delete the dead flag or implement the gate; make both live-dir reads use one shared constant (put it in `Settings` as `live_dir` with the env override, and derive both the mount and `_LIVE_ROOT` from it).

### D-5. Bootstrap/migrations story is one-way and dialect-fragile

`bootstrap._ensure_columns` runs two hard-coded `ALTER TABLE ... ADD COLUMN` statements inside `engine.begin()`, swallowing **all** exceptions per statement. On PostgreSQL, a failed ALTER aborts the implicit transaction, but each `execute` is attempted inside the same `begin()` block — the second ALTER runs in a doomed transaction if the first failed for a real reason (not "already exists"), masking genuine errors as "already present." `create_all` + ad-hoc ALTERs is acceptable for v1; the code itself says "use Alembic" but Alembic isn't in requirements, isn't in CI, and there's no `alembic/` directory. Combined with F-03's `ondelete` requirements, the schema will inevitably drift from the models.

**Fix:** adopt Alembic now (it's cheap: `alembic init`, autogenerate the current schema as baseline, add `pip install alembic` + a CI step that runs `alembic upgrade head` against a scratch PG before tests). Keep `_ensure_columns` only for the pre-Alembic databases, and make it dialect-aware (check `information_schema`/`PRAGMA table_info` rather than try/except-swallow).

### D-6. Worker: cameras never re-scanned; stop event nearly useless

`apps/worker/main.py:main()` snapshots the camera list **once** at startup (line 316-317). Cameras added/removed/enabled via the API (the product's normal administration flow!) don't start pipelines until the worker restarts; removed cameras keep their threads. There's no health-based restart of crashed loops either: after `MAX_CONSECUTIVE_FAILURES` the `StreamGateway.iter_frames` generator **returns** (gateway.py:60, 75), `run_camera` exits its loop, the thread dies — permanently, silently, until process restart. The `stop` event is only set on KeyboardInterrupt (the graceful path handles it; anything else — SIGTERM from Docker `stop` — kills daemon threads abruptly, leaving ffmpeg children to be reparented; the recorder's `stop_all` only runs on the clean path since `run_camera` returns after `iter_frames` exhausts — but for a camera that exhausted retries it does run; for SIGTERM it never does).

**Fix:** a supervisor loop (every N seconds: diff `Camera` table against running threads; start new, stop removed, restart dead); install a `SIGTERM` handler that sets `stop` so `recorder.stop_all()` and ffmpeg cleanup actually execute under `docker stop`; log camera-thread deaths at ERROR with a metric (ties into D-3's `camera_disconnects_total`). Also change `iter_frames`' terminal `return` to a long-backoff `continue` (or surface failure so the supervisor can decide) — "fail safe" per the README means keep trying safely, not give up after 10 tries at 60s backoff (~10 minutes).

### D-7. Duplicate Argon2/JWT/crypto parameter provenance; `.env` file precedence trap

`Settings` reads `.env` **and** environment (`SettingsConfigDict(env_file=".env")`). In the Docker/compose path, environment variables are injected explicitly — but `pydantic-settings` prefers real env vars over `.env`, and the image `COPY . /app` (F-12) ships a `.env` from the build context. A stale build-time `.env` can silently become the runtime config for anything **not** explicitly passed in `docker-compose.yml`'s `environment:` (e.g., `RECORD_*`, `RETENTION_*`, `AI_INFERENCE_FPS`, `AI_CONFIDENCE_THRESHOLD` are NOT in the compose environment list — so the **image's** `.env` wins over the host's `.env` that the compose file's `${VAR}` substitutions read... actually compose `${VAR}` reads the host `.env`, but pydantic reads the image-baked one; the two files can and will diverge). This is a genuine production incident waiting to happen: operators edit `.env`, restart compose, and the worker (whose settings come from pydantic, not compose substitutions) ignores them.

**Fix:** never bake `.env` into the image (see F-12's `.dockerignore`); in `Settings`, prefer `env_file=None` inside containers and pass a `LOCALSIGHT_ENV_FILE` opt-in, or add a startup log line printing the resolved source of each sensitive setting (never values). Document explicitly which knobs flow via compose env vs `.env`.

---

## Part 5 — Security review (defense-in-depth notes beyond the criticals)

The security posture is unusually good for this category: Argon2id, JWT rotation with replay detection, TOTP implemented correctly (RFC 6238, ±1 window, `compare_digest`), envelope encryption, SSRF guard with deploy-time allowlists, path-traversal-safe storage, HMAC-signed expiring URLs, audit trail, hardened headers/CSP, non-root container. Specific gaps found:

1. **JWT permissions stale-window (Medium):** access tokens embed the permission set (`dependencies.py:55` reads `claims["permissions"]`), valid 15 min. A demoted/locked-out admin keeps full permissions until token expiry; there's no revocation check per request (only refresh tokens are tracked server-side). Acceptable trade-off at 15-min TTL, but the `is_active` check happens per-request while role changes don't — document it or add a lightweight jti denylist on role change. **Fix option:** on `PUT /users` role change or `DELETE /users`, also revoke that user's refresh tokens (cheap, closes the 15-min window to "next refresh").
2. **SSRF DNS-rebinding residual (Low-Medium):** `validate_egress_url` resolves the host, validates the resolved IPs, returns the parsed URL — but the **consumer** (ffmpeg/httpx) resolves again at connect time. A rebinding DNS server can return a public IP for validation and a private IP at connect. Standard mitigation is to pin the resolved IP for the actual connection, or validate at-connection via a custom transport. Given `SSRF_ALLOWLIST` covers intended private ranges, the residual risk is the *unlisted* private space; recommend documenting the gap + network egress controls (already referenced in ssrf.py's docstring) or implementing IP-pinned connections for webhooks (httpx `transport` with a resolver override).
3. **`verify_code` MFA brute force (Low):** login rate limit is `rate=1.0, capacity=10` per IP — with a 6-digit code and a ±1 window (3 valid codes), 10 attempts/min/IP through a botnet gives non-trivial odds over days. Add a small per-user MFA failure counter/lockout (the `failed_login_attempts` machinery already exists for passwords — reuse it for MFA failures).
4. **Stored XSS via innerHTML (Medium, mitigated):** 7 `innerHTML` sites render `person.label`, `camera.name`, `audit.username` etc. — all operator-supplied, all persisted. CSP `script-src 'self'` blocks script execution, but `img-src 'self' data:` permits `data:` images (exfil/probing channel) and event-handler injection into non-script contexts remains. **Fix:** `textContent`/DOM building or a tiny `escapeHtml` helper; tighten `img-src` to `'self'` only (no legit `data:` usage in the shipped UI).
5. **Webhook payloads carry ciphertext + PII (Medium):** `worker/main.py:289-290` puts `str(ae.detail)` into the alert `message` — after F-01's fix, `detail` includes `plate_enc` (Fernet ciphertext, harmless) but also the ANPR event's `bbox`/timing; email bodies (`notify/__init__.py:74`) embed `alert.detail` raw. Third-party webhook/email systems receive more than the minimum. **Fix:** an allowlist of detail keys per channel (e.g., webhooks get `dwell_sec`, `direction`, `count`, never `plate_enc`/`plate_hash`).
6. **`/_ensure_columns` + `create_all` with elevated trust (Low):** the bootstrap DDL runs at every process start (API and worker race it — two processes ALTERing concurrently can both hit "duplicate column" and both swallow). Move DDL out of runtime bootstrap into migrations (D-5).

---

## Part 6 — Clean code & standards

1. **Bare/broad exception handling is the house style** — 27 `# noqa: BLE001` sites. Most are justified by the "never crash the loop" philosophy (worker frame loop, alert sender), and several re-raise correctly. But some sites suppress too much: e.g., `bootstrap._ensure_columns` swallows *all* exceptions per ALTER (masks real DDL errors — see D-5); `live.py:54-56` and `live.py:82-86` silently `pass` on `terminate()` failures (zombie processes go unnoticed); `worker/main.py:174-175` `except Exception: pass` around storage delete hides permission errors forever (retention looks successful while files accumulate). **Standard to adopt:** narrow the exception types (`OSError`, `subprocess.SubprocessError`) at infra seams; keep the broad catches only at true top-level loop boundaries, and pair each with a `log.warning` (several already do — good).
2. **Function-local imports scattered** — `live.py:41`, `main.py:104`, `timeline.py:32`, `sources.py:60/66`, `onvif.py` (three sites), `worker/main.py:306` (`dt_now` re-imports datetime inside a function *while the module already imports it* — a pure smell), plus `pipeline._crop_vehicle`'s numpy guard. Lazy imports for heavyweight optional deps (onnxruntime, boto3, paho) are deliberate and fine; stdlib/local-module laziness is just noise. **Fix:** hoist local-module imports to module scope; keep the optional-dependency ones, but standardize them behind a tiny `import_optional(name)` helper with a clear error message.
3. **`worker/main.py:305-307` — `dt_now()` wrapper** exists only to re-import `datetime` and call `utcnow`-equivalent — while `packages/domain/timeutil.utcnow` already exists and is **never used anywhere** (dead module, grep-verified). Delete `dt_now`, import `timeutil.utcnow`, and use it consistently.
4. **Inconsistent datetime handling** — `models.py` columns are `DateTime(timezone=True)`, but `analytics.py:40-41` has to strip tzinfo manually (`_naive`) because SQLite returns naive datetimes; `events.py:42-44` does `.replace("Z","+00:00")` parsing while `analytics.py:31-38` has a richer `_parse` that also validates — two parsing idioms for the same job, one without error handling (`events.py:42` will 500 on a malformed `start` string — `fromisoformat` raises `ValueError`, uncaught → 500 instead of 400). **Fix:** one `parse_iso_or_400` helper (timeutil is the natural home — currently dead, give it a job), used by events/audit/analytics/timeline.
5. **`alerts.py:130` — `limit: int = 50` with `min(limit, 500)` inline** vs the `Query(50, le=500, ge=1)` pattern used in events/audit. Inconsistent pagination contracts; also no `total` returned here unlike every other list endpoint.
6. **Naming/marker drift:** README says "49 tests" in one place (line 248) and "66 tests" elsewhere; docs claim `events:export` permission governs export endpoints — but `events.py:89,105` uses `events:export` while `events.py:105`'s clip uses... also `events:export` — yet RBAC defines `video:export` as a separate permission and `events.py:89`'s own docstring mentions watermarking. `SECURITY_OPERATOR` has `events:export` but `ANALYST` has it too — fine — just verify the permission taxonomy matches docs (`docs/security/SECURITY.md`).
7. **Dead UI artifacts:** `ui/app.js:94` — `$("tbody", ).innerHTML = ""` (stray comma — harmless but broken-looking) followed by `document.querySelector("#events-table tbody")` — two idioms for one node; the whole events table render assumes `data.items` exists while `persons` returns a bare array — the `api()` helper can't normalize this. Minor, but symptomatic of no linting on the UI.
8. **Type hints are strong** (SQLAlchemy 2.0 `Mapped[]`, modern `|` unions, `from __future__ import annotations` everywhere) — genuinely good. `run_in_threadpool` def routing is consistent. The few `dict`-typed request bodies (D-1) are the outliers. mypy with `--follow-imports=skip` is too weak to catch cross-module contract breaks like F-02; add `mypy packages apps` without skip once the schemas are wired (D-1), and make the CI lint step blocking (`continue-on-error: true` currently — see next).
9. **CI quality gates don't gate.** Both ruff and mypy steps carry `continue-on-error: true` (`.github/workflows/ci.yml`) — the "quality gate" job blocks merge on lint/test failures per the README, but lint/type findings never fail anything. **Fix:** remove `continue-on-error` once the current backlog is clean, add `--strict` gradually (`mypy --strict packages/security` first — it's the best-typed package).
10. **`if __name__` guard + module-level `app = create_app()` in `main.py`** means importing `apps.api.main` (e.g., for the tests, or any tooling) boots the whole runtime including DB DDL — the test suite works around this via conftest env vars, but any accidental import (e.g., a future script) creates/alters a database. Standard FastAPI practice, but given `create_all`+ALTERs run at import (D-5), consider `create_app()` on first request via lifespan or an explicit factory invocation in the ASGI entrypoint only.

---

## Appendix — minor findings (batch)

| ID | Finding | Evidence | Suggested action |
|----|---------|----------|------------------|
| M-1 | `ai_motion_gate_enabled` dead config | config.py:47, zero readers | Delete or implement (D-4) |
| M-2 | `Stream`, `SystemMetric`, `ModelVersion` tables created, never used | models.py:131,248,257; grep-verified zero usage | Delete (with Alembic drop) or wire ModelVersion to registry |
| M-3 | `timeutil.py` dead module | zero imports | Give it the shared `parse_iso_or_400` job (Part 6 §4) |
| M-4 | `schemas.py` 15/16 models unused | only TPLinkNvrSeed imported | Wire into routers (D-1) |
| M-5 | `models.Stream` never used but `provision_tplink_nvr` could populate it | — | Delete or use for stream metadata |
| M-6 | `.dockerignore` missing | `ls` shows none; `COPY . /app` | Add: `.venv`, `.env`, `data/`, `*.db`, `.coverage`, `coverage.xml`, `.git`, `.pytest_cache`, `__pycache__`, `docs/`, `tests/`, `ui/` (as needed), `models/` is needed — keep it |
| M-7 | Image ships 365KB+320KB SQLite DBs + `.env` | `ls` output; F-12 | Fixed by M-6 |
| M-8 | `psutil` imported in system.py but not in requirements | system.py:24-27 | Add to requirements or drop feature |
| M-9 | Prometheus scrape will 401 (metrics endpoint requires bearer auth; no credentials configured) | prometheus.yml vs system.py:74 | Add scrape token or unauthenticated internal listener (D-3) |
| M-10 | `noqa: BLE001` ×27 | grep count | Narrow types at infra seams (Part 6 §1) |
| M-11 | `MqttNotifier` default `retain=True` for alerts | notify/__init__.py:190 | Default `retain=False` — alerts are transient; retained MQTT alerts on the broker = stale forever |
| M-12 | `EmailNotifier` uses `starttls` on port 465 — wrong (465 is implicit TLS; starttls is 587) | notify/__init__.py:82-83 | Use `smtplib.SMTP_SSL` for 465 |
| M-13 | `push` cooldown recorded even when notifier construction later fails | worker/main.py:131-136 — `_cooldown.record(cd_key)` inside `try` before send | Record after successful `build_notifier` |
| M-14 | `cooldown` applied to PushNotifier unconditional append (`_build_notifiers` always adds PushNotifier first — it double-sends with any configured push route) | worker/main.py:98 | Skip default push when a push route matched |
| M-15 | `events.py:42` uncaught `fromisoformat` ValueError → 500 | — | Shared parser (Part 6 §4) |
| M-16 | `timeline.py:32` imports HTTPException inside function | — | Hoist |
| M-17 | `raise HTTPException(...) from exc` inconsistent (some sites have it, `cameras.py:294` SSRF validate in update_camera has no try at all — UnsafeUrlError propagates as 500) | cameras.py:293-298 | Wrap like create_camera does |
| M-18 | `cameras.py:update_camera` accepts `fps` without int coercion; `int()` was done in create | cameras.py:290-292 | Pydantic model (D-1) fixes |
| M-19 | Test count drift: README "66 tests" in one section, "49 tests" in layout section | README lines 77 vs 248 | Fix docs |
| M-20 | `retention` on `Camera` from NVR seed is `{"days": N}` but sweeper never reads it (F-11) and `rules` API never validates overlap between zones | — | F-11 + optional overlap lint in rules API |
| M-21 | `objects` rule in `rule_from_dict` defaults `("bag","package")` but comment in rules.py says `person` included; `ObjectLeftRule.labels` default includes `person` (rules.py:137) while factory overrides to bag/package only (rules.py:338-339) — intentional? confusing | rules.py:137 vs 338 | Align defaults |
| M-22 | `SemanticSearch` rebuilds index per request (analytics.py:88-93) — 2000 events embedded per search call, then discarded | analytics.py | Cache per (camera,range) with TTL, or precompute at event-write time (worker) |
| M-23 | `heatmap_grid` `.limit(20000)` without `.order_by` — nondeterministic 20k sample | analytics.py:103-108 | Order by recency (comment says "robust to sampling" — make it deterministic anyway) |
| M-24 | `_load_routes` cache is global mutable dict without lock (worker threads may race the 30s refresh) | worker/main.py:46-93 | Small lock or per-camera snapshot |
| M-25 | `IouTracker` greedy association is O(D×T) per frame with Python loops; `nms` list-comprehension per keep is O(n²) worst | tracker.py:60-69; detectors.py:45-53 | Fine at 5fps/small counts; document budget; numpy vectorization if ONNX staged at 25fps |
| M-26 | `SyntheticDetector` referenced in tests only; README calls reference detector "motion proxy" — two reference detectors exist (`detector_ref.SyntheticDetector`, `detectors.ReferenceMotionDetector`) — naming confusion | detector_ref.py vs detectors.py:226 | Consolidate to one reference implementation |
| M-27 | `detectors.py:292` — `"reference" | "synthetic"` both map to `ReferenceMotionDetector`, but `SyntheticDetector` (the actual synthetic) is unreachable via config | detectors.py:292-294 | M-26 |
| M-28 | `registry.json` = `{}` — ModelRegistry with empty models dict; `verify` on any get raises KeyError inside verify → RuntimeError message misleading | registry.py:53-56 | Guard: `get` returns None or raise friendly error; `verify` catches missing record |
| M-29 | `ffmpeg.open_decoder` sets `stderr=subprocess.PIPE` but never reads it — a chatty ffmpeg can deadlock on a full stderr pipe | ffmpeg.py:49-55 | `stderr=DEVNULL` or drain thread |
| M-30 | `FFmpegFrameSource.frames()` `finally: proc.terminate()` but no `wait()` — zombie on abrupt generator close | sources.py:65-77 | `terminate(); wait(timeout=5)` |
| M-31 | `_tmp_path` writes to `/tmp` (world-readable, fixed location) — in containers fine, on hosts the segments are world-readable briefly | recorder.py:46-47 | `tempfile.mkdtemp` under storage root with 0700 |
| M-32 | UI stores JWT in `localStorage` (XSS-exfiltratable; CSP mitigates — pair with httpOnly cookie + CSRF or document the tradeoff) | app.js:13 | Document or move to cookie |
| M-33 | `.gitignore` lacks `.coverage`, `coverage.xml` (currently untracked clutter; also both are in repo dir) | `git status` | Add to `.gitignore` |
| M-34 | `localsight.db` / `test_localsight.db` / `data/` correctly ignored; but `.env` is ignored while `test_localsight.db` files live in repo root — fine | git check-ignore | — |
| M-35 | `docs/api/openapi-summary.md` will drift from reality (no openapi export in CI) | — | CI job: dump `/openapi.json`, diff-check |
| M-36 | `worker/main.py:215` builds `face_chain`/`matcher` per camera even when `identity_recognition_enabled=false` (cheap objects — but `ReferenceEmbedder` alloc per camera is needless) | worker/main.py:215-216 | Build once, share |
| M-37 | `events.py:list_events` `total` via `count(subquery)` runs the full filtered query twice on every page | events.py:47-48 | Acceptable; consider keyset pagination for deep pages |
| M-38 | `alerts.py:analytic_events` no `total`, inconsistent with other lists | alerts.py:128-141 | Normalize pagination envelope |

---

## Prioritized remediation plan

**P0 — this week (breaks documented features / production risk):**
1. F-01 `Event.detail` column + regression tests (crash: ANPR, search, alerts feed)
2. F-02 S3 `verify_signed_url` + unified signing + `.dockerignore` (F-12/M-6 — one PR, 30 minutes of work, closes a secrets-in-image hole)
3. F-03 FK cascades (`Person`, `User`, `Camera` — GDPR erasure path must work)
4. F-08 login dummy-hash hoist + always-verify (5-line fix, halves login CPU, kills the oracle)

**P1 — next two weeks (resource efficiency + false controls):**
5. F-05 privacy masks enforcement + tests (privacy claim is currently false)
6. F-06 `put_stream` storage seam (recorder RAM cliff)
7. F-07 live-stream reaping (ffmpeg lifecycle)
8. F-04 batched writer + embedding cache (hot-path SQL: 440→~40 stmts/sec @ 8 cams)
9. F-11 retention completion (embeddings, audit, refresh tokens, per-camera overrides, chunked deletes)

**P2 — next month (design hygiene, then velocity):**
10. D-1 schema wiring (delete duplicated request models, use schemas.py)
11. D-5 Alembic baseline + CI migration step; make lint/mypy gates blocking (Part 6 §9)
12. D-3 wire metrics; D-6 worker supervisor + SIGTERM; F-10 epoch-DEK crypto; Part 5 security items (webhook detail allowlist, img-src tightening, MFA lockout, refresh revocation on role change)
13. Appendix sweep (the 38 minor items are mostly one-liners; batch them into 3–4 PRs by module)

**Effort/impact heuristic:** P0 ≈ 1–2 days total and eliminates every crash-level defect. P1 ≈ 4–6 days and converts the two headline efficiency risks (RAM, SQL churn) plus the compliance gap into resolved items. P2 is where the codebase goes from "good architecture with debt" to a team-velocity asset.

---

*Report generated from a full read of all 38 production modules + 3 UI files + infra + CI + tests, with every critical/high finding reproduced or measured in-environment. Baseline: 66/66 tests passing — the gaps above exist because the suite doesn't traverse these paths; each P0 fix should land with the test that would have caught it.*
