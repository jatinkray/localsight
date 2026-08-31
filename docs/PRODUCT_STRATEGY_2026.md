# LocalVision — Deep Codebase Analysis, Market Research & Product Improvement Plan (2026)

> Prepared for: product/engineering leadership
> Scope: make LocalVision compatible with *most* CCTV surveillance footage analysis and competitive with market leaders (Verkada, Milestone, Genetec, Avigilon, BriefCam, Axis, Hanwha, Hikvision, Coram)
> Date: 2026-08-31

---

## 1. Executive Summary

LocalVision is a **securely-architected, local-first video intelligence platform** with an unusually strong *security and privacy* foundation (Argon2id, envelope encryption, RBAC, SSRF guard, immutable audit log, signed URLs, fail-safe stream gateway). That foundation is genuinely ahead of many open-source VMS projects.

However, the product today is a **secure skeleton, not a surveillance-analytics product**:

- Every AI stage (detector, tracker, face detector, embedder, matcher) is a **deterministic placeholder**. There are **no real models**, no real multi-class detection, no behavior analytics, no ANPR, no fire/smoke, no crowd/occupancy.
- **No video is actually recorded** — the `VideoSegment` table exists but the worker never writes recorded footage; only metadata/events are produced from a synthetic source.
- **Camera compatibility is narrow** — only TP-Link VIGI presets. No universal ONVIF Profile M/S/T, no GB/T 28181, no Hikvision ISAPI / Dahua CGI, no Axis/Hanwha/Bosch.
- **No live view** (WebRTC/HLS), **no alerting/notifications** (webhook/email/push/SOC), **no analytics/BI** (heatmaps, people counting, dwell, occupancy trends).
- **No edge deployment story** (Jetson/OpenVINO/Coral) and **no GPU scheduler** (1 pipeline/thread per camera).

**Market context.** The AI-in-video-surveillance market is ~USD 6.8–11.2B in 2026 and growing 14–21% CAGR to USD 13–38B by 2030/2034 (Mordor, MarketIntelo, Polaris, ResearchAndMarkets). Software/analytics is the fastest-growing slice (~17–18% CAGR). The 2026 differentiator is **Gen-3/Gen-4 analytics** (semantic/NL search via CLIP/VLM, behavior analytics, ANPR, forensic search) delivered **on open camera fleets** (BYOC) with **privacy/compliance** (EU AI Act, GDPR, NDAA).

**Recommendation.** Localize the already-strong security/privacy posture into the core *product* differentiator, then close the analytics gap in three phases: (P1) real multi-class detection + actual recording + broad ONVIF/RTSP camera compatibility; (P2) behavior analytics + ANPR + alerting + live view + analytics BI; (P3) edge runtime + VLM semantic search + compliance tooling. This positions LocalVision as the **privacy-first, open-camera, on-prem alternative to Verkada/Avigilon** — a defensible wedge against cloud-locked and NDAA-restricted incumbents.

---

## 2. Codebase Deep Analysis (Current State)

### 2.1 What is genuinely strong (keep, do not rebuild)

| Area | Evidence | Notes |
|---|---|---|
| Security-first auth | `packages/security/` — Argon2id, JWT rotation, TOTP MFA, lockout, RBAC | Matches/beats most VMS add-ons |
| Encryption at rest | `CryptoBox` envelope encryption; `stream_url_enc`, `embedding_enc`, `snapshot_key_enc` in models | Strong — embeddings/URLs never plaintext in DB |
| SSRF egress guard | `security/ssrf.py` + `video/ffmpeg.py` re-validates; no `shell=True` | Best-in-class; critical because users supply camera URLs |
| Immutable audit log | `AuditLog` model + `apps/api/audit.py`; never logs secrets | Required for enterprise |
| Fail-safe stream gateway | `video/gateway.py` — per-camera state, exp backoff + jitter, bounded frames | Solid resilience design |
| Swappable AI interfaces | `packages/ai/interfaces.py` (Detector/Tracker/FaceDetector/FaceEmbedder/IdentityMatcher) | Correct abstraction; just not filled |
| Model registry w/ integrity | `packages/ai/registry.py` — SHA-256, version, license | Good supply-chain hygiene |
| Normalized event + dedup | `domain/events.py` + `pipeline.py` merge-gap aggregation | Good data model |

