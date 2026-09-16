"""Human review of guard-approved suggestions, idempotent per Idempotency-Key."""
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Review, Suggestion


class ReviewError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _scoped(tenant_id: int, key: str) -> str:
    return f"{tenant_id}:{key}"


def submit_review(
    session: Session, tenant_id: int, suggestion_id: int, action: str, note: str | None, key: str
) -> tuple[Review, bool]:
    """Returns (review, replayed). replayed=True when the same key + request was already applied."""
    sug = session.scalar(
        select(Suggestion).where(Suggestion.id == suggestion_id, Suggestion.tenant_id == tenant_id)
    )
    if sug is None:
        raise ReviewError(404, f"suggestion {suggestion_id} not found")

    scoped = _scoped(tenant_id, key)
    prior = session.scalar(select(Review).where(Review.idempotency_key == scoped))
    if prior is not None:
        if prior.suggestion_id == sug.id and prior.action == action:
            return prior, True
        raise ReviewError(409, "Idempotency-Key was already used for a different request")

    if sug.decision != "SUGGESTED":
        raise ReviewError(
            409, f"suggestion {sug.id} is {sug.decision}; only guard-approved suggestions can be reviewed"
        )
    existing = session.scalar(select(Review).where(Review.suggestion_id == sug.id))
    if existing is not None:
        raise ReviewError(409, f"suggestion {sug.id} was already reviewed ({existing.action})")

    review = Review(suggestion_id=sug.id, action=action, note=note, idempotency_key=scoped)
    session.add(review)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        prior = session.scalar(select(Review).where(Review.idempotency_key == scoped))
        if prior is not None and prior.suggestion_id == sug.id and prior.action == action:
            return prior, True
        raise ReviewError(409, f"suggestion {sug.id} was reviewed concurrently") from None
    return review, False