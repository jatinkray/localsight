"""Local model registry with integrity verification.

AI models are treated as application dependencies: every model carries a
name, version, source, license, and a SHA-256 hash. The platform refuses to
load a model whose on-disk hash does not match the registry (defense against
supply-chain / tampering). Models are never fetched from user-supplied URLs;
they are staged by an operator into the approved registry.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass
class ModelRecord:
    name: str
    version: str
    path: str
    hash_sha256: str
    source: str = ""
    license: str = ""


class ModelRegistry:
    def __init__(self, registry_path: str = "models/registry.json") -> None:
        self._path = registry_path
        self._models: Dict[Tuple[str, str], ModelRecord] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self._path):
            return
        with open(self._path) as fh:
            data = json.load(fh)
        for m in data.get("models", []):
            rec = ModelRecord(**m)
            self._models[(rec.name, rec.version)] = rec

    def register(self, rec: ModelRecord) -> None:
        self._models[(rec.name, rec.version)] = rec
        self._save()

    def get(self, name: str, version: str) -> ModelRecord:
        rec = self._models.get((name, version))
        if not rec:
            raise KeyError(f"model {name}@{version} not in approved registry")
        return rec

    def verify(self, name: str, version: str) -> bool:
        rec = self.get(name, version)
        actual = self._sha256(rec.path)
        return actual == rec.hash_sha256

    @staticmethod
    def _sha256(path: str) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        with open(self._path, "w") as fh:
            json.dump(
                {"models": [vars(m) for m in self._models.values()]},
                fh,
                indent=2,
            )
