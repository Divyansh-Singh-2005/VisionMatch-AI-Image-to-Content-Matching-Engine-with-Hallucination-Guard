"""Vision tagging batch job: retries, pacing, per-call cost log, quota pause, circuit breaker, alerts."""
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import Image, ImageMetadata, Job, JobItem
from app.db.session import SessionLocal
from app.schemas.vision import ImageTags
from app.services.cost import BudgetExceeded, ensure_budget, record_call
from app.services.vision import GeminiVision, VisionClient

log = logging.getLogger("jobs.tagging")

MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
CIRCUIT_BREAKER = 3  # consecutive failed images -> abort job
_QUOTA_ID = re.compile(r"quotaId['\"]?\s*:\s*['\"]([A-Za-z0-9_\-]+)")


class QuotaExhausted(RuntimeError):
    """Daily provider quota reached: retrying today is pointless, pause the job instead."""


def classify_api_error(exc: Exception) -> tuple[str, str]:
    """Return (kind, short detail). kind: quota_daily | rate_limit | transient | other."""
    text = str(exc)
    low = text.lower()
    if "429" in text or "resource_exhausted" in low:
        match = _QUOTA_ID.search(text)
        quota_id = match.group(1) if match else "unknown"
        daily = "perday" in quota_id.lower() or "perday" in low.replace(" ", "")
        return ("quota_daily" if daily else "rate_limit"), f"429 quota={quota_id}"
    if any(code in text for code in ("500", "502", "503", "504")) or "unavailable" in low:
        return "transient", f"{type(exc).__name__}: {text[:120]}"
    return "other", f"{type(exc).__name__}: {text[:160]}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_tagging_job(
    session: Session, tenant_id: int, *, retry_failed: bool = False, limit: int | None = None
) -> Job:
    statuses = ["pending", "failed"] if retry_failed else ["pending"]
    stmt = (
        select(Image)
        .where(Image.tenant_id == tenant_id, Image.status.in_(statuses))
        .order_by(Image.id)
    )
    if limit:
        stmt = stmt.limit(limit)
    images = session.scalars(stmt).all()
    job = Job(tenant_id=tenant_id, kind="tag_images", total=len(images))
    if not images:
        job.status = "completed"
        job.finished_at = _now()
    session.add(job)
    session.flush()
    for img in images:
        session.add(JobItem(job_id=job.id, target_id=img.id))
    session.commit()
    return job


def _process_image(
    s: Session, job: Job, item: JobItem, img: Image, vision: VisionClient, settings: Settings
) -> str:
    interval = 60.0 / max(settings.vision_rpm_limit, 1)
    path = Path(img.file_path)
    data = path.read_bytes()
    mime = MIME.get(path.suffix.lower(), "image/jpeg")
    ref = f"image:{img.id}"
    last_error = ""

    for attempt in range(1, settings.max_attempts + 1):
        ensure_budget(s, img.tenant_id, settings.daily_call_budget)
        item.attempts = attempt
        try:
            call = vision.describe(data, mime)
        except Exception as exc:
            kind, detail = classify_api_error(exc)
            last_error = f"api_error[{kind}]: {detail}"
            record_call(
                s, tenant_id=img.tenant_id, job_id=job.id, kind="vision",
                model=vision.model, target_ref=ref, ok=False, error=last_error,
            )
            s.commit()
            if kind == "quota_daily":
                raise QuotaExhausted(detail) from exc
            if kind == "other":
                log.warning("image %s non-retryable error: %s", img.id, last_error)
                break
            wait = interval * (2 ** attempt) if kind == "rate_limit" else interval * attempt
            log.warning("image %s attempt %s failed (%s); retry in %.0fs", img.id, attempt, last_error, wait)
            time.sleep(wait)
            continue

        try:
            tags = ImageTags.model_validate_json(call.text)
        except ValidationError as exc:
            last_error = f"schema_invalid: {exc.error_count()} error(s): {exc.errors()[0]['msg']}"
            record_call(
                s, tenant_id=img.tenant_id, job_id=job.id, kind="vision", model=vision.model,
                target_ref=ref, input_tokens=call.input_tokens, output_tokens=call.output_tokens,
                latency_ms=call.latency_ms, ok=False, error=last_error,
            )
            s.commit()
            log.warning("image %s attempt %s rejected: %s", img.id, attempt, last_error)
            time.sleep(interval)
            continue

        record_call(
            s, tenant_id=img.tenant_id, job_id=job.id, kind="vision", model=vision.model,
            target_ref=ref, input_tokens=call.input_tokens, output_tokens=call.output_tokens,
            latency_ms=call.latency_ms, ok=True,
        )
        s.merge(ImageMetadata(image_id=img.id, model=vision.model, **tags.model_dump(mode="json")))
        if tags.confidence < settings.min_vision_confidence:
            img.status = "flagged"
            img.last_error = (
                f"low vision confidence {tags.confidence:.2f} < {settings.min_vision_confidence:.2f}"
            )
        else:
            img.status = "tagged"
            img.last_error = None
        item.status = img.status
        item.last_error = img.last_error
        s.commit()
        time.sleep(interval)
        return img.status

    img.status = "failed"
    img.last_error = last_error
    item.status = "failed"
    item.last_error = last_error
    s.commit()
    return "failed"


def run_tagging_job(job_id: int, vision: VisionClient | None = None) -> Job:
    settings = get_settings()
    vision = vision or GeminiVision(settings)
    consecutive_failures = 0

    with SessionLocal() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise ValueError(f"job {job_id} not found")
        if job.status.startswith("completed"):
            return job  # idempotent re-run
        job.status = "running"
        job.started_at = job.started_at or _now()
        s.commit()

        items = s.scalars(
            select(JobItem)
            .where(JobItem.job_id == job_id, JobItem.status == "queued")
            .order_by(JobItem.id)
        ).all()

        try:
            for n, item in enumerate(items, start=1):
                img = s.get(Image, item.target_id)
                if img is None or img.status in ("tagged", "flagged"):
                    item.status = "skipped"
                    s.commit()
                    continue

                result = _process_image(s, job, item, img, vision, settings)
                if result == "failed":
                    job.failed += 1
                    consecutive_failures += 1
                else:
                    job.done += 1
                    consecutive_failures = 0
                    if result == "flagged":
                        job.flagged += 1
                s.commit()
                log.info("job %s progress %s/%s image=%s -> %s", job.id, n, job.total, img.id, result)

                if consecutive_failures >= CIRCUIT_BREAKER:
                    job.status = "aborted"
                    log.error(
                        "ALERT job %s aborted after %s consecutive failures; last error: %s",
                        job.id, consecutive_failures, img.last_error,
                    )
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
                log.error("ALERT job %s finished with %s failed image(s)", job.id, job.failed)
        job.finished_at = _now()
        s.commit()
        return job