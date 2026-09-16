"""Download a small, licensed-free image corpus from Pexels (free API key, no card).

Files get neutral names (img_001.jpg ...) so nothing downstream can "cheat" from filenames.
The source query is kept in manifest.json for eval labelling and attribution only.
"""
import json
import os
import sys
import time
from io import BytesIO
from pathlib import Path

import httpx
from dotenv import load_dotenv
from PIL import Image

load_dotenv()
API_KEY = os.getenv("PEXELS_API_KEY", "")
OUT = Path("data/images")
MAX_SIDE = 640

# (source_query, how many)
PLAN = [
    ("red fox", 9),
    ("gray wolf", 9),
    ("dog", 9),
    ("brown bear", 9),
    ("deer", 9),
    ("animal silhouette fog", 5),  # likely low-confidence images for probe 1
]


def main() -> None:
    if not API_KEY or API_KEY.startswith("your-"):
        sys.exit("PEXELS_API_KEY missing in .env")
    OUT.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    seen: set[int] = set()
    idx = 0
    with httpx.Client(headers={"Authorization": API_KEY}, timeout=30, follow_redirects=True) as client:
        for query, n in PLAN:
            resp = client.get(
                "https://api.pexels.com/v1/search",
                params={"query": query, "per_page": n * 3},
            )
            resp.raise_for_status()
            got = 0
            for photo in resp.json().get("photos", []):
                if got >= n:
                    break
                if photo["id"] in seen:
                    continue
                raw = client.get(photo["src"]["large"]).content
                img = Image.open(BytesIO(raw)).convert("RGB")
                img.thumbnail((MAX_SIDE, MAX_SIDE))
                idx += 1
                got += 1
                seen.add(photo["id"])
                name = f"img_{idx:03d}.jpg"
                img.save(OUT / name, "JPEG", quality=82, optimize=True)
                manifest.append(
                    {
                        "file": name,
                        "source_query": query,
                        "pexels_id": photo["id"],
                        "pexels_url": photo["url"],
                        "photographer": photo["photographer"],
                        "photographer_url": photo["photographer_url"],
                        "license": "Pexels License - https://www.pexels.com/license/",
                    }
                )
                time.sleep(0.3)
            print(f"{query!r}: {got}/{n}")
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"saved {len(manifest)} images to {OUT}")


if __name__ == "__main__":
    main()
