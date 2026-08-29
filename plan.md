Absolutely. Below is a master product/engineering prompt designed to give to an AI coding agent such as Cursor, Claude Code, or another software-engineering agent. It frames the system as a production-grade, local-first video intelligence platform, with bandwidth optimization, edge processing, security, observability, and a clean product architecture.

Master Prompt — Local-First NVR Video Intelligence Platform
Role

Act as a Principal Software Architect, AI/ML Architect, Network Architect, Security Architect, DevOps Engineer, and Product Designer.

Design and implement a production-grade, self-hosted video intelligence platform that continuously ingests video from NVRs/IP cameras, detects and tracks people using local AI models, optionally identifies known people using privacy-preserving biometric processing, generates timestamped events, and provides a secure web interface for searching and reviewing an entire day's activity.

The product must be designed around these principles:

Local-first / cloud-optional
Privacy by design
Zero unnecessary video movement
Bandwidth-efficient architecture
GPU-accelerated local AI
Security-first infrastructure
Fault tolerance and automatic recovery
Observable and auditable
Modular AI pipeline
Production-ready rather than a prototype

Do not simply create a demo. Produce an architecture and implementation that could realistically be deployed on-premises for 24/7 operation.

1. Product Definition

Build a platform tentatively named:

LocalVision

The platform connects to one or more NVRs/IP cameras and continuously processes their streams.

Core capabilities:

Discover/configure NVRs and cameras.
Connect to RTSP/RTSPS streams.
Support multiple cameras.
Continuously monitor camera health.
Detect people.
Track people across frames.
Optionally detect faces.
Optionally identify authorized/known individuals.
Record timestamps for detections.
Generate first-seen / last-seen intervals.
Store searchable events.
Store event snapshots and/or short video clips.
Maintain configurable video retention.
Provide daily activity timelines.
Search by:
date
time range
camera
person
known/unknown
detection confidence
Display the corresponding video segment when an event is selected.
Provide system health and camera health dashboards.
Support multiple users with role-based permissions.

The system should continue operating even if the internet connection is unavailable.

2. High-Level Architecture

Use an architecture similar to:

                  ┌──────────────────────────┐
                  │       NVR / Cameras      │
                  │ RTSP / RTSPS / ONVIF     │
                  └────────────┬─────────────┘
                               │
                               ▼
                  ┌──────────────────────────┐
                  │ Stream Gateway           │
                  │ FFmpeg / GStreamer       │
                  │ Hardware decode          │
                  └────────────┬─────────────┘
                               │
                    ┌──────────┴──────────┐
                    │                     │
                    ▼                     ▼
             Main Recording         AI Substream
             High quality           Low bandwidth
                    │                     │
                    │                     ▼
                    │            Person Detection
                    │                     │
                    │                     ▼
                    │                 Tracking
                    │                     │
                    │                     ▼
                    │              Face Detection
                    │                     │
                    │                     ▼
                    │          Optional Identification
                    │                     │
                    └──────────┬──────────┘
                               ▼
                     Event/Metadata Engine
                               │
                ┌──────────────┼───────────────┐
                ▼              ▼               ▼
            PostgreSQL       Object        Vector/Embedding
            Metadata         Storage          Index
                │              │               │
                └──────────────┼───────────────┘
                               ▼
                       API / WebSocket
                               │
                               ▼
                         Web Dashboard


Separate the video plane from the metadata/control plane.

Never send full-resolution video through the API server unless explicitly required.

3. Network Optimization — Critical Requirement

Optimize network utilization aggressively.

The architecture must avoid repeatedly transferring the same video.

Preferred strategy

If the NVR provides both a main stream and substream:

Main stream
   ↓
Local recording

Substream
   ↓
AI inference


Use the lowest-resolution substream that still provides acceptable detection accuracy.

Example:

Camera main stream:
1920x1080 @ 15 FPS

AI stream:
640x360 @ 5 FPS


The AI pipeline should NOT process every frame of the main stream.

Implement configurable:

inference FPS
detection resolution
keyframe interval
motion gating
frame skipping
region-of-interest processing
codec selection
hardware decoding

The system should calculate:

