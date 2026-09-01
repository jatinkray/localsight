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

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import text

from apps.api.bootstrap import Runtime
from apps.api.dependencies import get_current_user, get_runtime
from packages.domain.models import User
from packages.observability.metrics import metrics

router = APIRouter(tags=["system"])

try:
    import psutil
except Exception:  # pragma: no cover
    psutil = None


@router.get("/health/live")
def live():
    return {"status": "alive", "ts": dt.datetime.now(dt.UTC).isoformat()}


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
            "ts": dt.datetime.now(dt.UTC).isoformat()}


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
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
    }


@router.get("/api/system/metrics")
def system_metrics(request: Request, rt: Runtime = Depends(get_runtime)):
    """Prometheus scrape endpoint.

    Accepts EITHER a static bearer token matching METRICS_SCRAPE_TOKEN
    (Prometheus servers have no login flow) OR a valid user session
    (dashboard use). Without the token path, every scrape 401s and the
    shipped monitoring stack silently reports nothing.
    """
    import secrets as _secrets

    expected = os.environ.get("METRICS_SCRAPE_TOKEN", "")
    auth = request.headers.get("Authorization", "")
    if expected and auth.startswith("Bearer ") and _secrets.compare_digest(auth[7:], expected):
        return _render_metrics(rt)
    # No/incorrect scrape token: require a valid user session via the standard
    # dependency (raises 401 itself on failure).
    get_current_user(request)
    return _render_metrics(rt)


def _render_metrics(rt: Runtime) -> Response:
    if psutil:
        metrics.set("cpu_utilization", psutil.cpu_percent())
        metrics.set("ram_used_mb", psutil.virtual_memory().used / (1024 * 1024))
        root = os.path.abspath(rt.settings.storage_local_root)
        if os.path.exists(root):
            metrics.set("storage_usage_percent", psutil.disk_usage(root).percent)
    return Response(metrics.render(), media_type="text/plain; version=0.0.4")
