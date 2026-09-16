"""HTTP layer: validation and status codes only; logic lives in services and jobs."""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Response
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_tenant_id
from app.core.config import get_settings
from app.db.models import Embedding, Image, ImageMetadata, Job, Post, Review, Suggestion
from app.jobs.embedding import KINDS, create_embedding_job
from app.jobs.tagging import create_tagging_job
from app.schemas.api import (
    CandidateOut, CheckOut, CostRow, CostsOut, EmbedJobIn, GateOut, ImageOut, JobOut, MatchOut,
    PostOut, RankOut, ReviewIn, ReviewOut, SuggestionOut, TagJobIn,
)
from app.schemas.vision import Subject
from app.services.cost import cost_summary
from app.services.guard import Verdict
from app.services.matching import check_candidate, gates_json, match_post, persist_decision
from app.services.review import ReviewError, submit_review

router = APIRouter()
DB = Annotated[Session, Depends(get_db)]
TenantId = Annotated[int, Depends(get_tenant_id)]
PostRef = Annotated[str, Path(min_length=1, max_length=120, pattern=r"^[a-z0-9-]+$")]
ACTIVE = ("queued", "claimed", "running")


@router.get("/health")
def health(db: DB) -> dict:
    db.execute(text("select 1"))
    return {"status": "ok"}


# ---------------- jobs ----------------
@router.post("/jobs/tag-images", response_model=JobOut, status_code=202)
def start_tagging(db: DB, tenant: TenantId, response: Response, body: TagJobIn | None = None):
    body = body or TagJobIn()
    active = db.scalar(
        select(Job)
        .where(Job.tenant_id == tenant, Job.kind == "tag_images", Job.status.in_(ACTIVE))
        .order_by(Job.id)
    )
    if active is not None:
        response.status_code = 200  # idempotent: one active tagging job per tenant
        return active
    if not body.force:
        statuses = ["pending", "failed"] if body.retry_failed else ["pending"]
        waiting = db.scalar(
            select(func.count(Image.id)).where(Image.tenant_id == tenant, Image.status.in_(statuses))
        )
        if not waiting:
            latest = db.scalar(
                select(Job)
                .where(Job.tenant_id == tenant, Job.kind == "tag_images")
                .order_by(Job.id.desc())
            )
            if latest is not None:
                response.status_code = 200  # nothing to tag: no empty job is created
                return latest
    return create_tagging_job(db, tenant, retry_failed=body.retry_failed, limit=body.limit, force=body.force)


@router.post("/jobs/embed", response_model=list[JobOut], status_code=202)
def start_embedding(db: DB, tenant: TenantId, response: Response, body: EmbedJobIn | None = None):
    body = body or EmbedJobIn()
    kinds = KINDS if body.kind == "all" else (f"embed_{body.kind}",)
    active = db.scalars(
        select(Job)
        .where(Job.tenant_id == tenant, Job.kind.in_(kinds), Job.status.in_(ACTIVE))
        .order_by(Job.id)
    ).all()
    if active:
        response.status_code = 200
        return active
    return [create_embedding_job(db, tenant, kind) for kind in kinds]


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: Annotated[int, Path(ge=1)], db: DB, tenant: TenantId):
    job = db.scalar(select(Job).where(Job.id == job_id, Job.tenant_id == tenant))
    if job is None:
        raise HTTPException(404, f"job {job_id} not found")
    return job


# ---------------- images / posts ----------------
def _image_out(img: Image, meta: ImageMetadata | None) -> ImageOut:
    return ImageOut(
        id=img.id,
        file=img.file_path.rsplit("/", 1)[-1],
        status=img.status,
        last_error=img.last_error,
        subject=meta.subject if meta else None,
        subject_canonical=meta.subject_canonical if meta else None,
        category=meta.category if meta else None,
        attributes=list(meta.attributes) if meta else [],
        caption=meta.caption if meta else None,
        confidence=meta.confidence if meta else None,
        model=meta.model if meta else None,
    )