network bandwidth per camera
network bandwidth aggregate
decode FPS
AI FPS
GPU utilization
CPU utilization
storage write rate


Expose these metrics in the dashboard.

4. Prefer Local Stream Processing

Where possible:

NVR → Local Server


rather than:

NVR → Cloud → AI Server


All AI inference should happen locally.

Internet connectivity must NOT be required for:

person detection
tracking
face detection
identity matching
video recording
event search
dashboard operation

Cloud integrations, if implemented, must be explicitly opt-in.

5. Stream Ingestion

Implement a robust stream ingestion layer.

Support:

RTSP
RTSPS where supported
ONVIF camera discovery/configuration where practical
H.264
H.265
TCP transport
UDP transport when explicitly configured

Use FFmpeg or GStreamer.

The ingestion layer must:

reconnect automatically
detect stalled streams
detect corrupt frames
maintain connection health
handle camera reboot
handle NVR reboot
recover after network interruption
expose stream metrics
avoid uncontrolled process spawning

Use bounded queues.

Never allow a slow AI inference pipeline to cause unlimited frame buffering.

When overloaded, drop frames intelligently rather than allowing memory growth.

6. AI Architecture

Design AI as replaceable modules.

Do not hard-code the entire application around one AI model.

Define interfaces such as:

Detector
Tracker
FaceDetector
FaceEmbedder
IdentityMatcher
AttributeDetector
EventClassifier


The system must allow models to be replaced independently.

Prefer local models that can run using:

NVIDIA CUDA
TensorRT where beneficial
ONNX Runtime
CPU fallback
Intel/AMD accelerators where practical

The implementation must benchmark model choices rather than assuming one model is universally optimal.

7. Person Detection

Implement a local object detection model capable of detecting:

person


Optionally support:

vehicle
bicycle
motorcycle
animal


but keep the initial product focused on people.

The detection pipeline should support configurable confidence thresholds.

Example:

detection:
  confidence_threshold: 0.45
  iou_threshold: 0.50
  inference_fps: 5


Do not run inference at unnecessary FPS.

If no motion is detected, dynamically reduce inference frequency.

8. Tracking

Use a local multi-object tracking system.

The tracker should provide:

track_id
camera_id
first_seen
last_seen
bounding_box
trajectory
confidence


A person should receive a temporary tracking ID:

camera-01-track-1842


Do not confuse a tracking ID with a person's real identity.

Tracking IDs are ephemeral.

9. Identity Recognition

Identity recognition must be an optional module, not a requirement for basic operation.

Support two modes:

Mode A — Person detection only
Person detected
Camera 1
10:31:24

Mode B — Authorized identity recognition
Person detected
Candidate: Person-001
Confidence: 0.91
10:31:24


Use an embedding-based architecture.

Pipeline:

Person
  ↓
Face detection
  ↓
Quality check
  ↓
Face alignment
  ↓
Embedding model
  ↓
Vector similarity search
  ↓
Threshold
  ↓
Known / Unknown


Do not automatically label a person when similarity is below the configured threshold.

Use:

known
unknown
uncertain


rather than forcing a false identity.

Provide configurable thresholds and evaluation tools.

10. Biometric Privacy

Treat face embeddings and identity data as highly sensitive.

Implement:

encryption at rest
encryption in transit
strict access control
audit logging
configurable retention
deletion workflows
export controls
administrative approval for enrollment
explicit enrollment process
no automatic creation of identities from surveillance footage
no cloud upload by default

Do not store raw face images indefinitely merely because an embedding exists.

Allow administrators to configure:

raw face image retention
embedding retention
event retention
video retention
audit-log retention


Implement privacy-preserving defaults.

The product must be designed so biometric recognition can be completely disabled.

Where applicable, require the deploying organization to establish an appropriate lawful basis, notice/consent process, retention policy, and access policy before enabling biometric identification. Do not assume that technical authorization makes biometric processing legally permissible.

11. Identity Enrollment

Provide a secure administrator workflow:

Create Person
    ↓
Upload/select authorized reference image
    ↓
Quality validation
    ↓
Generate embedding locally
    ↓
Encrypt/store embedding
    ↓
Audit enrollment


Never send enrollment images to an external API.

Allow multiple reference images per person.

