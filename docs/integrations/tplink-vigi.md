# TP-Link integration (VIGI & Tapo)

LocalVision is RTSP/ONVIF-native, which is exactly what TP-Link's surveillance gear
speaks. This document confirms compatibility and shows the native setup for TP-Link.

## Verdict

| Device | Protocols | Works with LocalVision | Native fit |
|--------|----------|------------------------|------------|
| **VIGI camera** (direct) | RTSP `stream1`/`stream2`, ONVIF S/G/T | Yes — no code change | Full |
| **VIGI NVR** | Per-channel RTSP `live/ch/<N>/stream/<1|2>`, ONVIF | Yes — `POST /api/cameras/from-nvr` | Full (1 call provisions all channels) |
| **Tapo (wired)** | RTSP `stream1`/`stream2`, ONVIF S | Yes (creds in URL) | Full for wired models |
| **Tapo (battery/solar)** | — | No | Unsupported by TP-Link itself |

LocalVision maps onto TP-Link 1:1: the **substream** (`stream2` / channel sub) is
used for AI (bandwidth-efficient), the **main stream** (`stream1`) for recording.

## Confirmed conventions (TP-Link official docs)

### VIGI camera (direct attach)
- Main: `rtsp://<ip>:554/stream1` · Sub: `rtsp://<ip>:554/stream2`
- ONVIF port **80** (newer firmware) or **2020** (legacy); RTSP port **554**
- Auth: HTTP **digest** (MD5 or SHA-256) — the camera's own login
- Enable ONVIF at *Settings → Network → Advanced → ONVIF*

### VIGI NVR (per-channel)
- Live: `rtsp://<nvr>:554/live/ch/<N>/stream/1` (main) and `/stream/2` (sub)
- Channel **0** = channel-zero overview (main only); channels **1..N** are cameras
- ONVIF port 80/2020; digest auth
- This is the most "native" path: add the NVR once, LocalVision ingests every channel.

### Tapo (wired only)
- Main: `rtsp://<user>:<pass>@<ip>:554/stream1` · Sub: `.../stream2`
- ONVIF port **2020**; credentials are a **Camera Account** created in the Tapo app
  (separate from the Tapo login, 6–32 chars)
- Battery/solar models (C425, C460, C660, C645D, D230, …) do **not** support RTSP
- Dual-lens models: telephoto lens is `stream6`/`stream7`

## Security notes (important)
- TP-Link states RTSP/ONVIF are **not secure** and must not be exposed publicly.
  Put cameras/NVR on an isolated VLAN; reach them only via VPN/port-forward if remote.
- LocalVision's **SSRF egress guard blocks private/loopback/metadata by default** —
  allow-list your camera VLAN in `SSRF_ALLOWLIST` (e.g. `192.168.0.0/16`) or camera
  adds will be rejected. This is by design.
- Stream URLs and NVR credentials are **encrypted at rest** and never returned to clients.
- Biometric recognition stays **off by default**; enable only with lawful basis.

## Quick start with a VIGI NVR (one call)

```bash
# 1) List the built-in vendor presets
curl -s http://localhost:8000/api/cameras/presets \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# 2) Provision the NVR + all 8 channels (adjust nvr_ip to your device)
curl -s -X POST http://localhost:8000/api/cameras/from-nvr \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"nvr_ip":"192.168.1.50","nvr_name":"VIGI NVR","channel_count":8,"retention_days":7}'
# => {"nvr_id": "...", "cameras": [ {id, name:"VIGI NVR ch1", main_stream, sub_stream}, ... ]}
```

## Adding a single VIGI camera directly

```bash
curl -s -X POST http://localhost:8000/api/cameras \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"Lobby","stream_url":"rtsp://192.168.1.20:554/stream1",
       "substream_url":"rtsp://192.168.1.20:554/stream2"}'
```

## Adding a wired Tapo camera

```bash
curl -s -X POST http://localhost:8000/api/cameras \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"Garage","stream_url":"rtsp://tapoUser:StrongPass@192.168.1.30:554/stream1",
       "substream_url":"rtsp://tapoUser:StrongPass@192.168.1.30:554/stream2"}'
```

## Gotchas
- **Digest SHA-256 incompatibility**: some third-party ONVIF clients fail against
  VIGI's SHA-256 digest. FFmpeg/LocalVision handle digest fine; if ONVIF discovery
  misbehaves, set the camera's *Digest Authentication Algorithm* to MD5/SHA256.
- **ONVIF port after firmware upgrade**: older port 2020 stays open; both 80 and
  2020 are accepted by LocalVision's presets.
- **Tapo stream limits**: a camera can't do cloud + microSD + NVR simultaneously;
  remove the microSD to keep NVR/RTSP recording.
- **No permanent public exposure** — LocalVision issues signed, expiring media URLs.

## Beyond TP-Link — ONVIF & multi-vendor

LocalVision is not limited to TP-Link. Any RTSP/ONVIF camera works:

- **ONVIF discovery** — `POST /api/cameras/onvif/discover` finds devices on the LAN
  (WS-Discovery). `POST /api/cameras/onvif/streams` returns RTSP URIs for a device
  profile. Both are SSRF-validated and audited.
- **Vendor presets** — `GET /api/cameras/presets` lists URL templates for
  `axis`, `hanwha`, `hikvision` (ISAPI), `dahua` (CGI), `reolink`, `bosch`, `onvif`,
  and `gbt28181`. `POST /api/cameras/presets/build` constructs a `rtsp://` URL for a
  vendor (credentials are never echoed back in the response — they are encrypted at
  rest when the camera is created).

```bash
# List vendor presets
curl -s http://localhost:8000/api/cameras/presets -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# Discover ONVIF devices
curl -s -X POST http://localhost:8000/api/cameras/onvif/discover -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"timeout":2.0}'

# Fetch streams for one device (xaddr must be on the SSRF allowlist)
curl -s -X POST http://localhost:8000/api/cameras/onvif/streams -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"xaddr":"http://192.168.1.5/onvif"}'
```

For non-TP-Link cameras, add them via `POST /api/cameras` with the `stream_url` /
`substream_url` from the preset (or ONVIF discovery), exactly as shown above for a
single VIGI camera.