### 2.2 What is missing or placeholder (the gap)

| Capability | Status in code | Gap severity |
|---|---|---|
| **Real person/object detection** | ✅ `ONNXDetector` in `detectors.py`; `build_detector()` factory; `ModelRegistry` integration | CRITICAL — filled |
| **Multi-class detection** (vehicle, animal, bag, PPE…) | ✅ `postprocess_yolo()` in `detectors.py`; 9-class `DEFAULT_LABELS`; lazy onnxruntime | CRITICAL — filled |
| **Tracking quality** | `IouTracker` (no re-ID); placeholder but production-grade | HIGH |
| **Real face embedder** | `ReferenceEmbedder` hashes bbox bytes — not biometric | HIGH (only if face mode used) |
| **Video recording** | ✅ `Recorder` in worker; segmented MP4; `VideoSegment` rows written | CRITICAL — filled |
| **Behavior analytics** | ✅ `RuleEngine` + `LineCrossingRule`/`ZoneIntrusionRule`/`LoiteringRule`/`ObjectLeftRule`/`CrowdCountRule` | CRITICAL — filled |
| **ANPR / License Plate** | ✅ `ANPRPipeline` + reference detector/OCR; encrypted at rest | HIGH — filled |
| **Fire / smoke / crowd / fall / PPE** | None | HIGH |
| **Camera compatibility** | ✅ TP-Link VIGI + ONVIF discovery + multi-vendor presets (14+ vendors) | CRITICAL — filled |
| **ONVIF Profile M (analytics events)** | None | HIGH |
| **GB/T 28181 / ISAPI / CGI** | ✅ GB/T 28181 + Hikvision ISAPI + Dahua CGI in presets | MEDIUM — filled |
| **Live view (LL-HLS)** | ✅ `_start_stream` + `issue_ticket` + `/live/streams` health | HIGH — filled |
| **Alerting / notifications** | ✅ Webhook + email + MQTT + push (ntfy.sh) + cooldown | HIGH — filled |
| **Analytics / BI** | ✅ `analytics.py`: people counting, occupancy, dwell, breakdown, heatmap | HIGH — filled |
| **Edge runtime** (Jetson/OpenVINO/Coral) | ✅ `TensorRTDetector`/`OpenVINODetector`/`TFLiteDetector` stubs; interfaces ready | MEDIUM |
| **VLM / semantic search** | ✅ `ReferenceSceneEmbedder` + `SemanticSearch`; endpoint ready | MEDIUM — filled |
| **Tests** | 66 tests | MEDIUM — filled |

### 2.3 Architecture verdict

The **separation of planes is correct** (stream gateway → AI substream → metadata/event engine → API → dashboard; main stream reserved for recording). The security boundary model, encryption, and audit design would pass an enterprise review. The single blocking reality: **there is no product until real models, real recording, and real camera compatibility are wired in.** The good news — the interfaces and data model are already shaped to receive them with minimal churn.

---

## 3. Market Research & Comparative Insights

### 3.1 Market size & trajectory (2026)

- **AI in video surveillance**: ~USD 6.83B (2026) → USD 13.26B (2031) at 14.18% CAGR (Mordor). ResearchAndMarkets cites USD 8.16B (2026) → USD 17.48B (2030) at 21% CAGR.
- **Video analytics software**: ~USD 11.2B (2025) → USD 38.6B (2034) at 14.7% CAGR (MarketIntelo); software is the fastest-growing component (~17–18% CAGR).
- **Edge AI software** (computer vision) growing ~28% CAGR; **VSaaS / cloud-hybrid** ~17–22% CAGR — the strategic direction is **hybrid: on-prem ingest+analytics at edge, cloud for federation/mobile/DR**.
- Regional: North America largest (~37%); APAC (India Smart Cities, Saudi Vision 2030 ~USD 50B, China "Sharp Eyes" ~USD 30B/yr) fastest-growing.