Example:

Person:
employee-001

Reference embeddings:
embedding-01
embedding-02
embedding-03


Use multiple references to improve robustness.

12. Event Model

Create a normalized event structure.

Example:

{
  "event_id": "uuid",
  "camera_id": "camera-01",
  "track_id": "track-1842",
  "identity_id": "person-001",
  "identity_status": "known",
  "timestamp_start": "2026-08-30T10:31:24Z",
  "timestamp_end": "2026-08-30T10:34:12Z",
  "confidence": 0.91,
  "bounding_box": {},
  "snapshot_uri": "...",
  "video_segment_uri": "..."
}


Use UTC internally.

Convert to local timezone only in the UI.

13. Event Deduplication

Do not create thousands of events because a person remains visible for several minutes.

Implement event aggregation.

Example:

10:00:00 detected
10:00:01 detected
10:00:02 detected
...
10:05:42 detected


Should become:

Person present
10:00:00 → 10:05:42


Allow configurable gap tolerance:

event:
  merge_gap_seconds: 10

14. Video Storage

Use a tiered storage model.

Hot storage

Recent video and frequently accessed clips.

Warm storage

Older daily recordings.

Cold storage

Optional archival storage.

Use segmented files rather than enormous single-day files.

Example:

camera-01/
  2026/
    08/
      30/
        00-00.mp4
        00-05.mp4
        00-10.mp4
        ...


Prefer formats/codecs that provide practical seeking and efficient storage.

Use configurable retention:

retention:
  video_days: 7
  event_days: 30
  snapshots_days: 14
  audit_days: 365


Never silently delete data outside the configured policy.

15. Video/Metadata Separation

The database should store references, not large video blobs.

For example:

PostgreSQL
    ↓
event metadata
    ↓
object/file storage
    ↓
video segment


Use an abstraction:

StorageProvider


Implement:

LocalFilesystemStorage
S3CompatibleStorage


The initial deployment should work entirely on local disks.

16. Database

Use PostgreSQL for production.

Core tables:

users
roles
permissions

cameras
nvr_devices
streams

persons
person_embeddings

detections
tracks
events

video_segments
snapshots

audit_logs
system_metrics
model_versions


Use UUIDs.

Add appropriate indexes for:

camera_id
timestamp
identity_id
event_type
track_id


Optimize time-range queries.

Consider PostgreSQL partitioning for very large event volumes.

17. Vector Search

Use a local vector search solution.

Prefer PostgreSQL + pgvector initially to minimize infrastructure complexity.

Store:

embedding
model_version
embedding_dimension
person_id
created_at


Never compare embeddings generated by incompatible models without explicit migration/version handling.

Every embedding must carry its model version.

18. API Architecture

Implement a secure REST API.

Optional:

WebSocket


for live event updates.

Example APIs:

POST   /api/auth/login

GET    /api/cameras
POST   /api/cameras
DELETE /api/cameras/{id}

GET    /api/events
GET    /api/events/{id}

GET    /api/persons
POST   /api/persons
DELETE /api/persons/{id}

GET    /api/timeline

GET    /api/video/{segment}

GET    /api/system/health
GET    /api/system/metrics


Implement pagination.

Never return unlimited event records.

19. Authentication

Implement strong authentication.

Support:

username/password
Argon2id password hashing
session expiration
refresh-token rotation
secure cookies where applicable
optional WebAuthn/passkeys
optional TOTP MFA
account lockout/rate limiting
password reset with secure one-time tokens

Never store plaintext passwords.

Never log credentials or tokens.

20. Authorization

Implement RBAC.

Example roles:

ADMIN
SECURITY_OPERATOR
ANALYST
VIEWER


Example permissions:

camera:view
camera:configure
video:view
video:export
person:view
person:enroll
person:delete
events:view
events:export
system:configure
audit:view


An ordinary viewer must not be able to enroll or delete identities.

21. Network Security

Assume the NVR and camera network is hostile.

Recommended architecture:

Camera VLAN
     │
     │ restricted
     ▼
Video Processing Server
     │
     ├── Management VLAN
     │
     └── User VLAN


Do not expose cameras directly to the internet.

