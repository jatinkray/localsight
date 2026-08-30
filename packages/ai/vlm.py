"""Gen-3/4 semantic (VLM) search interface.

Market leaders (Verkada, Coram) differentiate on natural-language forensic search:
"person in red near the gate between 14:00 and 16:00". We model this as a pluggable
SceneEmbedder that turns an event's context (optionally a snapshot crop) into a
fixed-dimension vector, plus a SemanticSearch index over events. Matching is done
by cosine similarity against a text embedding of the query.

The ReferenceSceneEmbedder is deterministic (hash-based, like the reference face
embedder) so the search path is exercisable end-to-end without a staged VLM. A real
CLIP/VLM backend is dropped in behind the same interface (lazy import).
"""
from __future__ import annotations

import hashlib
import math
from typing import List, Tuple

from packages.ai.matcher import cosine


class SceneEmbedder:
    model_version: str = "ref-vlm-v0"
    dimension: int = 128

    def embed_text(self, query: str) -> List[float]:  # pragma: no cover - interface
        raise NotImplementedError

    def embed_image(self, crop) -> List[float]:  # pragma: no cover - interface
        raise NotImplementedError


class ReferenceSceneEmbedder(SceneEmbedder):
    """Deterministic text/image embedder for tests and offline operation."""

    model_version = "ref-vlm-v0"
    dimension = 128

    def embed_text(self, query: str) -> List[float]:
        return self._vec(query.encode("utf-8"))

    def embed_image(self, crop) -> List[float]:
        if isinstance(crop, (bytes, bytearray)):
            seed = crop
        else:
            seed = str(crop).encode("utf-8")
        return self._vec(seed)

    @staticmethod
    def _vec(seed: bytes) -> List[float]:
        vec: List[float] = []
        buf = seed
        while len(vec) < ReferenceSceneEmbedder.dimension:
            buf = hashlib.sha256(buf).digest()
            vec.extend(b / 255.0 - 0.5 for b in buf)
        vec = vec[: ReferenceSceneEmbedder.dimension]
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


class SemanticSearch:
    """In-memory cosine index over (event_id, embedding) pairs.

    Production would back this with pgvector + the same SceneEmbedder; the API is
    intentionally identical so the storage layer is a drop-in swap.
    """

    def __init__(self, embedder: SceneEmbedder) -> None:
        self.embedder = embedder
        self._items: List[Tuple[str, List[float]]] = []

    def index(self, event_id: str, text: str) -> None:
        self._items.append((event_id, self.embedder.embed_text(text)))

    def search(self, query: str, top_k: int = 10) -> List[Tuple[str, float]]:
        q = self.embedder.embed_text(query)
        scored = [(eid, cosine(q, emb)) for eid, emb in self._items]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]