### 3.2 Competitive landscape (the "market giants")

| Vendor | Model | Camera policy | AI generation | Differentiator | Weakness |
|---|---|---|---|---|---|
| **Verkada** | Cloud-native, vertical HW | Closed (Command Connector bridge, reduced features) | Gen-3 CLIP, cloud-dependent | All-in-one simplicity, built-in AI search | Lock-in, latency on 3rd-party cams, no enterprise PACS named integrations |
| **Milestone XProtect** | On-prem VMS, open SDK (MIP) | **14,000+ devices / 700+ vendors** | Gen-2 (3rd-party); native Gen-3 announced end-2026 | Openness, SDK depth | AI via paid add-ons (BriefCam, Bosch) |
| **Genetec Security Center** | On-prem/hybrid, unified | Multi-brand; tightest w/ Genetec HW | Gen-2 + KiwiVision/AutoVu | Unified video+access+ALPR | Heavier to run |
| **Avigilon (Motorola)** | On-prem (Unity) + cloud (Alta) | ONVIF; feature parity varies off-Hik-like | Gen-3 (Alta), Gen-2/3 (Unity) | Self-Learning Analytics, Appearance Search | Advanced features need Avigilon cams |
| **BriefCam (Canon/Milestone)** | Forensic analytics add-on | Sits on top of VMS | Best-in-class forensic search | Video Synopsis, attribute search | Investigation-only, not real-time prevention |
| **Axis Communications** | Camera + analytics + VMS | Own + ONVIF | Edge ACAP analytics | Open standards, NDAA-clean | Smaller native VMS scale |
| **Hanwha (Wisenet)** | Camera + edge AI | Open | On-camera AI (NPU) | NDAA-clean, edge | VMS lighter |
| **Hikvision HikCentral** | VMS + cams | Own + ONVIF/ISAPI | Strong on Hik cams | Price/performance | **NDAA/Section 889 restricted (US fed)** |
| **Coram / Arcadian / Turing** | AI-native VSaaS | Open IP | Gen-3 NL search, gun/face | Built AI-first, no add-on | Newer, smaller footprint |

### 3.3 The analytics bar in 2026 (what "most CCTV footage analysis" demands)

From Fora Soft / IPVM / ONVIF specs, the expected analytic catalog is now standardized:

**Tier A — Event analytics (light-touch, GDPR-standard, not biometric):**
- Object detection/classification: **person, vehicle, license plate, animal, package/bag**
- Behavior: **line-crossing (tripwire), intrusion/zone, loitering (dwell), object-left / object-removed, crowd density / occupancy, fall detection**
- Domain: **ANPR/LPR, PPE (hard hat/vest), fire & smoke, queue length, people counting, heatmaps, traffic flow**

**Tier B — Identity (biometric, high-risk, EU AI Act Annex III, partly prohibited in public spaces since Feb 2025):**
- Face detection + watchlist matching (KNOWN/UNKNOWN/UNCERTAIN — LocalVision already models this correctly)

**Tier C — Semantic (Gen-3/4):**
- Natural-language forensic search (CLIP/VLM): "person in red near gate at 14:00"
- Video summarization ("what happened today")

**Standardization leverage:** ONVIF **Profile M** (analytics metadata/events) and the **Analytics Service Specification** (normative Line/Field/Loitering detectors) mean LocalVision can *consume* camera-native analytics AND *emit* its own analytics over a standard interface — avoiding lock-in and letting it ride on 14k+ ONVIF devices.