Do not expose RTSP ports publicly.

Firewall rules should follow least privilege.

Example:

Cameras → Video server: RTSP only
Users → Video server: HTTPS only
Video server → Internet: disabled by default


If internet access is required for updates, allow controlled outbound access only.

22. TLS

Use HTTPS everywhere for application access.

For higher-security deployments support:

TLS 1.3
mTLS
internal CA
certificate rotation


Never transmit credentials or identity information over plaintext HTTP.

23. Secrets Management

Never store secrets in source code.

Do not place:

NVR passwords
database passwords
JWT secrets
encryption keys
API keys


in Git.

Support:

Docker secrets
environment-based secrets
Vault-compatible secret management


Encrypt sensitive configuration at rest where appropriate.

24. Encryption

Implement:

In transit

TLS.

At rest

Encrypt:

database
embeddings
snapshots
exported video
credentials
sensitive configuration

Use envelope encryption where practical.

Separate encryption keys from encrypted data.

Design for key rotation.

25. Audit Logging

Create immutable-style audit records for security-sensitive actions.

Log:

login
logout
failed login
identity enrollment
identity deletion
identity modification
video export
video deletion
camera configuration
user creation
permission changes
system configuration changes


Each event should include:

timestamp
user
source IP
action
resource
result
request ID


Never put passwords, access tokens, or raw biometric data in logs.

26. Video Export Security

Video exports can contain sensitive information.

Implement:

permission checks
export audit logs
configurable maximum export duration
optional watermarking
signed download URLs
expiration
download logging
optional encryption

Never create permanent publicly accessible video URLs.

27. API Security

Implement:

request validation
schema validation
rate limiting
CSRF protection where applicable
CORS allowlist
security headers
request size limits
timeout limits
pagination limits
SSRF protection
path traversal protection
SQL injection protection
command injection protection
secure file handling

Never pass user-controlled strings directly into shell commands.

For FFmpeg/GStreamer process invocation, use structured argument arrays rather than shell interpolation.

28. SSRF Protection

This is particularly important because users configure camera/NVR URLs.

Do not allow an authenticated user to use the platform as a generic network proxy.

Validate camera destinations.

Prevent access to unauthorized:

localhost
127.0.0.1
private management endpoints
cloud metadata services
internal admin services


unless explicitly permitted by deployment configuration.

Use network-level egress restrictions in addition to application-level validation.

29. AI Security

Treat AI models as application dependencies.

Maintain:

model name
model version
hash
source
license
configuration


Verify model integrity before loading.

Do not dynamically download arbitrary models from user-supplied URLs.

Use an approved local model registry.

AI inference must be isolated from the public network.

30. AI Accuracy and False Positives

Never claim identity with absolute certainty.

Every recognition result should have:

identity
similarity/confidence
threshold
model version
quality score


Implement three states:

KNOWN
UNKNOWN
UNCERTAIN


Allow administrators to tune thresholds.

Provide evaluation tooling so operators can measure:

false positive rate
false negative rate
precision
recall
identity confidence distribution


Do not optimize only for maximum recognition rate.

Safety and false-positive reduction are more important than aggressive matching.

31. Processing Optimization

Use a pipeline such as:

RTSP
 ↓
Hardware Decode
 ↓
Motion Detection
 ↓
Frame Sampling
 ↓
Person Detection
 ↓
Tracking
 ↓
Face Detection only for tracked people
 ↓
Quality Filter
 ↓
Face Embedding
 ↓
Identity Search
 ↓
Event Aggregation


Do not run expensive face recognition if there are no people.

Do not run face recognition on every frame.

Cache recent embeddings for active tracks.

Example:

recognize track every 1–3 seconds


rather than:

recognize 30 times/sec


Make this configurable.

32. GPU Scheduling

Support multiple cameras sharing one GPU.

Implement a bounded inference scheduler.

Example:

Camera 1 → detection queue
Camera 2 → detection queue
Camera 3 → detection queue
Camera 4 → detection queue
                 ↓
          GPU Scheduler
                 ↓
             inference


Avoid creating one uncontrolled inference process per camera.

Expose:

GPU utilization
VRAM usage
inference latency
queue depth
FPS
dropped frames

