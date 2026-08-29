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
    return {"status": "alive", "ts": dt.datetime.now(dt.timezone.utc).isoformat()}


@router.get("/health/ready")
def ready(request: Request, rt: Runtime = Depends(get_runtime)):
    components: dict[str, dict] = {}
    try:
        with rt.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        components["database"] = {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        components["database"] = {"status": "down", "detail": str(exc)[:200]}
    try:
        rt.storage.put("__health__", b"ok")
        rt.storage.delete("__health__")
        components["storage"] = {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        components["storage"] = {"status": "down", "detail": str(exc)[:200]}
    overall = "ready" if all(c["status"] == "ok" for c in components.values()) else "degraded"
    return {"status": overall, "components": components,
            "ts": dt.datetime.now(dt.timezone.utc).isoformat()}


@router.get("/api/system/health")
def system_health(request: Request, _: User = Depends(get_current_user), rt: Runtime = Depends(get_runtime)):
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
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


@router.get("/api/system/metrics")
def system_metrics(request: Request, _: User = Depends(get_current_user), rt: Runtime = Depends(get_runtime)):
    if psutil:
        metrics.set("cpu_utilization", psutil.cpu_percent())
        metrics.set("ram_used_mb", psutil.virtual_memory().used / (1024 * 1024))
        root = os.path.abspath(rt.settings.storage_local_root)
        if os.path.exists(root):
            metrics.set("storage_usage_percent", psutil.disk_usage(root).percent)
    return Response(metrics.render(), media_type="text/plain; version=0.0.4")
