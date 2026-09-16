"""Run the vision tagging batch job from the CLI (the API will trigger the same job in Phase 4)."""
import argparse

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.session import SessionLocal
from app.jobs.tagging import create_tagging_job, run_tagging_job


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    setup_logging()
    settings = get_settings()
    with SessionLocal() as s:
        job = create_tagging_job(s, settings.default_tenant_id, retry_failed=args.retry_failed, limit=args.limit)
    print(f"job {job.id}: {job.total} image(s) queued")
    if job.total == 0:
        return
    job = run_tagging_job(job.id)
    print(f"job {job.id} {job.status}: done={job.done} flagged={job.flagged} failed={job.failed}")


if __name__ == "__main__":
    main()