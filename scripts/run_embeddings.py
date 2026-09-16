"""Run embed_images then embed_posts as background-style batch jobs."""
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.session import SessionLocal
from app.jobs.embedding import KINDS, create_embedding_job, run_embedding_job


def main() -> None:
    setup_logging()
    settings = get_settings()
    for kind in KINDS:
        with SessionLocal() as s:
            job = create_embedding_job(s, settings.default_tenant_id, kind)
        print(f"job {job.id} {kind}: {job.total} item(s) queued")
        if job.total:
            job = run_embedding_job(job.id)
        print(f"job {job.id} {kind} {job.status}: done={job.done} failed={job.failed}")
        if job.status.startswith("paused") or job.status == "aborted":
            break


if __name__ == "__main__":
    main()