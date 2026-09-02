"""System health, readiness, and metrics.

Separates liveness (process up) from readiness (dependencies reachable). Metrics
are exported in Prometheus text format. GPU/CPU/RAM are best-effort and should be
supplied by the worker in production; here we report what's observable. The
authenticated endpoints require any valid session; /health/* probes are open for
orchestrators.
"""
from __future__ import annotations

import datetime as dt
import os
import secrets

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from apps.api.bootstrap import Runtime
from apps.api.dependencies import get_current_user, get_db, get_runtime
from packages.domain.models import User
from packages.observability.metrics import metrics

router = APIRouter(tags=["system"])

try:
    import psutil
except Exception:  # pragma: no cover
    psutil = None


@router.get("/health/live")
def live():
    return {"status": "alive", "ts": dt.datetime.now(dt.UTC)}


@router.get("/health/ready")
def ready(request: Request, rt: Runtime = Depends(get_runtime)):
    components: dict[str, dict] = {}
    try:
        with rt.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        components["database"] = {"status": "ok"}
    except Exception as exc:
        components["database"] = {"status": "down", "detail": str(exc)[:200]}
    try:
        rt.storage.put("__health__", b"ok")
        rt.storage.delete("__health__")
        components["storage"] = {"status": "ok"}
    except Exception as exc:
        components["storage"] = {"status": "down", "detail": str(exc)[:200]}
    overall = "ready" if all(c["status"] == "ok" for c in components.values()) else "degraded"
    return {"status": overall, "components": components,
            "ts": dt.datetime.now(dt.UTC)}


@router.get("/api/system/health")
def system_health(request: Request, _: User = Depends(get_current_user),
                  rt: Runtime = Depends(get_runtime)):
    try:
        with rt.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "components": {
            "database": {"status": "ok" if db_ok else "down"},
            "ai_model": {"name": rt.settings.ai_detector, "version": rt.embedder.model_version},
        },
        "generated_at": dt.datetime.now(dt.UTC),
    }


def _metrics_auth(request: Request, db: Session = Depends(get_db)) -> None:
    """Scrape-token OR user-session auth for /api/system/metrics.

    A static bearer token (METRICS_SCRAPE_TOKEN) is accepted for Prometheus,
    which has no login flow; anything else must be a valid user session.
    get_current_user MUST be called with a real Session (its second parameter
    is a Depends() marker when invoked outside DI) — calling it bare was the
    cause of the 500 on every user-session metrics request.
    """
    expected = os.environ.get("METRICS_SCRAPE_TOKEN", "")
    auth = request.headers.get("Authorization", "")
    if expected and auth.startswith("Bearer ") and secrets.compare_digest(auth[7:], expected):
        return
    get_current_user(request, db)  # raises 401 itself on failure


@router.get("/api/system/metrics", dependencies=[Depends(_metrics_auth)])
def system_metrics(rt: Runtime = Depends(get_runtime)):
    """Prometheus scrape endpoint (auth handled by _metrics_auth)."""
    return _render_metrics(rt)


def _render_metrics(rt: Runtime) -> Response:
    if psutil:
        metrics.set("cpu_utilization", psutil.cpu_percent())
        metrics.set("ram_used_mb", psutil.virtual_memory().used / (1024 * 1024))
        root = os.path.abspath(rt.settings.storage_local_root)
        if os.path.exists(root):
            metrics.set("storage_usage_percent", psutil.disk_usage(root).percent)
    return Response(metrics.render(), media_type="text/plain; version=0.0.4")
