"""Evidence helper: python -m scripts.report [tags|costs|jobs]"""
import json
import sys
from pathlib import Path

from sqlalchemy import func, select

from app.db.models import AICall, Image, ImageMetadata, Job
from app.db.session import SessionLocal


def tags() -> None:
    manifest = {}
    mf = Path("data/images/manifest.json")
    if mf.exists():
        manifest = {m["file"]: m["source_query"] for m in json.loads(mf.read_text(encoding="utf-8-sig"))}
    with SessionLocal() as s:
        rows = s.execute(
            select(Image, ImageMetadata)
            .outerjoin(ImageMetadata, ImageMetadata.image_id == Image.id)
            .order_by(Image.id)
        ).all()
        print(f"{'id':>3} {'file':<12} {'source':<22} {'status':<8} {'canonical':<11} {'conf':>5}  caption / note")
        for img, m in rows:
            name = img.file_path.rsplit("/", 1)[-1]
            src = manifest.get(name, "")[:22]
            if m:
                note = m.caption[:55] + (f"  [{img.last_error}]" if img.status == "flagged" else "")
                print(f"{img.id:>3} {name:<12} {src:<22} {img.status:<8} {m.subject_canonical:<11} {m.confidence:>5.2f}  {note}")
            else:
                print(f"{img.id:>3} {name:<12} {src:<22} {img.status:<8} {'-':<11} {'-':>5}  {(img.last_error or '')[:80]}")
        counts = dict(s.execute(select(Image.status, func.count()).group_by(Image.status)).all())
        print("status counts:", counts)


def costs() -> None:
    with SessionLocal() as s:
        rows = s.execute(
            select(
                AICall.kind, AICall.model, func.count(), func.sum(func.cast(AICall.ok, type_=func.count().type)),
                func.sum(AICall.input_tokens), func.sum(AICall.output_tokens),
                func.sum(AICall.est_cost_usd), func.avg(AICall.latency_ms),
            ).group_by(AICall.kind, AICall.model)
        ).all()
        print(f"{'kind':<8} {'model':<22} {'calls':>5} {'ok':>4} {'in_tok':>8} {'out_tok':>8} {'est_usd':>10} {'avg_ms':>7}")
        for kind, model, n, ok, tin, tout, usd, ms in rows:
            print(f"{kind:<8} {model:<22} {n:>5} {ok or 0:>4} {tin or 0:>8} {tout or 0:>8} {usd or 0:>10.6f} {int(ms or 0):>7}")
        print("\nlast 5 calls:")
        for c in s.scalars(select(AICall).order_by(AICall.id.desc()).limit(5)):
            print(f"  #{c.id} {c.kind} {c.target_ref} ok={c.ok} in={c.input_tokens} out={c.output_tokens} "
                  f"usd={c.est_cost_usd:.6f} ms={c.latency_ms} err={(c.error or '')[:60]}")


def jobs() -> None:
    with SessionLocal() as s:
        for j in s.scalars(select(Job).order_by(Job.id)):
            print(f"job {j.id} {j.kind} {j.status} total={j.total} done={j.done} flagged={j.flagged} failed={j.failed}")


if __name__ == "__main__":
    {"tags": tags, "costs": costs, "jobs": jobs}[sys.argv[1] if len(sys.argv) > 1 else "tags"]()