33. Graceful Degradation

If GPU becomes unavailable:

GPU unavailable
    ↓
reduce inference FPS
    ↓
CPU fallback


If CPU becomes overloaded:

reduce inference frequency


If storage is nearly full:

alert
    ↓
apply configured retention policy


Never crash the entire platform because one camera fails.

34. Camera Failure Handling

Each camera must have independent state:

ONLINE
DEGRADED
OFFLINE
RECONNECTING


Implement exponential backoff:

1 sec
2 sec
5 sec
10 sec
30 sec
60 sec


with jitter.

Do not hammer an offline NVR.

35. Time Synchronization

Timestamp accuracy is critical.

Use NTP.

All services should use synchronized clocks.

Store timestamps internally in UTC.

The UI should support:

user timezone
camera timezone
UTC


Detect significant clock drift and report it.

36. Daily Timeline

Build a timeline UI:

00:00 ───────────────────────── 24:00

Camera 1
       ███          █████
       Person A     Person B

Camera 2
              ██
              Unknown


Clicking an event should show:

identity
confidence
camera
timestamp
duration
snapshot
video playback


Provide:

±10 seconds
±30 seconds
±1 minute


context playback around an event.

37. Search

Provide fast queries such as:

Show all people seen today.

Show Person A between 08:00 and 12:00.

Show unknown people on Camera 3.

Show all activity between 18:00 and 20:00.

Show first appearance of Person A today.

Show all cameras where Person A was detected.


Return results efficiently using indexed metadata.

38. Product UX

Design a modern security operations dashboard.

Primary navigation:

Dashboard
Cameras
Live View
Timeline
Events
People
Search
Video Archive
System Health
Audit Logs
Settings


Dashboard should immediately show:

Cameras online: 12/12
People detected today: 184
Known identities: 17
Unknown events: 42
GPU: 63%
Storage: 71%


Use dark mode as the default.

Prioritize clarity over visual decoration.

39. Live View

Live video should preferably be delivered efficiently.

Do not proxy unnecessary high-bitrate streams through the API server.

Consider:

WebRTC
HLS
LL-HLS


depending on latency requirements.

Use a dedicated media gateway if required.

The architecture should allow direct or optimized local media delivery while keeping authentication and authorization enforced.

40. Containerization

Provide Docker Compose for local deployment.

Example services:

frontend
api
worker
stream-gateway
postgres
redis
object-storage
reverse-proxy


Do not add infrastructure merely because it is fashionable.

If Redis is unnecessary for the first version, don't introduce it.

Keep the architecture operationally simple.

41. Kubernetes Readiness

The initial deployment can be Docker Compose, but architecture should be Kubernetes-compatible.

Prepare for:

horizontal API scaling
GPU nodes
worker scaling
persistent volumes
health checks
rolling deployments


AI workers should be independently scalable.

42. Observability

Implement:

Metrics

Prometheus-compatible metrics.

Track:

camera_fps
camera_disconnects
stream_latency
frames_processed
frames_dropped
detections_per_minute
recognition_requests
recognition_latency
GPU_utilization
GPU_memory
CPU_utilization
RAM
storage_usage
database_latency
API_latency
queue_depth

Logs

Structured JSON logs.

Tracing

Use OpenTelemetry-compatible tracing where practical.

Every request should have a correlation/request ID.

43. Health Checks

Implement:

/health/live
/health/ready


Separate:

liveness
readiness
dependency health


The platform should detect:

database unavailable
storage unavailable
GPU unavailable
camera unavailable
AI worker unavailable


independently.

44. Backup and Recovery

Back up:

database
identity metadata
encrypted embeddings
configuration
audit logs


Video backup should be configurable because of its size.

Provide documented restore procedures.

Test restore procedures.

A backup that has never been restored should not be considered reliable.

45. Data Retention

Retention must be explicit and configurable.

Example:

retention:
  recordings_days: 7
  events_days: 30
  snapshots_days: 14
  embeddings_days: 90
  audit_logs_days: 365


Support per-camera retention policies.

Support legal/administrative retention overrides where appropriate.

Deletion must be auditable.

46. Privacy Controls

Provide a privacy configuration panel.

Controls:

