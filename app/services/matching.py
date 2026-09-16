from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import Embedding, Image, ImageMetadata, Post, Suggestion
from app.services.guard import Candidate, Decision, Verdict, decide, evaluate


def _guard_kwargs(settings: Settings) -> dict:
    return {
        "threshold": settings.similarity_threshold,
        "min_confidence": settings.min_vision_confidence,
        "unverified_threshold": settings.unverified_subject_threshold,
    }


def rank_images(session: Session, post: Post, model: str, limit: int | None = None) -> list[Candidate]:
    post_vec = session.scalar(
        select(Embedding.vector).where(
            Embedding.owner_type == "post", Embedding.owner_id == post.id, Embedding.model == model
        )
    )
    if post_vec is None:
        raise LookupError(f"post {post.id} has no embedding for model {model}")
    distance = Embedding.vector.cosine_distance(post_vec).label("distance")
    stmt = (
        select(Image, ImageMetadata, distance)
        .join(Embedding, and_(Embedding.owner_id == Image.id, Embedding.owner_type == "image"))
        .outerjoin(ImageMetadata, ImageMetadata.image_id == Image.id)
        .where(
            Embedding.model == model,
            Embedding.tenant_id == post.tenant_id,
            Image.tenant_id == post.tenant_id,
        )
        .order_by(distance)
    )
    if limit:
        stmt = stmt.limit(limit)
    out: list[Candidate] = []
    for img, meta, dist in session.execute(stmt):
        out.append(Candidate(
            image_id=img.id,
            score=round(1.0 - float(dist), 4),
            status=img.status,
            subject=meta.subject if meta else None,
            subject_canonical=meta.subject_canonical if meta else None,
            confidence=meta.confidence if meta else None,
        ))
    return out


def match_post(session: Session, post: Post, settings: Settings) -> tuple[Decision, list[Candidate]]:
    candidates = rank_images(session, post, settings.embedding_model)
    decision = decide(post.target_subject, candidates, top_k=settings.top_k, **_guard_kwargs(settings))
    return decision, candidates


def check_candidate(session: Session, post: Post, image_id: int, settings: Settings) -> Verdict:
    for c in rank_images(session, post, settings.embedding_model):
        if c.image_id == image_id:
            return evaluate(post.target_subject, c, **_guard_kwargs(settings))
    raise LookupError(f"image {image_id} not found or not embedded for this tenant")


def gates_json(v: Verdict) -> list[dict]:
    return [{"gate": g.gate, "passed": g.passed, "detail": g.detail} for g in v.gates]


def persist_decision(session: Session, post: Post, decision: Decision) -> list[Suggestion]:
    rows: list[Suggestion] = []
    for rank, v in enumerate(decision.verdicts, start=1):
        rows.append(Suggestion(
            tenant_id=post.tenant_id,
            post_id=post.id,
            image_id=v.candidate.image_id,
            rank=rank,
            score=v.candidate.score,
            decision="SUGGESTED" if v.accepted else "REJECTED",
            reasons=gates_json(v),
        ))
    if decision.decision == "NO_CONFIDENT_MATCH":
        rows.append(Suggestion(
            tenant_id=post.tenant_id,
            post_id=post.id,
            image_id=None,
            rank=None,
            score=None,
            decision="NO_CONFIDENT_MATCH",
            reasons=[{"gate": "summary", "passed": False, "detail": r} for r in decision.reasons],
        ))
    session.add_all(rows)
    session.flush()
    return rows