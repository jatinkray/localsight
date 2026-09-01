"""Storage provider interface."""
from __future__ import annotations

from abc import ABC, abstractmethod


class StorageProvider(ABC):
    @abstractmethod
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        ...

    @abstractmethod
    def put_stream(self, key: str, source_path: str, content_type: str = "application/octet-stream") -> int:
        """Move a file on disk into storage without buffering it in memory.

        Returns the number of bytes stored. Implementations must consume
        `source_path` (they may move or delete it) and never read the whole
        payload into the process heap — this is the path used by the segment
        recorder for multi-hundred-MB MP4s.
        """
        ...

    @abstractmethod
    def get(self, key: str) -> bytes:
        ...

    @abstractmethod
    def delete(self, key: str) -> None:
        ...

    @abstractmethod
    def exists(self, key: str) -> bool:
        ...

    @abstractmethod
    def sign_get_url(self, key: str, expires_sec: int = 300) -> str:
        """Return a time-limited, non-guessable URL for downloading `key`.

        Implementations must never produce a permanently public URL, and must
        return an *application-relative* path (`/api/video/...`) so the client
        contract is identical across backends; remote objects are streamed
        through the app rather than exposed directly.
        """
        ...

    @abstractmethod
    def verify_signed_url(self, key: str, exp: str, sig: str) -> bool:
        """Validate a `sign_get_url` signature pair (key, exp, sig).

        Implementations share one HMAC scheme (SHA-256 over `key:exp` with the
        deployment signing secret) so the media endpoint authorizes the same
        way regardless of backend.
        """
        ...