Enable person detection
Enable face detection
Enable identity recognition
Enable face snapshots
Enable recording
Retention periods
Identity matching threshold
Mask faces in exports
Mask non-relevant regions


Implement optional privacy masks:

Region A → ignore
Region B → ignore


This allows sensitive areas to be excluded from processing.

47. Multi-Tenant Design

Design the data model so tenant isolation can be introduced later.

Every security-sensitive object should be associated with an appropriate scope.

Never rely solely on frontend filtering for authorization.

Authorization must be enforced server-side.

48. Secure Development

Apply:

dependency pinning
SBOM generation
vulnerability scanning
static analysis
container scanning
secret scanning
dependency updates
signed releases where possible
reproducible builds where practical

Use a secure CI/CD pipeline.

49. Testing

Implement:

Unit tests

For:

event aggregation
timestamp handling
authorization
retention
threshold logic
Integration tests

For:

NVR connection
stream reconnection
database
storage
AI workers
Security tests

For:

authentication
authorization
SSRF
path traversal
injection
rate limiting
token handling
Load tests

Simulate:

1 camera
4 cameras
8 cameras
16 cameras
32 cameras


Measure:

CPU
GPU
RAM
network
storage
latency
dropped frames

50. Capacity Planning

Create a capacity calculator.

Inputs:

number_of_cameras
resolution
camera_fps
codec
main_stream_bitrate
substream_bitrate
AI_fps
retention_days


Calculate:

network bandwidth
daily storage
monthly storage
GPU requirements
CPU requirements
RAM requirements


Example formula:

Daily storage ≈ bitrate(bits/sec) × 86400 / 8


Include filesystem/database overhead.

51. Bandwidth Budgeting

For every camera calculate:

recording bandwidth
AI bandwidth
live-view bandwidth
export bandwidth


Do not assume bandwidth is unlimited.

The dashboard should display:

Camera 01
Recording: 4.2 Mbps
AI:        0.4 Mbps
Live:      0 Mbps
Total:     4.6 Mbps


Allow administrators to configure bandwidth limits.

52. Intelligent Frame Sampling

Implement adaptive sampling.

Example:

No motion:
1 FPS

Motion detected:
5 FPS

Person detected:
5–10 FPS

Face recognition:
1 recognition / 2 seconds / active track


Make the policy configurable.

The objective is:

maximize useful AI information per byte and per GPU cycle.

53. Security Boundaries

Explicitly define:

Camera network
        ↓
Stream ingestion boundary
        ↓
AI processing boundary
        ↓
Metadata boundary
        ↓
Application boundary
        ↓
User boundary


Each boundary must have:

authentication where appropriate
authorization
validation
rate limiting
monitoring
logging
54. Threat Model

Produce a threat model covering at minimum:

Compromised camera
Compromised NVR
Malicious authenticated user
Stolen administrator credentials
Malicious video file
Malicious RTSP URL
SSRF
RCE through media processing
Malicious model
Database theft
Embedding theft
Video theft
API abuse
Denial of service
Storage exhaustion
GPU exhaustion
Supply-chain compromise
Insider threat


For every threat provide:

attack
impact
likelihood
mitigation
detection
recovery


Use STRIDE where useful.

55. Important Security Requirement

Treat FFmpeg/GStreamer and AI model runtimes as potentially dangerous processing components.

Run them with:

least privilege
non-root users
restricted filesystem access
restricted network access
resource limits
CPU limits
memory limits
process limits
seccomp/AppArmor where appropriate

Do not give video-processing containers unnecessary access to the host.

56. Product Architecture Deliverables

Before writing implementation code, produce:

Product Requirements Document
Architecture Decision Record
System architecture diagram
Network architecture diagram
Data-flow diagram
Database ERD
API specification
Threat model
Security architecture
AI pipeline architecture
Deployment architecture
Capacity model
Observability strategy
Disaster recovery strategy
Test strategy
UI/UX specification

Then implement the system incrementally.

57. Recommended Technology Direction

Use a pragmatic stack.

Backend

Prefer:

Python
FastAPI
Pydantic
SQLAlchemy
PostgreSQL

AI

Prefer:

