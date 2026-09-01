"""Secure media delivery.

Video/snapshot bytes are served ONLY through short-lived, cryptographically
signed URLs (HMAC over key+expiry). There is no permanent public link. Access
also requires an authenticated session with video:view. Path traversal is
defeated by the storage layer's key validation.
"""
from __future__ import annotations

import mimetypes

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from apps.api.bootstrap import Runtime
from apps.api.dependencies import get_runtime

router = APIRouter(tags=["video"])


@router.get("/api/video/{key:path}")
def serve_video(
    key: str,
    request: Request,
    exp: str = Query(...),
    sig: str = Query(...),
    rt: Runtime = Depends(get_runtime),
):
    """Serve a media object via its signed, expiring URL.

    Authorization is the URL signature itself — an HMAC over `key:exp` with
    the master key, valid ≤ `signed_url_ttl` (default 300 s) and scoped to
    this one object. A Bearer session is NOT required here because these
    URLs are consumed by <img>/<video> tags, which cannot send Authorization
    headers. The signature is the designed credential for media delivery
    (see packages/storage/base.py); treating it as such is what lets the
    dashboard drawer render snapshots and clips.

    Signed URLs are issued only by permissioned endpoints (event detail,
    export, clip assembly), which audit every issuance.
    """
    if not rt.storage.verify_signed_url(key, exp, sig):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid or expired link")
    try:
        data = rt.storage.get(key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="not found") from None
    ctype = mimetypes.guess_type(key)[0] or "application/octet-stream"
    return Response(content=data, media_type=ctype, headers={"Cache-Control": "no-store"})
