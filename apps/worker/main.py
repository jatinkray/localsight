"""AI/video worker.

Runs the per-camera pipelines. Each camera gets its own gateway + pipeline in its
own thread, so one camera stalling never takes down the others.

Inbound frames come from the lowest-resolution substream by default (bandwidth/
GPU efficiency). The *main* stream is recorded by a per-camera Recorder into
segmented, seekable clips. Behavior-analytics rules and ANPR (if configured) are
evaluated per frame and turned into point-in-time events, which are then fanned
out to the configured alert channels.
"""
from __future__ import annotations

import os
import threading
import time
from typing import List, Optional

from packages.ai.anpr import ANPRPipeline, ReferencePlateDetector, ReferencePlateOCR
from packages.ai.detectors import build_detector
from packages.ai.face import ReferenceEmbedder, ReferenceFaceDetector
from packages.ai.interfaces import Detector
from packages.ai.matcher import VectorMatcher
from packages.ai.pipeline import CameraPipeline
from packages.ai.rules import rule_engine_from_json
from packages.ai.tracker import IouTracker
from packages.domain.models import AlertRoute, Camera, VideoSegment
from packages.notify import Alert, PushNotifier, WebhookNotifier, build_notifier, dispatch
from packages.observability.logging import configure_logging, logging
from packages.security.errors import UnsafeUrlError
from packages.security.ssrf import validate_egress_url
from packages.video.gateway import StreamGateway
from packages.video.recorder import Recorder
from packages.video.sources import FFmpegFrameSource, SyntheticFrameSource

from apps.api.bootstrap import build
from apps.api.config import Settings

log = logging.getLogger("localvision.worker")


# ── alert routing (reads AlertRoute from the DB) ─────────────────────────────
_route_cache: dict = {"at": 0.0, "routes": []}


def _load_routes(rt) -> list:
    """Cache alert routes (plus the env-webhook fallback) for ~30s to avoid a DB
    round-trip on every analytic event."""
    now = time.time()
    if now - _route_cache["at"] < 30 and _route_cache["routes"]:
        return _route_cache["routes"]
    routes: list = []
    try:
        with rt.SessionLocal() as s:
            for r in s.query(AlertRoute).filter_by(enabled=True).all():
                cfg = r.config_enc and rt.crypto.decrypt_json(r.config_enc) or {}
                routes.append((r.channel, cfg, r.rule_type, r.camera_id))
    except Exception as exc:  # noqa: BLE001 - degraded: no routes this round
        log.warning("failed to load alert routes: %s", exc)
    env_webhook = os.environ.get("ALERT_WEBHOOK_URL")
    if env_webhook:
        routes.append(("webhook", {"url": env_webhook}, "*", None))
    _route_cache["routes"] = routes
    _route_cache["at"] = now
    return routes


def _build_notifiers(rt, alert: Alert) -> List:
    """Select notifiers for an alert per configured routes; always capture in-process."""
    notifiers: List = [PushNotifier()]
    for channel, cfg, rule_type, camera_id in _load_routes(rt):
        if rule_type not in ("*", alert.rule_type):
            continue
        if camera_id and camera_id != alert.camera_id:
            continue
        if channel == "webhook":
            url = cfg.get("url")
            if not url:
                continue
            try:
                validate_egress_url(url, allowlist=rt.settings.ssrf_allowlist_cidrs)
            except UnsafeUrlError:
                log.warning("skipping unvalidated webhook route for %s", alert.rule_type)
                continue
            notifiers.append(WebhookNotifier(url))
        elif channel == "email":
            try:
                notifiers.append(build_notifier("email", cfg))
            except Exception as exc:  # noqa: BLE001 - bad channel config
                log.warning("skipping email route: %s", exc)
    return notifiers


def make_detector(settings: Settings, registry) -> Detector:
    """Build the configured Detector. Fails closed: a misconfigured or integrity-
    failing staged model backend raises instead of silently degrading to a
    non-functional detector, so operators get a clear health signal rather than a
    false sense of safety."""
    return build_detector(settings, registry)


def build_make_source(camera: Camera, settings: Settings, crypto):
    sub_url = camera.substream_url_enc
    if sub_url:
        plain = crypto.decrypt_str(sub_url)
        def make():
            return FFmpegFrameSource(plain, width=640, height=360, fps=settings.ai_inference_fps)
        return make
    return lambda: SyntheticFrameSource(fps=settings.ai_inference_fps)