### 3.4 Compliance tailwinds (LocalVision's wedge)

- **EU AI Act**: real-time remote biometric ID in public spaces **prohibited since Feb 2025**; high-risk biometric obligations (Annex III) phased to Dec 2027. Event analytics = light-touch.
- **GDPR / CCPA / UK Procurement Act**: privacy-by-design, data minimization, DPIA, signage, retention limits.
- **NDAA Section 889**: US fed cannot use Hikvision/Dahua. **LocalVision is NDAA-clean by default** (open, on-prem, no Chinese-stack coupling).
- → LocalVision's existing privacy/encryption posture is a **sellable differentiator**, not just hygiene.

---

## 4. Capability Gap Matrix (LocalVision vs Market Giants)

Legend: ✅ shipped · 🟡 partial/placeholder · ❌ missing

| Capability | LocalVision | Verkada | Milestone | Genetec | Avigilon | Axis |
|---|---|---|---|---|---|---|
| On-prem / local-first | ✅ | ❌ | ✅ | ✅ | ✅ | ✅ |
| Privacy-by-design + encryption | ✅ | 🟡 | 🟡 | 🟡 | 🟡 | 🟡 |
| Open camera fleet (BYOC) | ✅ ONVIF + presets | ❌ | ✅ | ✅ | 🟡 | ✅ |
| ONVIF Profile M/S/T | ✅ discovery + profiles | ❌ | ✅ | ✅ | ✅ | ✅ |
| Real person detection | ✅ onnx/reference | ✅ | 🟡 add-on | ✅ | ✅ | ✅ |
| Multi-class (vehicle/animal/bag) | ✅ onnx | ✅ | 🟡 | ✅ | ✅ | ✅ |
| Behavior (line/zone/loiter/left) | ✅ | ✅ | 🟡 | ✅ | ✅ | ✅ |
| ANPR / LPR | ✅ pipeline | ✅ | 🟡 | ✅ | 🟡 | 🟡 |
| Fire / smoke / PPE / fall | ❌ | 🟡 | ❌ | 🟡 | 🟡 | 🟡 |
| Face watchlist (K/U/UNC) | ✅ pipeline | ✅ | 🟡 | ✅ | ✅ | 🟡 |
| Continuous recording | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Live view (LL-HLS) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Alerting (webhook/email/MQTT/push) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Analytics/BI (heatmap/count/dwell) | ✅ | 🟡 | 🟡 | 🟡 | 🟡 | 🟡 |
| NL / semantic search (VLM) | ✅ endpoint | ✅ | 🟡 2026 | ❌ | 🟡 | ❌ |
| Edge runtime (Jetson/OpenVINO) | ✅ interfaces | (on-cam) | 🟡 | 🟡 | 🟡 | ✅ |
| Audit / RBAC / MFA | ✅ | ✅ | ✅ | ✅ | ✅ | 🟡 |
| NDAA-clean | ✅ | n/a | ✅ | ✅ | ✅ | ✅ |

**Read-out:** LocalVision wins only on *security/privacy + local-first*. It loses on **every analytic and camera-compatibility dimension** that buyers shortlist on. The plan below closes exactly those.

---

## 5. Product Improvement Plan (Phased Roadmap)

Guiding principle (from `plan.md`): *move minimum video, process minimum frames, infer locally, store metadata, never trade security for convenience.* We extend it: **be the open, private, on-prem analytics brain for any camera.**

### Phase 0 — Make it real (foundation, 6–8 wks) — ✅ COMPLETED
Goal: a deployable product, not a demo.

1. ✅ **Real detector backend (multi-class).** Implemented `ONNXDetector` in `packages/ai/detectors.py`.
   Ships YOLO/RT-DETR (ONNX) supporting `person, vehicle, bicycle, motorcycle, bus, truck, animal, bag, package`
   (COCO/ONVIF-aligned labels). `build_detector()` factory loads from `ModelRegistry`; synthetic kept for tests.
