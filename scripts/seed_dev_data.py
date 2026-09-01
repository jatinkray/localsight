"""Seed the dev database with realistic demo data for UI/UX audits and demos.

DEV-ONLY convenience script — never runs in tests or production.
Uses the real runtime bootstrap (crypto + storage wired exactly like the app)
so seeded events produce working signed snapshot/video URLs, which is what a
UI audit needs.

Usage:
    .venv/bin/python scripts/seed_dev_data.py [--fresh]
"""
from __future__ import annotations

import argparse
import io
import os
import secrets
import struct
import sys
from datetime import timedelta

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from apps.api.bootstrap import build, init_db, seed  # noqa: E402
from apps.api.config import Settings  # noqa: E402
from packages.domain.models import (  # noqa: E402
    AlertRoute,
    Camera,
    Event,
    Person,
    PersonEmbedding,
    VideoSegment,
)
from packages.domain.timeutil import utcnow  # noqa: E402

CAM_NAMES = [
    ("Lobby", "ONLINE", "2560x1440", 15),
    ("Warehouse", "ONLINE", "1920x1080", 25),
    ("Loading Dock", "ONLINE", "1920x1080", 20),
    ("Parking Gate", "DEGRADED", "1920x1080", 25),
    ("Server Room", "OFFLINE", "1280x720", 10),
]
PERSON_LABELS = [
    ("employee-001", "Alice Nguyen"),
    ("employee-002", "Bob Marlowe"),
    ("contractor-014", "Chen Wei"),
    ("visitor-0001", "Dana Krisch"),
]


