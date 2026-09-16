"""Idempotent seed: registers corpus images (dedup by sha256), upserts posts by slug and,
with --snapshot, loads committed tags/subjects/embeddings so probes run without an API key."""
import argparse
import hashlib
import json
from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Image, Post
from app.db.session import SessionLocal

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def seed_images(session, tenant_id: int, images_dir: str) -> tuple[int, int]:
    files = sorted(p for p in Path(images_dir).iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
    added = 0
    for path in files:
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        exists = session.scalar(select(Image.id).where(Image.tenant_id == tenant_id, Image.sha256 == sha))
        if exists:
            continue
        session.add(Image(tenant_id=tenant_id, file_path=path.as_posix(), sha256=sha))
        added += 1
    session.commit()
    return added, len(files)


def seed_posts(session, tenant_id: int, posts_file: str) -> tuple[int, int]:
    posts = json.loads(Path(posts_file).read_text(encoding="utf-8-sig"))
    added = 0
    for p in posts:
        row = session.scalar(select(Post).where(Post.tenant_id == tenant_id, Post.slug == p["slug"]))
        if row is None:
            session.add(Post(tenant_id=tenant_id, slug=p["slug"], title=p["title"], body=p["body"]))
            added += 1
        else:
            row.title, row.body = p["title"], p["body"]
    session.commit()
    return added, len(posts)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", action="store_true", help="load data/snapshot/snapshot.json")
    args = parser.parse_args()
    settings = get_settings()
    tenant = settings.default_tenant_id
    with SessionLocal() as s:
        ia, it = seed_images(s, tenant, settings.images_dir)
        pa, pt = seed_posts(s, tenant, settings.posts_file)
        print(f"images: {ia} new / {it} files | posts: {pa} new / {pt} in file")
        if args.snapshot:
            from scripts.snapshot import apply_snapshot

            print("snapshot applied:", apply_snapshot(s, tenant))


if __name__ == "__main__":
    main()