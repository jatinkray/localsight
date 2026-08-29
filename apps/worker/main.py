"""AI/video worker.

Runs the per-camera pipelines. This is a *separate process* from the API; it
shares the database and storage. Each camera gets its own gateway + pipeline in
its own thread, so one camera stalling never takes down the others.

Inbound frames come from the lowest-resolution substream by default (bandwidth/
GPU efficiency). When no stream URL is configured (demo/test), a synthetic source
is used so the pipeline is exercisable without hardware.
"""
from __future__ import annotations

import threading
import time

from packages.ai.detector_ref import SyntheticDetector
from packages.ai.face import ReferenceEmbedder, ReferenceFaceDetector
from packages.ai.interfaces import Detector
from packages.ai.matcher import VectorMatcher
from packages.ai.pipeline import CameraPipeline
from packages.ai.tracker import IouTracker
from packages.domain.models import Camera
from packages.observability.logging import configure_logging, logging
from packages.video.gateway import StreamGateway
from packages.video.sources import FFmpegFrameSource, SyntheticFrameSource

from apps.api.bootstrap import build
from apps.api.config import Settings

log = logging.getLogger("localvision.worker")


def make_detector(settings: Settings) -> Detector:
    if settings.ai_detector == "reference":
        return SyntheticDetector(fps=settings.ai_inference_fps)
    raise RuntimeError(f"unknown AI_DETECTOR={settings.ai_detector!r} (add an ONNX/YOLO backend)")


def build_make_source(camera: Camera, settings: Settings, crypto):
    # Prefer the configured (decrypted) substream; otherwise fall back to synthetic frames.
    sub_url = camera.substream_url_enc
    if sub_url:
        plain = crypto.decrypt_str(sub_url)  # never log the plaintext
        def make():
            return FFmpegFrameSource(plain, width=640, height=360, fps=settings.ai_inference_fps)
        return make
    return lambda: SyntheticFrameSource(fps=settings.ai_inference_fps)


def run_camera(rt, camera: Camera, stop: threading.Event) -> None:
    detector = make_detector(rt.settings)
    tracker = IouTracker(iou_threshold=rt.settings.ai_iou_threshold)
    face_chain = (ReferenceFaceDetector(), ReferenceEmbedder())
    matcher = VectorMatcher(threshold=rt.settings.ai_similarity_threshold)
    pipeline = CameraPipeline(
        camera.id, detector, tracker, face_chain, matcher, rt.SessionLocal, rt.storage, rt.crypto,
        confidence_threshold=rt.settings.ai_confidence_threshold,
        merge_gap_seconds=10.0,
        recognize_interval_sec=rt.settings.ai_recognize_interval_sec,
        model_version=rt.embedder.model_version,
        identity_recognition_enabled=rt.settings.ai_identity_recognition_enabled,
    )

    def make_source():
        return build_make_source(camera, rt.settings, rt.crypto)()

    gateway = StreamGateway(camera.id, make_source, on_status=lambda cid, s: log.info("camera %s -> %s", cid, s))
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
        except Exception:  # noqa: BLE001 - one bad frame must not kill the camera loop
            session.rollback()
            log.exception("pipeline error for camera %s", camera.id)
        finally:
            session.close()
        # Drop frames intelligently: if processing lagged, the generator simply
        # yields the next available frame; we never buffer unbounded.


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
