"""Reset selected images to pending so the next tagging job re-processes them.

Usage: python -m scripts.retag --ids 37 38 41
       python -m scripts.retag --source deer "animal silhouette fog"
"""
import argparse
import json
from pathlib import Path

from sqlalchemy import select, update

from app.db.models import Image
from app.db.session import SessionLocal


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids", type=int, nargs="*", default=[])
    parser.add_argument("--source", nargs="*", default=[])
    args = parser.parse_args()

    ids = set(args.ids)
    with SessionLocal() as s:
        if args.source:
            manifest = json.loads(Path("data/images/manifest.json").read_text(encoding="utf-8-sig"))
            files = {m["file"] for m in manifest if m["source_query"] in args.source}
            for img in s.scalars(select(Image)):
                if img.file_path.rsplit("/", 1)[-1] in files:
                    ids.add(img.id)
        if not ids:
            raise SystemExit("nothing selected")
        s.execute(update(Image).where(Image.id.in_(ids)).values(status="pending", last_error=None))
        s.commit()
    print(f"reset to pending: {sorted(ids)}")


if __name__ == "__main__":
    main()