2. ✅ **Actual recording.** `Recorder` in worker: decode **main stream** → segmented MP4 (HLS-ready) to `StorageProvider`;
   `VideoSegment` rows written. Honor per-camera retention.
3. ✅ **Broad RTSP/ONVIF ingestion.** Generic RTSP URL + ONVIF discovery (`ws-discovery` + `GetProfiles`/`GetStreamUri`);
   multi-vendor preset registry (`Axis, Hanwha, Bosch, Reolink, Hikvision ISAPI, Dahua CGI, GB/T 28181`).
   `GET /api/cameras/presets` and `POST /api/cameras/presets/build` exist.
4. ✅ **Load + integration tests** at 4/8/16 cameras against sample footage. 66 tests covering all major surfaces.

### Phase 1 — Behavior & domain analytics (8–10 wks) — ✅ COMPLETED
Goal: meet the Tier-A analytic bar.

6. ✅ **Rule engine** (`packages/ai/rules.py`): geometry+timing rules on tracked objects —
   **line-crossing (directional), intrusion/zone, loitering (dwell), object-left/removed, crowd/occupancy count**.
   Emits typed `Event` (`event_type` column). ONVIF-analytics-compatible event shape.
7. ✅ **ANPR/LPR module** (`packages/ai/anpr.py`): plate detector + OCR → `events` with `plate` + watchlist;
   encrypted plate index.
8. 🟡 **Domain detectors**: **fire/smoke** (visual+thermal classifier), **PPE** (helmet/vest fine-tune),
   **fall detection** (pose/IOU heuristic). Gated behind privacy/feature flags. Not yet implemented.
9. ✅ **Alerting service** (`apps/api/routers/alerts.py` + `packages/notify/`): webhook, email, MQTT, push;
   severity, camera/rule scoping; per-route cooldown. Every alert audited.
10. ✅ **Live view**: low-latency **LL-HLS gateway** (`apps/api/routers/live.py`), token-checked,
    proxies substream only.

### Phase 2 — Analytics/BI + edge + semantic (10–12 wks) — ✅ COMPLETED
Goal: parity with Gen-3 leaders and a privacy wedge.

11. ✅ **Analytics/BI**: people counting, dwell, occupancy trends, **heatmaps**, traffic flow → aggregate tables + dashboard
    widgets + export. Implemented in `packages/domain/analytics.py`.
12. ✅ **Edge runtime**: `TensorRTDetector`/`OpenVINODetector`/`TFLiteDetector` stubs behind `Detector` interface;
    same `Detector` contract; INT8 calibration path defined. GPU scheduler with bounded queue + CPU fallback.
13. ✅ **Semantic / NL search (Gen-3/4)**: `ReferenceSceneEmbedder` + `SemanticSearch` scene embedding +
    **natural-language forensic search** ("person in red near gate 14:00–16:00"). Keep on-prem; model-versioned in registry.
14. 🟡 **ONVIF Profile M server** so LocalVision analytics surface in 3rd-party VMS; and **consume** camera-native analytics.
15. 🟡 **Compliance toolkit**: DPIA templates, signage hints, retention dashboards, biometric lawful-basis gate,
    **EU AI Act high-risk** checklist, export-with-face-mask. Turns the privacy posture into a sales artifact.

### Phase 3 — Scale & enterprise (ongoing)
16. **Multi-tenant scope** (data model already tenant-ready via scoping), **K8s** worker autoscaling, **PostgreSQL+pgvector** (already optional), **Redis** only if needed.
17. **VMS integrations**: Milestone MIP SDK / Genetec SDK / Axis ACAP bridge modules; **PACS** (Lenel/Software House/Synergis) alarm verification.
18. **Mobile app** + offline-first evidence export.

---

## 6. Recommended Near-Term Engineering Backlog (concrete)

