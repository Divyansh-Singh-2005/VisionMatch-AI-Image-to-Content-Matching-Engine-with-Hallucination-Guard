"""Background worker: claims queued jobs from Postgres (FOR UPDATE SKIP LOCKED) and runs them.

Slow AI work never runs on the request path; the API only inserts a queued job row.
"""
import logging
import signal
import time

from sqlalchemy import text

from app.core.logging import setup_logging
from app.db.session import engine
from app.jobs.embedding import KINDS, run_embedding_job
from app.jobs.tagging import run_tagging_job

log = logging.getLogger("jobs.worker")

RUNNERS = {"tag_images": run_tagging_job, **{kind: run_embedding_job for kind in KINDS}}
CLAIM = text(
    """
    UPDATE jobs SET status = 'claimed'
    WHERE id = (
        SELECT id FROM jobs WHERE status = 'queued' ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1
    )
    RETURNING id, kind
    """
)
MARK_FAILED = text("UPDATE jobs SET status = 'failed', finished_at = now() WHERE id = :id")

_stop = False


def _handle_stop(signum, frame) -> None:  # noqa: ARG001
    global _stop
    _stop = True
    log.info("worker stopping after current job (signal %s)", signum)


def claim_one():
    with engine.begin() as conn:
        return conn.execute(CLAIM).first()


def main(poll_seconds: float = 2.0) -> None:
    setup_logging()
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    log.info("worker started; job kinds: %s", ", ".join(RUNNERS))
    while not _stop:
        row = claim_one()
        if row is None:
            time.sleep(poll_seconds)
            continue
        job_id, kind = row
        runner = RUNNERS.get(kind)
        if runner is None:
            log.error("ALERT job %s has unknown kind %s", job_id, kind)
            with engine.begin() as conn:
                conn.execute(MARK_FAILED, {"id": job_id})
            continue
        log.info("claimed job %s (%s)", job_id, kind)
        try:
            job = runner(job_id)
            log.info("job %s finished: %s done=%s failed=%s", job_id, job.status, job.done, job.failed)
        except Exception:
            log.exception("ALERT job %s crashed", job_id)
            with engine.begin() as conn:
                conn.execute(MARK_FAILED, {"id": job_id})
    log.info("worker stopped")


if __name__ == "__main__":
    main()