@router.get("/images", response_model=list[ImageOut])
def list_images(
    db: DB,
    tenant: TenantId,
    status_filter: Annotated[
        Literal["pending", "tagged", "flagged", "failed"] | None, Query(alias="status")
    ] = None,
    subject: Subject | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    stmt = (
        select(Image, ImageMetadata)
        .outerjoin(ImageMetadata, ImageMetadata.image_id == Image.id)
        .where(Image.tenant_id == tenant)
        .order_by(Image.id)
        .limit(limit)
        .offset(offset)
    )
    if status_filter:
        stmt = stmt.where(Image.status == status_filter)
    if subject:
        stmt = stmt.where(ImageMetadata.subject_canonical == subject.value)
    return [_image_out(img, meta) for img, meta in db.execute(stmt)]


@router.get("/images/{image_id}", response_model=ImageOut)
def get_image(image_id: Annotated[int, Path(ge=1)], db: DB, tenant: TenantId):
    row = db.execute(
        select(Image, ImageMetadata)
        .outerjoin(ImageMetadata, ImageMetadata.image_id == Image.id)
        .where(Image.id == image_id, Image.tenant_id == tenant)
    ).first()
    if row is None:
        raise HTTPException(404, f"image {image_id} not found")
    return _image_out(*row)


@router.get("/posts", response_model=list[PostOut])
def list_posts(db: DB, tenant: TenantId):
    has_emb = (
        select(Embedding.id)
        .where(Embedding.owner_type == "post", Embedding.owner_id == Post.id)
        .exists()
    )
    rows = db.execute(select(Post, has_emb).where(Post.tenant_id == tenant).order_by(Post.id)).all()
    return [
        PostOut(id=p.id, slug=p.slug, title=p.title, target_subject=p.target_subject, has_embedding=bool(e))
        for p, e in rows
    ]


def _resolve_post(db: Session, tenant: int, ref: str) -> Post:
    stmt = select(Post).where(Post.tenant_id == tenant)
    stmt = stmt.where(Post.id == int(ref)) if ref.isdigit() else stmt.where(Post.slug == ref)
    post = db.scalar(stmt)
    if post is None:
        raise HTTPException(404, f"post '{ref}' not found")
    return post


def _cand_out(v: Verdict, rank: int | None, suggestion_id: int | None) -> CandidateOut:
    c = v.candidate
    return CandidateOut(
        rank=rank,
        image_id=c.image_id,
        score=c.score,
        subject=c.subject,
        subject_canonical=c.subject_canonical,
        confidence=c.confidence,
        accepted=v.accepted,
        suggestion_id=suggestion_id,
        gates=[GateOut(gate=g.gate, passed=g.passed, detail=g.detail) for g in v.gates],
        reasons=v.reasons,
    )


# ---------------- matching ----------------
@router.get("/posts/{post_ref}/images", response_model=MatchOut)
def post_images(
    post_ref: PostRef,
    db: DB,
    tenant: TenantId,
    ranking: Annotated[int, Query(ge=0, le=100)] = 0,
):
    settings = get_settings()
    post = _resolve_post(db, tenant, post_ref)
    try:
        decision, cands = match_post(db, post, settings)
    except LookupError as exc:
        raise HTTPException(409, f"{exc}; run POST /jobs/embed first") from exc
    rows = persist_decision(db, post, decision)
    db.commit()
    out = [_cand_out(v, rank, row.id) for rank, (v, row) in enumerate(zip(decision.verdicts, rows), start=1)]
    return MatchOut(
        post_id=post.id,
        slug=post.slug,
        target_subject=post.target_subject,
        decision=decision.decision,
        suggested=next((c for c in out if c.accepted), None),
        candidates=out,
        reasons=list(decision.reasons),
        no_match_suggestion_id=rows[-1].id if decision.decision == "NO_CONFIDENT_MATCH" else None,
        threshold=settings.similarity_threshold,
        unverified_threshold=settings.unverified_subject_threshold,
        ranking=[
            RankOut(rank=i, image_id=c.image_id, subject_canonical=c.subject_canonical, status=c.status, score=c.score)
            for i, c in enumerate(cands[:ranking], start=1)
        ],
    )


@router.post("/posts/{post_ref}/images/{image_id}/check", response_model=CheckOut)
def force_check(post_ref: PostRef, image_id: Annotated[int, Path(ge=1)], db: DB, tenant: TenantId):
    settings = get_settings()
    post = _resolve_post(db, tenant, post_ref)
    try:
        v = check_candidate(db, post, image_id, settings)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    row = Suggestion(
        tenant_id=tenant,
        post_id=post.id,
        image_id=image_id,
        rank=None,
        score=v.candidate.score,
        decision="SUGGESTED" if v.accepted else "REJECTED",
        reasons=gates_json(v),
    )
    db.add(row)
    db.commit()
    return CheckOut(
        post_id=post.id,
        image_id=image_id,
        result="ACCEPTED" if v.accepted else "REJECTED",
        candidate=_cand_out(v, None, row.id),
    )


# ---------------- review ----------------
@router.get("/suggestions/{suggestion_id}", response_model=SuggestionOut)
def get_suggestion(suggestion_id: Annotated[int, Path(ge=1)], db: DB, tenant: TenantId):
    row = db.execute(
        select(Suggestion, Post.slug, ImageMetadata)
        .join(Post, Post.id == Suggestion.post_id)
        .outerjoin(ImageMetadata, ImageMetadata.image_id == Suggestion.image_id)
        .where(Suggestion.id == suggestion_id, Suggestion.tenant_id == tenant)
    ).first()
    if row is None:
        raise HTTPException(404, f"suggestion {suggestion_id} not found")
    sug, slug, meta = row
    review = db.scalar(select(Review).where(Review.suggestion_id == sug.id))
    return SuggestionOut(
        id=sug.id,
        post_id=sug.post_id,
        post_slug=slug,
        image_id=sug.image_id,
        image_subject=meta.subject if meta else None,
        image_caption=meta.caption if meta else None,
        rank=sug.rank,
        score=sug.score,
        decision=sug.decision,
        reasons=list(sug.reasons),
        created_at=sug.created_at,
        review=ReviewOut.model_validate(review) if review else None,
    )


@router.post("/suggestions/{suggestion_id}/review", response_model=ReviewOut, status_code=201)
def review_suggestion(
    suggestion_id: Annotated[int, Path(ge=1)],
    body: ReviewIn,
    response: Response,
    db: DB,
    tenant: TenantId,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=80)],
):
    try:
        review, replayed = submit_review(db, tenant, suggestion_id, body.action, body.note, idempotency_key)
    except ReviewError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    if replayed:
        response.status_code = 200
    return ReviewOut.model_validate(review).model_copy(update={"replayed": replayed})


# ---------------- costs ----------------
@router.get("/costs", response_model=CostsOut)
def costs(db: DB, tenant: TenantId, limit: Annotated[int, Query(ge=1, le=200)] = 20):
    data = cost_summary(db, tenant, limit, get_settings().daily_call_budget)
    data["recent"] = [CostRow.model_validate(r) for r in data["recent"]]
    return CostsOut(**data)