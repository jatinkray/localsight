"""Boot helper for the UI e2e suite: seeds the throwaway DB, then serves.

Launched by tests/ui/conftest.py as a subprocess (NOT imported by tests —
the app must be built exactly once, inside this process). The env is
provided by the parent; nothing here reads dev state.

Usage: python tests/ui/_boot.py <port>
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from apps.api.bootstrap import build, init_db  # noqa: E402
from apps.api.config import Settings  # noqa: E402
from packages.domain.models import Base  # noqa: E402


def main() -> None:
    port = sys.argv[1]

    settings = Settings()
    rt = build(settings)

    # Fresh throwaway DB (never drop a shared/dev database): the parent
    # guarantees a unique DATABASE_URL per session.
    Base.metadata.drop_all(rt.engine)
    init_db(rt)

    # Seed the demo dataset (cameras, persons, events, tracks, routes) so
    # every UI surface has honest data to render. Reuses the canonical
    # dev seeding — same script operators use, not a divergent copy.
    from scripts.seed_dev_data import seed_demo_data

    seed_demo_data(rt)

    import uvicorn

    uvicorn.run("apps.api.main:app", port=int(port), log_level="warning")


if __name__ == "__main__":
    main()