def run_camera(rt, camera: Camera, stop: threading.Event) -> None:
    settings = rt.settings
    try:
        detector = make_detector(settings, rt.registry)
    except Exception as exc:
        log.critical("camera %s not started: detector backend failed to load: %s", camera.id, exc)
        return
    tracker = IouTracker(iou_threshold=settings.ai_iou_threshold)
    face_chain = (ReferenceFaceDetector(), ReferenceEmbedder())
    matcher = VectorMatcher(threshold=settings.ai_similarity_threshold)

    rule_engine = None
    if settings.ai_rules_enabled:
        rule_engine = rule_engine_from_json(camera.id, camera.rules)
    anpr = None
    if settings.ai_anpr_enabled:
        anpr = ANPRPipeline(ReferencePlateDetector(), ReferencePlateOCR(), conf_thr=0.6)

    pipeline = CameraPipeline(
        camera.id, detector, tracker, face_chain, matcher, rt.SessionLocal, rt.storage, rt.crypto,
        confidence_threshold=settings.ai_confidence_threshold,
        merge_gap_seconds=10.0,
        recognize_interval_sec=settings.ai_recognize_interval_sec,
        model_version=rt.embedder.model_version,
        identity_recognition_enabled=settings.ai_identity_recognition_enabled,
        rule_engine=rule_engine,
        anpr=anpr,
    )

    # Main-stream recorder (only when a main URL is configured + recording on).
    recorder: Optional[Recorder] = None
    main_url = camera.stream_url_enc
    if settings.record_enabled and main_url:
        plain_main = rt.crypto.decrypt_str(main_url)
        recorder = Recorder(
            camera.id, rt.storage, rt.crypto,
            seg_seconds=settings.record_segment_seconds,
            allowlist=settings.ssrf_allowlist_cidrs,
        )

        def _record_loop() -> None:
            while not stop.is_set():
                try:
                    seg = recorder.record_url(plain_main, dt_now())
                    proc = recorder.last_proc
                    if proc is not None:
                        try:
                            proc.wait()
                        except Exception:  # noqa: BLE001
                            pass
                    done = recorder.finalize_last()
                    if done is not None:
                        try:
                            with rt.SessionLocal() as s:
                                s.add(done)
                                s.commit()
                        except Exception as exc:  # noqa: BLE001 - keep recording
                            log.warning("failed to persist segment for %s: %s", camera.id, exc)
                except Exception as exc:  # noqa: BLE001 - recording must not kill analytics
                    log.warning("recorder error for %s: %s", camera.id, exc)
                    stop.wait(2.0)

        threading.Thread(target=_record_loop, name=f"rec-{camera.id}", daemon=True).start()

    def make_source():
        return build_make_source(camera, settings, rt.crypto)()

    gateway = StreamGateway(camera.id, make_source,
                            on_status=lambda cid, st: log.info("camera %s -> %s", cid, st))
    log.info("starting pipeline for camera %s (%s)", camera.id, camera.name)
    for frame, ts in gateway.iter_frames():
        if stop.is_set():
            break
        session = rt.SessionLocal()
        try:
            events = pipeline.process_frame(session, frame, ts)
            session.commit()
            for ev in events:
                log.info("event %s cam=%s identity=%s", ev.id, ev.camera_id, ev.identity_status)
            # Fan out point-in-time analytic events to alert channels.
            for ae in getattr(pipeline, "_last_analytic", []):
                alert = Alert(rule_id=ae.track_id or ae.event_type, rule_type=ae.event_type,
                              camera_id=ae.camera_id, title=ae.event_type, message=str(ae.detail),
                              detail=ae.detail, ts=ae.timestamp_start.isoformat() if ae.timestamp_start else None)
                try:
                    dispatch(alert, _build_notifiers(rt, alert))
                except Exception:  # noqa: BLE001 - alerting must never crash the loop
                    log.exception("alert dispatch failed for %s", camera.id)
        except Exception:  # noqa: BLE001 - one bad frame must not kill the camera loop
            session.rollback()
            log.exception("pipeline error for camera %s", camera.id)
        finally:
            session.close()

    if recorder is not None:
        recorder.stop_all()


def dt_now():
    import datetime as dt
    return dt.datetime.now(dt.timezone.utc)


def main() -> None:
    settings = Settings()
    configure_logging(settings.log_level)
    rt = build(settings)
    stop = threading.Event()
    threads = []
    with rt.SessionLocal() as session:
        cameras = session.query(Camera).all()
    for camera in cameras:
        t = threading.Thread(target=run_camera, args=(rt, camera, stop), daemon=True)
        t.start()
        threads.append(t)
    log.info("worker running for %d camera(s). Ctrl-C to stop.", len(threads))
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop.set()
        for t in threads:
            t.join(timeout=5)


if __name__ == "__main__":
    main()
