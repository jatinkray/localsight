"""Identity (person) management and enrollment.

Enrollment is an explicit, administrator-gated workflow: an operator uploads one
or more reference images, the embedding is generated LOCALLY (never sent to any
external API), encrypted at rest with the person's other embeddings, and the
action is audited. The system never creates identities automatically from
surveillance footage.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.audit import write_audit
from apps.api.bootstrap import Runtime
from apps.api.dependencies import get_db, get_runtime, require_permission
from packages.domain.models import Person, PersonEmbedding

router = APIRouter(prefix="/api/persons", tags=["persons"])


@router.get("", dependencies=[Depends(require_permission("person:view"))])
def list_persons(db: Session = Depends(get_db)):
    from sqlalchemy import func
    rows = db.execute(select(Person).order_by(Person.label)).scalars().all()
    out = []
    for p in rows:
        enrolled = db.execute(
            select(func.count(PersonEmbedding.id))
            .where(PersonEmbedding.person_id == p.id)
        ).scalar() or 0
        out.append({"id": p.id, "label": p.label, "display_name": p.display_name,
                    "status": p.status, "created_at": p.created_at.isoformat(),
                    "faces_enrolled": enrolled})
    return out


@router.post("", dependencies=[Depends(require_permission("person:enroll"))])
def create_person(body: dict, request: Request, db: Session = Depends(get_db), rt: Runtime = Depends(get_runtime)):
    label = (body or {}).get("label")
    if not label:
        raise HTTPException(status_code=400, detail="label required")
    if db.execute(select(Person).where(Person.label == label)).first():
        raise HTTPException(status_code=409, detail="label already exists")
    person = Person(label=label, display_name=(body or {}).get("display_name", ""),
                    created_by=request.state.user.id)
    db.add(person)
    db.flush()
    write_audit(db, user=request.state.user, action="person.create", resource=person.id,
                request_id=getattr(request.state, "request_id", "-"),
                detail={"label": label})
    db.commit()
    return {"id": person.id, "label": person.label}


@router.delete("/{person_id}", dependencies=[Depends(require_permission("person:delete"))])
def delete_person(person_id: str, request: Request, db: Session = Depends(get_db)):
    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="person not found")
    db.delete(person)  # cascade removes embeddings
    write_audit(db, user=request.state.user, action="person.delete", resource=person_id,
                request_id=getattr(request.state, "request_id", "-"))
    db.commit()
    return {"ok": True}


@router.get("/{person_id}/references", dependencies=[Depends(require_permission("person:view"))])
def list_references(person_id: str, db: Session = Depends(get_db)):
    """Embedding metadata for a person's reference enrollments.

    Privacy-by-design honesty: the uploaded IMAGE BYTES are not retained —
    only the encrypted embedding and its provenance. The UI surfaces this
    explicitly (no fake "photo gallery").
    """
    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="person not found")
    rows = db.execute(
        select(PersonEmbedding).where(PersonEmbedding.person_id == person_id)
        .order_by(PersonEmbedding.created_at.desc())
    ).scalars().all()
    return {
        "person_id": person_id,
        "image_bytes_retained": False,
        "references": [
            {"id": r.id, "model_version": r.model_version, "dimension": r.dimension,
             "quality_score": r.quality_score,
             "created_at": r.created_at.isoformat()}
            for r in rows
        ],
    }


@router.post("/{person_id}/references", dependencies=[Depends(require_permission("person:enroll"))])
async def add_reference(
    person_id: str,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    rt: Runtime = Depends(get_runtime),
):
    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="person not found")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty upload")
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="reference image too large (max 10MB)")

    # Local embedding generation (no external API). Reference embedder is
    # deterministic on the image bytes; a production face model would detect and
    # align a face first.
    emb = rt.embedder.embed(data, None)
    record = PersonEmbedding(
        person_id=person.id,
        embedding_enc=rt.crypto.encrypt_json(emb),
        model_version=rt.embedder.model_version,
        dimension=len(emb),
        quality_score=1.0,
        source_ref_enc=rt.crypto.encrypt_str(f"upload:{file.filename}"),
    )
    db.add(record)
    write_audit(db, user=request.state.user, action="person.enroll",
                resource=person_id, request_id=getattr(request.state, "request_id", "-"),
                detail={"model_version": rt.embedder.model_version, "bytes": len(data)})
    db.commit()
    return {"ok": True, "model_version": rt.embedder.model_version, "dimension": len(emb)}