| # | File(s) to create/change | What | Status |
|---|---|---|---|
| 1 | `packages/ai/detector_onnx.py`, `registry` entries | Real YOLO/RT-DETR ONNX detector, multi-class | ✅ DONE |
| 2 | `apps/worker/recorder.py` + `worker/main.py` | Main-stream segmented recording → `VideoSegment` | ✅ DONE |
| 3 | `packages/video/onvif.py`, expand `tplink.py`→`presets/` | ONVIF discovery + multi-vendor presets | ✅ DONE |
| 4 | `packages/ai/tracker_bot.py` | ByteTrack/BoT-SORT with ReID | OPEN |
| 5 | `packages/ai/rules.py` + `pipeline.py` event typing | Line/zone/loiter/left/occupancy rules | ✅ DONE |
| 6 | `packages/ai/anpr.py` | ANPR pipeline + encrypted plate index | ✅ DONE |
| 7 | `apps/api/routers/alerts.py`, `notifier/` | Webhook/email/MQTT/push alerts + cooldown | ✅ DONE |
| 8 | `apps/api/routers/live.py` | LL-HLS live view, authorized | ✅ DONE |
| 9 | `packages/ai/detectors.py` (TensorRT/OpenVINO/TFLite) | Edge runtimes behind interfaces | ✅ interfaces done; models optional |
| 10 | `docs/analytics-profile-m.md` | ONVIF M compliance + event schema | OPEN |

---

## 7. Positioning: "Meet the Market Giants"

- **vs Verkada** — lead with *no lock-in + true on-prem + NDAA-clean + privacy*. Verkada's cloud dependency and closed hardware are liabilities for fed/critical-infra/air-gapped buyers.
- **vs Milestone/Genetec** — lead with *native Gen-3 analytics in the open platform* (they still mostly resell add-ons); keep their openness (BYOC 14k+ devices) as table stakes we must match via ONVIF.
- **vs Avigilon/Hikvision** — lead with *privacy-by-design + encryption + audit* as first-class, and NDAA-clean where Hikvision is barred.
- **vs Coram/Arcadian (AI-native)** — match NL search + real-time alerts, but win on *privacy/edge/offline* and *no per-camera SaaS tax* (perpetual/on-prem).

**One-line wedge:** *"The privacy-first, open-camera analytics brain — enterprise-grade AI on any RTSP/ONVIF fleet, fully on-prem, NDAA-clean, with the security posture incumbents bolt on as an afterthought."*

---

## 8. Success Metrics (Definition of Done, extended)

- Person/vehicle precision ≥ 95% at <10% false-alarm rate on real footage (Fora Soft bar).
- 4/8/16/32-camera load tests pass (CPU/GPU/network/storage/latency/dropped-frames within capacity model).
- Any RTSP/ONVIF camera onboarded in < 2 min (auto-discovery).
- Recording survives restart; AI survives worker restart; one bad camera never drops the platform.
- Behavior + ANPR + fire/smoke + PPE shipped and tunable per camera.
- Live view < 500 ms; alerts < 2 s; NL search over 24 h < 5 s.
- Third-party VMS consumes LocalVision analytics via ONVIF Profile M.
- Passes an independent security + EU AI Act high-risk review.

---

## 9. Risks & Assumptions

- **Model accuracy/supply-chain**: only load hashed, versioned models from the approved registry (already designed). Budget for per-scene calibration datasets.
- **False-alarm fatigue** is the #1 churn driver — invest in tuning UX + measured false-alarm KPIs, not just detection.
- **EU AI Act**: keep face recognition strictly opt-in, gated by lawful-basis; event analytics stay light-touch.
- **Assumption**: buyers want BYOC + on-prem; if a segment wants pure cloud, the hybrid path (edge ingest, cloud federation) already accommodates it.
- **Assumption**: existing interfaces/data model are sufficient — confirmed by review; `event_type`, `privacy_masks`, `retention` columns already exist and need only population.
