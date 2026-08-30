"""Per-camera behavior-analytics rule management.

Rules are stored as JSON on Camera.rules and consumed by the worker's RuleEngine
(see packages.ai.rules.rule_engine_from_json). Invalid specs are rejected on write
so a bad UI payload can never crash the worker (the worker also guards on load).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from apps.api.audit import write_audit
from apps.api.bootstrap import Runtime
from apps.api.dependencies import get_db, get_runtime, require_permission
from packages.ai.rules import rule_from_dict
from packages.domain.models import Camera

router = APIRouter(prefix="/api", tags=["rules"])


class RulesBody(BaseModel):
    rules: list[dict]


@router.get("/cameras/{camera_id}/rules", dependencies=[Depends(require_permission("rules:configure"))])
def get_rules(camera_id: str, db: Session = Depends(get_db)):
    cam = db.get(Camera, camera_id)
    if not cam:
        raise HTTPException(status_code=404, detail="camera not found")
    return {"camera_id": camera_id, "rules": cam.rules or []}


@router.put("/cameras/{camera_id}/rules", dependencies=[Depends(require_permission("rules:configure"))])
def put_rules(camera_id: str, body: RulesBody, request: Request, db: Session = Depends(get_db)):
    cam = db.get(Camera, camera_id)
    if not cam:
        raise HTTPException(status_code=404, detail="camera not found")
    # Validate every spec up-front (dry run) so we never persist a malformed rule.
    for spec in body.rules:
        try:
            rule_from_dict(camera_id, spec)  # raises on bad spec
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"invalid rule: {exc}") from exc
    cam.rules = body.rules
    write_audit(db, user=request.state.user, action="camera.rules.update", resource=camera_id,
                request_id=getattr(request.state, "request_id", "-"),
                detail={"count": len(body.rules)})
    db.commit()
    return {"camera_id": camera_id, "rules": cam.rules}
