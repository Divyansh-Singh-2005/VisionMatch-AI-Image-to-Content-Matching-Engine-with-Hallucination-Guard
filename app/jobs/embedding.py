"""Embedding batch jobs (embed_images / embed_posts).

embed_posts also extracts each post's target subject (LLM, schema-validated) before embedding.
Every API attempt is written to ai_calls. Unchanged text (same sha256) is skipped - re-runs are idempotent.
"""
import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import Embedding, Image, ImageMetadata, Job, JobItem, Post
from app.db.session import SessionLocal
from app.jobs.tagging import CIRCUIT_BREAKER, QuotaExhausted, classify_api_error
from app.schemas.matching import PostSubject
from app.services.cost import BudgetExceeded, ensure_budget, record_call
from app.services.embeddings import GeminiEmbedder, image_text, post_text, text_hash
from app.services.post_subject import GeminiPostSubject

log = logging.getLogger("jobs.embedding")
KINDS = ("embed_images", "embed_posts")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_embedding_job(session: Session, tenant_id: int, kind: str) -> Job:
    if kind == "embed_images":
        ids = session.scalars(
            select(Image.id)
            .join(ImageMetadata, ImageMetadata.image_id == Image.id)
            .where(Image.tenant_id == tenant_id, Image.status.in_(("tagged", "flagged")))
            .order_by(Image.id)
        ).all()
    elif kind == "embed_posts":
        ids = session.scalars(
            select(Post.id).where(Post.tenant_id == tenant_id).order_by(Post.id)
        ).all()
    else:
        raise ValueError(f"unknown embedding job kind: {kind}")
    job = Job(tenant_id=tenant_id, kind=kind, total=len(ids))
    if not ids:
        job.status = "completed"
        job.finished_at = _now()
    session.add(job)
    session.flush()
    for target_id in ids:
        session.add(JobItem(job_id=job.id, target_id=target_id))
    session.commit()
    return job


def _call_with_retries(
    s: Session,
    job: Job,
    settings: Settings,
    *,
    tenant_id: int,
    kind: str,
    model: str,
    ref: str,
    fn: Callable,
    interval: float,
    validate: Callable | None = None,
):
    """Returns (result, None) on success or (None, last_error). Raises QuotaExhausted / BudgetExceeded."""
    last_error = ""
    for attempt in range(1, settings.max_attempts + 1):
        ensure_budget(s, tenant_id, settings.daily_call_budget)
        try:
            raw = fn()
        except Exception as exc:
            k, detail = classify_api_error(exc)
            last_error = f"api_error[{k}]: {detail}"
            record_call(s, tenant_id=tenant_id, job_id=job.id, kind=kind, model=model,
                        target_ref=ref, ok=False, error=last_error)
            s.commit()
            if k == "quota_daily":
                raise QuotaExhausted(detail) from exc
            if k == "other":
                break
            wait = interval * (2 ** attempt) if k == "rate_limit" else interval * attempt
            log.warning("%s %s attempt %s failed (%s); retry in %.0fs", kind, ref, attempt, last_error, wait)
            time.sleep(max(wait, 2.0))
            continue

        usage = {
            "input_tokens": getattr(raw, "input_tokens", 0),
            "output_tokens": getattr(raw, "output_tokens", 0),
            "latency_ms": getattr(raw, "latency_ms", 0),
        }
        result = raw
        if validate is not None:
            try:
                result = validate(raw)
            except ValidationError as exc:
                last_error = f"schema_invalid: {exc.errors()[0]['msg']}"
                record_call(s, tenant_id=tenant_id, job_id=job.id, kind=kind, model=model,
                            target_ref=ref, ok=False, error=last_error, **usage)
                s.commit()
                time.sleep(interval)
                continue
        record_call(s, tenant_id=tenant_id, job_id=job.id, kind=kind, model=model,
                    target_ref=ref, ok=True, **usage)
        s.commit()
        time.sleep(interval)
        return result, None
    return None, last_error


