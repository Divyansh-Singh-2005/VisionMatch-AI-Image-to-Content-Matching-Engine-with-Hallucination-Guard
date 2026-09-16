"""Reproducible snapshot of vision tags, post subjects and embeddings.

export: python -m scripts.snapshot export  -> data/snapshot/snapshot.json
import: python -m scripts.seed --snapshot   (no API key needed)
"""
import json
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Embedding, Image, ImageMetadata, Post
from app.db.session import SessionLocal

SNAP = Path("data/snapshot/snapshot.json")
META_FIELDS = ("subject", "subject_canonical", "category", "attributes", "caption", "confidence", "model")


def export(path: Path = SNAP) -> None:
    tenant = get_settings().default_tenant_id
    with SessionLocal() as s:
        id_to_file, images = {}, []
        rows = s.execute(
            select(Image, ImageMetadata)
            .outerjoin(ImageMetadata, ImageMetadata.image_id == Image.id)
            .where(Image.tenant_id == tenant)
            .order_by(Image.id)
        ).all()
        for img, meta in rows:
            name = img.file_path.rsplit("/", 1)[-1]
            id_to_file[img.id] = name
            images.append({
                "file": name,
                "sha256": img.sha256,
                "status": img.status,
                "last_error": img.last_error,
                "metadata": None if meta is None else {f: getattr(meta, f) for f in META_FIELDS},
            })
        posts = s.scalars(select(Post).where(Post.tenant_id == tenant).order_by(Post.id)).all()
        id_to_slug = {p.id: p.slug for p in posts}
        embeddings = []
        for e in s.scalars(
            select(Embedding).where(Embedding.tenant_id == tenant).order_by(Embedding.owner_type, Embedding.owner_id)
        ):
            key = id_to_file.get(e.owner_id) if e.owner_type == "image" else id_to_slug.get(e.owner_id)
            if key is None:
                continue
            embeddings.append({
                "owner_type": e.owner_type,
                "key": key,
                "model": e.model,
                "text_hash": e.text_hash,
                "vector": [round(float(x), 6) for x in e.vector],
            })
    data = {
        "images": images,
        "posts": [{"slug": p.slug, "target_subject": p.target_subject} for p in posts],
        "embeddings": embeddings,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"snapshot: {len(images)} images, {len(posts)} posts, {len(embeddings)} embeddings -> {path} "
          f"({path.stat().st_size / 1024:.0f} KB)")


def apply_snapshot(s: Session, tenant_id: int, path: Path = SNAP) -> dict:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    by_sha = {img.sha256: img for img in s.scalars(select(Image).where(Image.tenant_id == tenant_id))}
    file_to_id: dict[str, int] = {}
    n_images = 0
    for rec in data["images"]:
        img = by_sha.get(rec["sha256"])
        if img is None:
            continue
        file_to_id[rec["file"]] = img.id
        if rec["metadata"]:
            s.merge(ImageMetadata(image_id=img.id, **rec["metadata"]))
        img.status = rec["status"]
        img.last_error = rec["last_error"]
        n_images += 1
    posts = {p.slug: p for p in s.scalars(select(Post).where(Post.tenant_id == tenant_id))}
    for rec in data["posts"]:
        if rec["slug"] in posts:
            posts[rec["slug"]].target_subject = rec["target_subject"]
    n_emb = 0
    for rec in data["embeddings"]:
        if rec["owner_type"] == "image":
            owner_id = file_to_id.get(rec["key"])
        else:
            owner_id = posts[rec["key"]].id if rec["key"] in posts else None
        if owner_id is None:
            continue
        existing = s.scalar(select(Embedding).where(
            Embedding.owner_type == rec["owner_type"],
            Embedding.owner_id == owner_id,
            Embedding.model == rec["model"],
        ))
        if existing is None:
            s.add(Embedding(
                tenant_id=tenant_id, owner_type=rec["owner_type"], owner_id=owner_id,
                model=rec["model"], text_hash=rec["text_hash"], vector=rec["vector"],
            ))
        else:
            existing.text_hash = rec["text_hash"]
            existing.vector = rec["vector"]
        n_emb += 1
    s.commit()
    return {"images": n_images, "posts": len(data["posts"]), "embeddings": n_emb}


if __name__ == "__main__":
    if sys.argv[1:] != ["export"]:
        raise SystemExit("usage: python -m scripts.snapshot export")
    export()