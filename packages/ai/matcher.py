"""Vector similarity matcher with KNOWN / UNCERTAIN / UNKNOWN classification.

Embeddings are compared only against embeddings of the *same model version*;
mixing incompatible models is forbidden. The best cosine score drives the
classification band (see packages.domain.events.classify_identity).
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from packages.ai.interfaces import IdentityMatcher, Recognition
from packages.domain.events import classify_identity


def cosine(a: List[float], b: List[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class VectorMatcher(IdentityMatcher):
    def __init__(self, threshold: float = 0.85) -> None:
        self._threshold = threshold

    def search(
        self,
        vector: List[float],
        model_version: str,
        enrolled: List[Tuple[str, List[float], str]],
    ) -> Recognition:
        """`enrolled` is a list of (person_id, embedding, model_version).

        Only same-version embeddings are compared; mismatches are ignored.
        """
        best_id: Optional[str] = None
        best_score = -1.0
        for pid, emb, ver in enrolled:
            if ver != model_version:
                continue
            score = cosine(vector, emb)
            if score > best_score:
                best_score, best_id = score, pid
        status = classify_identity(best_score if best_id else None, self._threshold)
        return Recognition(person_id=best_id, similarity=best_score if best_id else None, status=status)
