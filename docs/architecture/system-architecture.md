# System Architecture

## High-level data flow

```
                 ┌──────────────────────────┐
                 │       NVR / Cameras      │  RTSP/RTSPS/ONVIF (camera VLAN)
                 └────────────┬─────────────┘
                              │  substream (low-res) ──► AI
                              │  main stream (hi-res) ──► Recording
                              ▼
                 ┌──────────────────────────┐
                 │      Stream Gateway       │  FFmpeg/GStreamer, hw decode,
                 │  reconnect + backoff      │  bounded queue, frame drop
                 └────────────┬─────────────┘
            ┌─────────────────┴─────────────────┐
            ▼                                   ▼
   Main Recording (storage)              AI Substream
                                          │
                                    Person Detection (swappable)
                                          │
                                      Tracking (IOU)
                                          │
                              Face detect + embed (optional)
                                          │
                                  Identity search (vector)
                                          ▼
                                  Event / Metadata Engine
                              (dedup → presence intervals)
                                          │
                ┌─────────────────────────┼─────────────────────────┐
                ▼                         ▼                         ▼
          PostgreSQL                Object Storage          Vector Index
          (metadata/refs)          (video/snapshots)       (embeddings)
                └─────────────────────────┴─────────────────────────┘
                                          ▼
                                    API / WebSocket
                                          ▼
                                   Web Dashboard
```

## Security boundaries (each enforces authN/Z, validation, rate-limit, logging)

```
Camera network ──▶ Stream ingestion boundary ──▶ AI processing boundary
        ──▶ Metadata boundary ──▶ Application boundary ──▶ User boundary
```

## Processing pipeline (per camera)

```
RTSP → decode → [motion gate] → frame sampling → person detection →
tracking → face detection (tracked people only) → quality filter →
embedding → identity search (throttled ~1–3s/track) → event aggregation
```

## State model

- **Camera status** (independent per camera): `ONLINE | DEGRADED | OFFLINE | RECONNECTING`
  with exponential backoff + jitter (1,2,5,10,30,60s) and never hammering the NVR.
- **Track** = ephemeral `camera-01-track-1842`; never the real identity.
- **Event** = aggregated presence window with `first_seen`/`last_seen`, confidence,
  snapshot/video refs, and an explicit `identity_status` of `known|unknown|uncertain`.

## Scaling story (Kubernetes-ready, not required)

- API: stateless, horizontally scalable behind the proxy.
- Worker: one process per host; multiple workers share DB/storage and process
  disjoint camera sets. GPU scheduling is a future bounded scheduler.
- Storage: local disk → S3-compatible; DB: Postgres with partitioning on `events`.