def run_embedding_job(
    job_id: int,
    embedder: GeminiEmbedder | None = None,
    classifier: GeminiPostSubject | None = None,
) -> Job:
    settings = get_settings()
    embedder = embedder or GeminiEmbedder(settings)
    embed_interval = 60.0 / max(settings.embed_rpm_limit, 1)
    llm_interval = 60.0 / max(settings.vision_rpm_limit, 1)
    consecutive_failures = 0

    with SessionLocal() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise ValueError(f"job {job_id} not found")
        if job.status.startswith("completed"):
            return job
        job.status = "running"
        job.started_at = job.started_at or _now()
        s.commit()

        items = s.scalars(
            select(JobItem).where(JobItem.job_id == job_id, JobItem.status == "queued").order_by(JobItem.id)
        ).all()

        def fail(item: JobItem, error: str) -> None:
            nonlocal consecutive_failures
            item.status = "failed"
            item.last_error = error
            job.failed += 1
            consecutive_failures += 1
            s.commit()

        try:
            for n, item in enumerate(items, start=1):
                if job.kind == "embed_images":
                    img = s.get(Image, item.target_id)
                    meta = s.get(ImageMetadata, item.target_id)
                    if img is None or meta is None:
                        item.status = "skipped"
                        s.commit()
                        continue
                    owner_type, tenant_id, text = "image", img.tenant_id, image_text(meta)
                else:
                    post = s.get(Post, item.target_id)
                    if post is None:
                        item.status = "skipped"
                        s.commit()
                        continue
                    owner_type, tenant_id, text = "post", post.tenant_id, post_text(post)
                    if post.target_subject is None:
                        classifier = classifier or GeminiPostSubject(settings)
                        parsed, err = _call_with_retries(
                            s, job, settings, tenant_id=tenant_id, kind="post_subject",
                            model=classifier.model, ref=f"post:{post.id}",
                            fn=lambda: classifier.classify(text), interval=llm_interval,
                            validate=lambda r: PostSubject.model_validate_json(r.text),
                        )
                        if parsed is None:
                            fail(item, err)
                            continue
                        post.target_subject = parsed.target_subject.value
                        s.commit()
                        log.info("post %s target_subject=%s (%s)", post.slug, post.target_subject, parsed.reason)

                thash = text_hash(text)
                existing = s.scalar(
                    select(Embedding).where(
                        Embedding.owner_type == owner_type,
                        Embedding.owner_id == item.target_id,
                        Embedding.model == embedder.model,
                    )
                )
                if existing is not None and existing.text_hash == thash:
                    item.status = "skipped"
                    job.done += 1
                    consecutive_failures = 0
                    s.commit()
                    continue

                call, err = _call_with_retries(
                    s, job, settings, tenant_id=tenant_id, kind="embed", model=embedder.model,
                    ref=f"{owner_type}:{item.target_id}", fn=lambda: embedder.embed(text),
                    interval=embed_interval,
                )
                if call is None:
                    fail(item, err)
                else:
                    if existing is None:
                        s.add(Embedding(
                            tenant_id=tenant_id, owner_type=owner_type, owner_id=item.target_id,
                            model=embedder.model, text_hash=thash, vector=call.vector,
                        ))
                    else:
                        existing.text_hash = thash
                        existing.vector = call.vector
                    item.status = "done"
                    job.done += 1
                    consecutive_failures = 0
                    s.commit()
                log.info("job %s %s progress %s/%s %s:%s -> %s",
                         job.id, job.kind, n, job.total, owner_type, item.target_id, item.status)

                if consecutive_failures >= CIRCUIT_BREAKER:
                    job.status = "aborted"
                    log.error("ALERT job %s aborted after %s consecutive failures; last error: %s",
                              job.id, consecutive_failures, item.last_error)
                    break
        except BudgetExceeded as exc:
            job.status = "paused_budget"
            log.error("ALERT job %s paused: %s", job.id, exc)
        except QuotaExhausted as exc:
            job.status = "paused_quota"
            log.error("ALERT job %s paused: provider daily quota reached (%s); rerun later", job.id, exc)

        if job.status == "running":
            job.status = "completed_with_errors" if job.failed else "completed"
            if job.failed:
                log.error("ALERT job %s finished with %s failed item(s)", job.id, job.failed)
        job.finished_at = _now()
        s.commit()
        return job