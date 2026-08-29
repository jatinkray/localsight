#!/usr/bin/env python3
"""Capacity planner.

Estimates per-camera and fleet-wide network bandwidth, storage, and approximate
compute from a small set of inputs. Uses the spec's storage formula:

    Daily storage ≈ bitrate(bits/sec) × 86400 / 8

with explicit overhead for filesystem/DB and a per-event metadata allowance.
Run:

    python scripts/capacity.py --cameras 8 --main-fps 15 --main-bitrate-mbps 4 \\
        --sub-fps 5 --sub-bitrate-mbps 0.4 --ai-fps 5 --retention-days 7
"""
from __future__ import annotations

import argparse
import json


def estimate(
    cameras: int,
    main_fps: int,
    main_bitrate_mbps: float,
    sub_fps: int,
    sub_bitrate_mbps: float,
    ai_fps: int,
    retention_days: int,
    event_retention_days: int = 30,
    events_per_camera_day: int = 200,
) -> dict:
    # Network (per camera):
    #   recording = main stream bitrate
    #   AI = substream bitrate (processed locally; counts as ingress)
    #   live view + export assumed 0 unless explicitly provisioned.
    rec_bps = main_bitrate_mbps * 1_000_000
    ai_bps = sub_bitrate_mbps * 1_000_000
    per_cam_mbps = main_bitrate_mbps + sub_bitrate_mbps

    # Storage: recording at main bitrate (+10% FS overhead).
    daily_bytes_per_cam = int(rec_bps * 86400 / 8 * 1.10)
    # Metadata: ~2 KB per event row (conservative), + snapshots if enabled.
    meta_bytes_per_cam_day = events_per_camera_day * 2048
    total_daily = (daily_bytes_per_cam + meta_bytes_per_cam_day) * cameras
    stored_bytes = daily_bytes_per_cam * cameras * retention_days
    meta_stored = meta_bytes_per_cam_day * cameras * event_retention_days

    # Compute heuristics (order-of-magnitude): each AI inference at `ai_fps`
    # across N cameras. We express a relative GPU "frame-load" rather than a hard
    # GPU model claim, since that depends on the chosen model/benchmark.
    ai_frame_load = ai_fps * cameras  # frames/sec to infer across the fleet

    gb = 1024**3
    return {
        "per_camera": {
            "recording_bandwidth_mbps": round(main_bitrate_mbps, 3),
            "ai_bandwidth_mbps": round(sub_bitrate_mbps, 3),
            "total_bandwidth_mbps": round(per_cam_mbps, 3),
            "daily_storage_gb": round(daily_bytes_per_cam / gb, 3),
        },
        "fleet": {
            "cameras": cameras,
            "total_bandwidth_mbps": round(per_cam_mbps * cameras, 2),
            "total_bandwidth_gbps": round(per_cam_mbps * cameras / 1000, 3),
            "daily_storage_gb": round(total_daily / gb, 2),
            "retained_video_gb": round(stored_bytes / gb, 2),
            "retained_metadata_gb": round(meta_stored / gb, 3),
            "ai_frame_load_fps": ai_frame_load,
        },
        "assumptions": {
            "fs_overhead_pct": 10,
            "events_per_camera_day": events_per_camera_day,
            "event_retention_days": event_retention_days,
            "note": "GPU/RAM needs depend on the actual detector model; benchmark before sizing.",
        },
    }


def main() -> int:
    p = argparse.ArgumentParser(description="LocalVision capacity planner")
    p.add_argument("--cameras", type=int, default=4)
    p.add_argument("--main-fps", type=int, default=15)
    p.add_argument("--main-bitrate-mbps", type=float, default=4.0)
    p.add_argument("--sub-fps", type=int, default=5)
    p.add_argument("--sub-bitrate-mbps", type=float, default=0.4)
    p.add_argument("--ai-fps", type=int, default=5)
    p.add_argument("--retention-days", type=int, default=7)
    p.add_argument("--event-retention-days", type=int, default=30)
    p.add_argument("--events-per-camera-day", type=int, default=200)
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    out = estimate(
        args.cameras, args.main_fps, args.main_bitrate_mbps, args.sub_fps,
        args.sub_bitrate_mbps, args.ai_fps, args.retention_days,
        args.event_retention_days, args.events_per_camera_day,
    )
    if args.json:
        print(json.dumps(out, indent=2))
        return 0

    f = out["fleet"]
    print("LocalVision capacity estimate")
    print("=" * 40)
    print(f"Cameras                : {f['cameras']}")
    print(f"Total bandwidth        : {f['total_bandwidth_mbps']} Mbps ({f['total_bandwidth_gbps']} Gbps)")
    print(f"Daily storage          : {f['daily_storage_gb']} GB")
    print(f"Retained video (policy): {f['retained_video_gb']} GB")
    print(f"AI frame-load          : {f['ai_frame_load_fps']} fps (across fleet)")
    print(f"Per-camera bandwidth   : {out['per_camera']['total_bandwidth_mbps']} Mbps")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
