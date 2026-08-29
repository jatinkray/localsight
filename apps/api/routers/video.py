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
from apps.api.dependencies import get_current_user, get_runtime, require_permission
from packages.domain.models import User

router = APIRouter(tags=["video"])


@router.get("/api/video/{key:path}", dependencies=[Depends(require_permission("video:view"))])
def serve_video(
    key: str,
    request: Request,
    exp: str = Query(...),
    sig: str = Query(...),
    rt: Runtime = Depends(get_runtime),
):
    if not rt.storage.verify_signed_url(key, exp, sig):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid or expired link")
    try:
        data = rt.storage.get(key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="not found")
    ctype = mimetypes.guess_type(key)[0] or "application/octet-stream"
    return Response(content=data, media_type=ctype, headers={"Cache-Control": "no-store"})
