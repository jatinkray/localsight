# Database ERD

All tables use UUID primary keys (string hex), are UTC-timestamped, and store
*references/metadata*, never large video blobs. Sensitive columns are encrypted by
the application before insert.

## Tables and key relationships

```
User(1) ──< (N) Role(1) ──< (N) Permission          # RBAC
User(1) ──< (N) RefreshToken                        # refresh rotation/revocation
User(1) ──< (N) AuditLog                            # immutable audit

NvrDevice(1) ──< (N) Camera                         # discovered/added cameras
Camera(1) ──< (N) Stream                            # main + sub per camera
Camera(1) ──< (N) Detection                         # raw per-frame detections
Camera(1) ──< (N) Track                             # ephemeral tracked persons
Camera(1) ──< (N) Event                             # aggregated presence intervals
Camera(1) ─< (N) VideoSegment, Snapshot

Person(1) ──< (N) PersonEmbedding                  # encrypted biometric vectors
Person(1) ──< (N) Event(identity_id)               # known identity linkage
Track(identity_id) ── Person(optional)

Event ──< Snapshot, VideoSegment                   # media references (encrypted keys)
SystemMetric(ts, name, value, tags)                # observability
ModelVersion(name, version, hash, source, license) # AI supply-chain integrity
```

## Indexes (hot paths)

- `events(camera_id, timestamp_start)`
- `events(identity_id, timestamp_start)`
- `tracks(camera_id, last_seen)`
- `detections(camera_id, frame_ts)`
- `audit_logs(ts, action)`

## Encryption scope (application-layer envelope)

Encrypted at rest: `cameras.stream_url_enc`, `cameras.substream_url_enc`,
`nvr_devices.username_enc/password_enc`, `users.mfa_secret_enc`,
`person_embeddings.embedding_enc`, `snapshots.storage_key_enc`,
`events.snapshot_key_enc`, `events.video_segment_key_enc`.

The DB stores only ciphertext for these columns; keys never touch the database.
```
