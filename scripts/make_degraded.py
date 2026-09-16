"""Create two deliberately degraded copies of corpus photos to exercise low-confidence flagging."""
import json
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter

DIR = Path("data/images")
MANIFEST = DIR / "manifest.json"
SPECS = [
    ("img_051.jpg", "img_005.jpg", "pixelated to 20px wide, upscaled, gaussian blur r=12"),
    ("img_052.jpg", "img_012.jpg", "brightness x0.10, gaussian blur r=14"),
]


def degrade(src: Path, how: str) -> Image.Image:
    img = Image.open(src).convert("RGB")
    w, h = img.size
    if how.startswith("pixelated"):
        small = img.resize((20, max(1, round(20 * h / w))), Image.Resampling.BILINEAR)
        return small.resize((w, h), Image.Resampling.BICUBIC).filter(ImageFilter.GaussianBlur(12))
    return ImageEnhance.Brightness(img).enhance(0.10).filter(ImageFilter.GaussianBlur(14))


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8-sig"))
    by_file = {m["file"]: m for m in manifest}
    for name, source, how in SPECS:
        if name in by_file:
            print(f"{name} already in manifest")
            continue
        degrade(DIR / source, how).save(DIR / name, "JPEG", quality=80, optimize=True)
        src = by_file[source]
        manifest.append({
            "file": name,
            "source_query": "synthetic degraded",
            "derived_from": source,
            "degradation": how,
            "pexels_id": src["pexels_id"],
            "pexels_url": src["pexels_url"],
            "photographer": src["photographer"],
            "photographer_url": src["photographer_url"],
            "license": src["license"],
        })
        print(f"created {name} from {source} ({how})")
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()