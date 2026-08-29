"""Storage provider interface."""
from __future__ import annotations

from abc import ABC, abstractmethod


class StorageProvider(ABC):
    @abstractmethod
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
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

        Implementations must never produce a permanently public URL."""
        ...
