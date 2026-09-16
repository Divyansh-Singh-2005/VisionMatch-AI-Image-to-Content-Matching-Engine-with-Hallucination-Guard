"""Idempotent seed: registers corpus images (dedup by sha256) and upserts posts by slug."""
import hashlib
import json
from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Image, Post
from app.db.session import SessionLocal


def seed_images(session, tenant_id: int, images_dir: str) -> tuple[int, int]:
    files = sorted(p for p in Path(images_dir).iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"})
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
    settings = get_settings()
    with SessionLocal() as s:
        ia, it = seed_images(s, settings.default_tenant_id, settings.images_dir)
        pa, pt = seed_posts(s, settings.default_tenant_id, settings.posts_file)
    print(f"images: {ia} new / {it} files | posts: {pa} new / {pt} in file")


if __name__ == "__main__":
    main()