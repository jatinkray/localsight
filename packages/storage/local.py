"""Local-filesystem storage with strict path-traversal protection and signed,
expiring download URLs (no permanently public links).

Keys are treated as opaque, slash-separated identifiers. Every key is validated
so it can never escape `root`, defending against `../../etc/passwd` style attacks
even if a key originates from user input.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
import urllib.parse

from packages.storage.base import StorageProvider


class LocalFilesystemStorage(StorageProvider):
    def __init__(self, root: str, signing_secret: str) -> None:
        self._root = os.path.abspath(root)
        os.makedirs(self._root, exist_ok=True)
        self._secret = signing_secret.encode()

    # ── safety ────────────────────────────────────────────────────────────
    def _resolve(self, key: str) -> str:
        if not key or key.startswith("/") or ".." in key.split("/"):
            raise ValueError("invalid storage key")
        # We avoid os.path.join's absolute-path override and normalize manually.
        rel = os.path.normpath(key)
        if rel != key or rel.startswith(".."):
            raise ValueError("invalid storage key (path traversal)")
        full = os.path.abspath(os.path.join(self._root, rel))
        if not full.startswith(self._root + os.sep) and full != self._root:
            raise ValueError("key escapes storage root")
        return full

    # ── object ops ────────────────────────────────────────────────────────
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        path = self._resolve(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + f".tmp.{os.getpid()}"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)  # atomic

    def get(self, key: str) -> bytes:
        with open(self._resolve(key), "rb") as fh:
            return fh.read()

    def delete(self, key: str) -> None:
        try:
            os.remove(self._resolve(key))
        except FileNotFoundError:
            pass

    def exists(self, key: str) -> bool:
        try:
            return os.path.isfile(self._resolve(key))
        except ValueError:
            return False

    # ── signed URLs ───────────────────────────────────────────────────────
    def _sig(self, key: str, exp: int) -> str:
        mac = hmac.new(self._secret, f"{key}:{exp}".encode(), hashlib.sha256)
        return mac.hexdigest()

    def sign_get_url(self, key: str, expires_sec: int = 300) -> str:
        exp = int(time.time()) + expires_sec
        sig = self._sig(key, exp)
        return f"/api/video/{urllib.parse.quote(key, safe='')}?exp={exp}&sig={sig}"

    def verify_signed_url(self, key: str, exp: str, sig: str) -> bool:
        try:
            exp_i = int(exp)
        except ValueError:
            return False
        if exp_i < int(time.time()):
            return False
        expected = self._sig(key, exp_i)
        return hmac.compare_digest(expected, sig or "")
