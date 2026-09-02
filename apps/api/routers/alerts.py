"""Alert routing + delivery API.

Allows operators to define which analytic event types are routed to which
notification channel (webhook/email/push). Channel secrets are encrypted at rest
and never returned to clients. A test endpoint verifies delivery without touching
a camera. All changes are audited.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from apps.api.audit import write_audit
from apps.api.bootstrap import Runtime
from apps.api.dependencies import get_current_user, get_db, get_runtime, require_permission
from packages.domain.models import AlertRoute, Event, User
from packages.domain.timeutil import iso
from packages.notify import Alert, MqttNotifier, PushNotifier, WebhookNotifier
from packages.security.errors import UnsafeUrlError
from packages.security.ssrf import validate_egress_url

router = APIRouter(prefix="/api", tags=["alerts"])


class RouteCreate(BaseModel):
    rule_type: str
    camera_id: str | None = None
    channel: str
    config: dict | None = None  # webhook url / smtp / recipients — encrypted at rest
    enabled: bool = True
    cooldown_sec: int = 0  # per-route suppression window (0 = disabled)


@router.get("/alerts/routes", dependencies=[Depends(require_permission("alerts:manage"))])
def list_routes(db: Session = Depends(get_db)):
    rows = db.query(AlertRoute).all()
    return [
        {"id": r.id, "rule_type": r.rule_type, "camera_id": r.camera_id,
         "channel": r.channel, "enabled": r.enabled, "cooldown_sec": r.cooldown_sec,
         "created_at": iso(r.created_at)}
        for r in rows
    ]


@router.post("/alerts/routes", dependencies=[Depends(require_permission("alerts:manage"))])
def create_route(body: RouteCreate, request: Request, db: Session = Depends(get_db),
                 rt: Runtime = Depends(get_runtime)):
    if body.channel not in ("webhook", "email", "push", "mqtt"):
        raise HTTPException(status_code=400, detail="unknown channel")
    route = AlertRoute(
        rule_type=body.rule_type, camera_id=body.camera_id, channel=body.channel,
        enabled=body.enabled, cooldown_sec=body.cooldown_sec,
        config_enc=rt.crypto.encrypt_json(body.config or {}) if body.config else None,
    )
    db.add(route)
    db.flush()
    write_audit(db, user=request.state.user, action="alert.route.create", resource=route.id,
                request_id=getattr(request.state, "request_id", "-"))
    db.commit()
    return {"id": route.id, "channel": route.channel, "rule_type": route.rule_type}


@router.delete("/alerts/routes/{route_id}", dependencies=[Depends(require_permission("alerts:manage"))])
def delete_route(route_id: str, request: Request, db: Session = Depends(get_db)):
    route = db.get(AlertRoute, route_id)
    if not route:
        raise HTTPException(status_code=404, detail="route not found")
    db.delete(route)
    write_audit(db, user=request.state.user, action="alert.route.delete", resource=route_id,
                request_id=getattr(request.state, "request_id", "-"))
    db.commit()
    return {"ok": True}


@router.post("/alerts/test", dependencies=[Depends(require_permission("alerts:manage"))])
def test_alert(db: Session = Depends(get_db), rt: Runtime = Depends(get_runtime)):
    """Deliver a synthetic test alert to every configured (webhook/mqtt) route.

    A missing broker or bad route config is swallowed so the endpoint never 500s;
    ``delivered`` counts only successful deliveries.
    """
    notifiers = []
    for r in db.query(AlertRoute).filter_by(enabled=True).all():
        cfg = r.config_enc and rt.crypto.decrypt_json(r.config_enc) or {}
        try:
            if r.channel == "webhook":
                url = cfg.get("url")
                if not url:
                    continue
                validate_egress_url(url, allowlist=rt.settings.ssrf_allowlist_cidrs)
                notifiers.append(WebhookNotifier(url))
            elif r.channel == "mqtt":
                notifiers.append(MqttNotifier(
                    host=cfg.get("host", "localhost"), port=int(cfg.get("port", 1883)),
                    topic=cfg.get("topic", "localsight/alerts/{camera_id}/{rule_type}"),
                    username=cfg.get("username"), password=cfg.get("password"),
                    tls=bool(cfg.get("tls", False)), qos=int(cfg.get("qos", 0)),
                    retain=bool(cfg.get("retain", True)),
                ))
            elif r.channel == "push":
                notifiers.append(PushNotifier(
                    server=cfg.get("server"), topic=cfg.get("topic"),
                    auth_token=cfg.get("auth_token"),
                    priority=cfg.get("priority"),
                    tags=cfg.get("tags"),
                    click=cfg.get("click"),
                    title=cfg.get("title"),
                ))
        except UnsafeUrlError:
            continue
        except Exception:  # noqa: BLE001 - bad route config / no broker must not 500
            continue
    alert = Alert(rule_id="test", rule_type="test", camera_id="", severity="info",
                  title="LocalVision test alert", message="This is a connectivity test.",
                  ts=dt.datetime.now(dt.timezone.utc))
    delivered = 0
    for ntf in notifiers:
        try:
            ntf.send(alert)
            delivered += 1
        except Exception:  # noqa: BLE001 - a delivery failure must not 500 the API
            continue
    return {"delivered": delivered}


@router.get("/alerts/events", dependencies=[Depends(require_permission("events:view"))])
def analytic_events(db: Session = Depends(get_db),
                    camera_id: str | None = None, limit: int = 50, offset: int = 0):
    """Recent point-in-time analytic events (everything that is not a presence window)."""
    q = db.query(Event).filter(Event.event_type != "presence")
    if camera_id:
        q = q.filter(Event.camera_id == camera_id)
    q = q.order_by(Event.timestamp_start.desc()).limit(min(limit, 500)).offset(offset)
    rows = q.all()
    return [
        {"id": e.id, "camera_id": e.camera_id, "event_type": e.event_type,
         "identity_status": e.identity_status, "timestamp_start": iso(e.timestamp_start),
         "detail": e.detail}
        for e in rows
    ]