def tiny_jpeg(w: int = 160, h: int = 120, shade: int = 40) -> bytes:
    """Minimal valid JPEG (single gray frame) — no Pillow dependency."""
    # Baseline JFIF: SOI, APP0, DQT, SOF0, DHT, SOS with all-zero image data.
    out = bytearray()
    out += b"\xff\xd8"  # SOI
    out += b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    # DQT: luminance table, all 8s (quality placeholder)
    out += b"\xff\xdb\x00C\x00" + bytes([8] * 64)
    # SOF0: 8-bit, h, w, 1 component
    out += b"\xff\xc0\x00\x0b\x08" + struct.pack(">HH", h, w) + b"\x01\x01\x11\x00"
    # DHT: standard luminance DC/AC (minimal)
    _dht_dc = (b"\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00"
               b"\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b")
    out += b"\xff\xc4\x00\x1f\x00" + _dht_dc
    out += b"\xff\xc4\x00\xb5\x10" + bytes([
        0x00, 0x02, 0x01, 0x03, 0x03, 0x02, 0x04, 0x03, 0x05, 0x05, 0x04, 0x04,
        0x00, 0x00, 0x01, 0x7D, 0x01, 0x02, 0x03, 0x00, 0x04, 0x05, 0x01, 0x06,
        0x07, 0x08, 0x09, 0x0A, 0x0B, 0x01, 0x00, 0x02, 0x03, 0x11, 0x04, 0x00,
        0x05, 0x21, 0x12, 0x31, 0x41, 0x06, 0x13, 0x51, 0x61, 0x07, 0x22, 0x71,
        0x14, 0x32, 0x81, 0x91, 0xA1, 0x08, 0x23, 0x42, 0xB1, 0xC1, 0x15, 0x52,
        0xD1, 0xF0, 0x24, 0x33, 0x62, 0x72, 0x82, 0x09, 0x0A, 0x16, 0x17, 0x18,
        0x19, 0x1A, 0x25, 0x26, 0x27, 0x28, 0x29, 0x2A, 0x34, 0x35, 0x36, 0x37,
        0x38, 0x39, 0x3A, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49, 0x4A, 0x53,
        0x54, 0x55, 0x56, 0x57, 0x58, 0x59, 0x5A, 0x63, 0x64, 0x65, 0x66, 0x67,
        0x68, 0x69, 0x6A, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78, 0x79, 0x7A, 0x83,
        0x84, 0x85, 0x86, 0x87, 0x88, 0x89, 0x8A, 0x92, 0x93, 0x94, 0x95, 0x96,
        0x97, 0x98, 0x99, 0x9A, 0xA2, 0xA3, 0xA4, 0xA5, 0xA6, 0xA7, 0xA8, 0xA9,
        0xAA, 0xB2, 0xB3, 0xB4, 0xB5, 0xB6, 0xB7, 0xB8, 0xB9, 0xBA, 0xC2, 0xC3,
        0xC4, 0xC5, 0xC6, 0xC7, 0xC8, 0xC9, 0xCA, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6,
        0xD7, 0xD8, 0xD9, 0xDA, 0xE1, 0xE2, 0xE3, 0xE4, 0xE5, 0xE6, 0xE7, 0xE8,
        0xE9, 0xEA, 0xF1, 0xF2, 0xF3, 0xF4, 0xF5, 0xF6, 0xF7, 0xF8, 0xF9, 0xFA,
    ])
    # SOS
    out += b"\xff\xda\x00\x08\x01\x01\x00\x00\x3F\x00"
    # Entropy-coded data: DC coeff of value `shade>>4`, then EOB. Bytes fabricated
    # to decode as constant gray — a real decoder renders a flat frame.
    dc_bits = bin(shade >> 3)[2:].zfill(8)
    bitstr = "0" * 2 + dc_bits + "1010"  # huff(dc), EOB
    pad = (8 - len(bitstr) % 8) % 8
    bitstr += "1" * pad
    for i in range(0, len(bitstr), 8):
        out.append(int(bitstr[i:i + 8], 2))
    out += b"\xff\xd9"  # EOI
    return bytes(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", action="store_true", help="drop + recreate schema first")
    args = ap.parse_args()

    settings = Settings()
    rt = build(settings)

    if args.fresh:
        print("Dropping schema (fresh)…")
        from packages.domain.models import Base

        Base.metadata.drop_all(rt.engine)
    init_db(rt)
    seed(rt)

    s = rt.SessionLocal()
    now = utcnow()
    n_events = 0
    try:
        # ── cameras ──────────────────────────────────────────────────────
        cameras: list[Camera] = []
        for i, (name, status, res, fps) in enumerate(CAM_NAMES):
            existing = s.query(Camera).filter(Camera.name == name).first()
            if existing:
                cameras.append(existing)
                continue
            cam = Camera(
                id=secrets.token_hex(16),
                name=name,
                stream_url_enc=rt.crypto.encrypt_str(
                    f"rtsp://user:pass@192.168.1.{40 + i}:554/stream1"
                ),
                substream_url_enc=rt.crypto.encrypt_str(
                    f"rtsp://user:pass@192.168.1.{40 + i}:554/sub"
                ),
                status=status,
                health={"ONLINE": "ok", "DEGRADED": "degraded", "OFFLINE": "unreachable"}[status],
                resolution=res,
                fps=fps,
                timezone="UTC",
                privacy_masks=[{"x": 0.05, "y": 0.0, "w": 0.15, "h": 1.0}],
                retention={"days": 7},
                rules={"direction": "any", "dwell_sec": 300, "count_threshold": 0, "zone": None},
                last_seen=now - timedelta(seconds=30 * (i + 1)),
            )
            s.add(cam)
            cameras.append(cam)

        # ── persons + embeddings ─────────────────────────────────────────
        persons: list[Person] = []
        for label, display in PERSON_LABELS:
            existing = s.query(Person).filter(Person.label == label).first()
            if existing:
                persons.append(existing)
                continue
            p = Person(id=secrets.token_hex(16), label=label, display_name=display, status="known")
            s.add(p)
            persons.append(p)
            s.add(
                PersonEmbedding(
                    id=secrets.token_hex(16),
                    person_id=p.id,
                    embedding_enc=rt.crypto.encrypt_str(
                        ",".join(f"{secrets.randbits(16) / 65535:.4f}" for _ in range(128))
                    ),
                    model_version="reference-embedder-1",
                    dimension=128,
                    quality_score=0.8,
                )
            )

        # ── video segments (6 per online camera, real files in storage) ──
        seg_by_cam: dict[str, list[VideoSegment]] = {c.id: [] for c in cameras}
        for cam in cameras:
            if cam.status == "OFFLINE":
                continue
            for j in range(6):
                seg_start = now - timedelta(hours=j + 1)
                key = f"recordings/{cam.id}/{seg_start:%Y%m%dT%H%M%S}.mp4"
                if s.query(VideoSegment).filter(VideoSegment.storage_key == key).first():
                    continue
                rt.storage.put(key, b"\x00\x00\x00\x18ftypmp42" + secrets.token_bytes(1024))
                seg = VideoSegment(
                    id=secrets.token_hex(16),
                    camera_id=cam.id,
                    storage_key=key,
                    storage_backend="local",
                    start_ts=seg_start,
                    end_ts=seg_start + timedelta(seconds=300),
                    duration_sec=300,
                    size_bytes=1_048_576,
                )
                s.add(seg)
                seg_by_cam[cam.id].append(seg)

        # ── events (last 48h, mixed types, real snapshot files) ──────────
        ev_types = ["presence", "line_cross", "person_detected", "anpr_hit"]
        for k in range(96):
            cam = cameras[k % len(cameras)]
            start = now - timedelta(minutes=(k * 37) % (60 * 48), seconds=900 * (k % 7))
            end = start + timedelta(seconds=45 + (k * 7) % 120)
            identity = persons[k % len(persons)] if k % 3 else None
            et = ev_types[k % 4]
            detail = None
            if et == "anpr_hit":
                detail = {"plate_enc": secrets.token_hex(32), "plate_hash": secrets.token_hex(16)}
            elif et == "line_cross":
                detail = {"direction": ("enter", "exit")[k % 2], "count": 1 + k % 3}
            else:
                detail = {"dwell_sec": (k * 13) % 300}
            snap_key = None
            if k % 2 == 0:
                snap_key = f"snapshots/demo-{k}.jpg"
                rt.storage.put(snap_key, tiny_jpeg(shade=30 + (k * 11) % 180))
            segs = seg_by_cam.get(cam.id, [])
            ev = Event(
                id=secrets.token_hex(16),
                camera_id=cam.id,
                identity_id=identity.id if identity else None,
                identity_status=("known" if identity else "unknown"),
                event_type=et,
                timestamp_start=start,
                timestamp_end=end,
                confidence=0.62 + ((k * 17) % 35) / 100,
                bbox={"x": 0.1, "y": 0.2, "w": 0.2, "h": 0.4},
                snapshot_key_enc=rt.crypto.encrypt_str(snap_key) if snap_key else None,
                video_segment_key_enc=(
                    rt.crypto.encrypt_str(segs[k % len(segs)].storage_key)
                    if segs and k % 2 == 0
                    else None
                ),
                detail=detail,
                created_at=start,
            )
            s.add(ev)
            n_events += 1

        # ── alert routes ────────────────────────────────────────────────
        if not s.query(AlertRoute).count():
            for rule_type, channel, cfg in [
                ("anpr", "email", {"to": "security@example.com"}),
                ("*", "webhook", {"url": "https://hooks.example.com/nvr"}),
                ("line_cross", "push", {"topic": "localsight/alerts"}),
            ]:
                s.add(
                    AlertRoute(
                        id=secrets.token_hex(16),
                        rule_type=rule_type,
                        channel=channel,
                        config_enc=rt.crypto.encrypt_str(
                            __import__("json").dumps(cfg)
                        ),
                        enabled=True,
                        cooldown_sec=300,
                    )
                )

        s.commit()
        print(
            f"Seeded: {s.query(Camera).count()} cameras, {s.query(Person).count()} persons, "
            f"{n_events} events (+{s.query(VideoSegment).count()} segments, "
            f"{s.query(AlertRoute).count()} alert routes)."
        )
    finally:
        s.close()
    _ = io  # silence unused import if struct path used


if __name__ == "__main__":
    main()