PyTorch / ONNX Runtime
CUDA where available
TensorRT where benchmarking proves beneficial
Local object detection model
Local tracking model
Local face detection/embedding model
pgvector for initial vector search

Video
FFmpeg or GStreamer
RTSP/RTSPS
H.264/H.265
hardware decoding

Frontend

Prefer:

React
TypeScript
modern component system
WebSocket/SSE for live events

Deployment
Docker
Docker Compose
Linux
NVIDIA Container Toolkit where NVIDIA GPU is used


Do not introduce unnecessary microservices.

Use a modular monolith plus dedicated AI/video workers initially.

58. Suggested Repository Structure

Create:

localvision/
├── apps/
│   ├── api/
│   ├── web/
│   ├── worker/
│   └── stream-gateway/
│
├── packages/
│   ├── domain/
│   ├── ai/
│   ├── video/
│   ├── security/
│   ├── storage/
│   └── observability/
│
├── infrastructure/
│   ├── docker/
│   ├── compose/
│   ├── nginx/
│   └── monitoring/
│
├── migrations/
├── tests/
├── docs/
│   ├── architecture/
│   ├── security/
│   ├── operations/
│   └── api/
│
├── scripts/
└── README.md


Keep domain logic independent from infrastructure.

59. Implementation Phases

Implement in stages.

Phase 1 — Foundation

Build:

authentication
database
camera management
RTSP ingestion
health monitoring
basic UI

Phase 2 — Recording

Add:

continuous recording
segmentation
retention
storage management
playback

Phase 3 — Person Detection

Add:

local detector
tracking
event generation
timeline

Phase 4 — Identity

Add:

face detection
local embeddings
identity enrollment
vector search
known/unknown/uncertain classification

Phase 5 — Optimization

Add:

adaptive sampling
GPU scheduling
hardware decode
motion gating
performance profiling

Phase 6 — Enterprise Security

Add:

MFA
RBAC
audit logs
mTLS
advanced retention
export controls
security hardening

Phase 7 — Scale

Test:

4 cameras
8 cameras
16 cameras
32 cameras


and document hardware requirements.

60. Definition of Done

Do not consider the product complete until:

A camera can be configured.
The stream reconnects automatically.
Video can be recorded continuously.
Recording survives process restarts.
AI processing survives worker restarts.
People can be detected locally.
People can be tracked.
Events receive accurate timestamps.
Events are deduplicated.
Daily activity can be searched.
Known identities can optionally be enrolled.
Identity matching runs locally.
No video is sent to the cloud by default.
The system continues operating without internet access.
Authentication is mandatory.
Authorization is enforced server-side.
Sensitive operations are audited.
Sensitive data is encrypted.
Retention policies work automatically.
Camera failures are visible.
Storage exhaustion is handled safely.
GPU overload is handled safely.
Security tests pass.
Load tests pass for the declared capacity.
Backup and restore procedures are documented.
Threat model is documented.
Deployment is reproducible.
Configuration is documented.
61. Final Engineering Principle

Optimize the entire system around this objective:

Move the minimum amount of video, process the minimum number of frames necessary, perform AI inference locally, store primarily metadata rather than redundant data, and never sacrifice security or privacy for convenience.

The system should be local-first, bandwidth-efficient, GPU-efficient, privacy-aware, fault-tolerant, auditable, and production-ready.

Before implementation, identify architectural ambiguities and state assumptions explicitly. Where multiple technical choices exist, benchmark them and choose based on measurable CPU/GPU/network/storage cost, latency, reliability, privacy, and operational complexity rather than popularity.

One architectural recommendation

For the first production version, I would not make this a large microservice/Kubernetes system. A much better starting point is:

FastAPI + PostgreSQL/pgvector + FFmpeg/GStreamer + dedicated AI worker + React + Docker Compose, with the AI/video workers separated from the API.

The biggest performance win will come from using the NVR's low-resolution substream for AI while retaining the main stream for recording. That can dramatically reduce GPU work and network traffic without sacrificing the quality of the archived footage.

If you want, I can next turn this into a complete technical blueprint with database schema, Docker Compose, API endpoints, AI pipeline, folder structure, and the first implementation sprint, ready to hand directly to a coding agent.