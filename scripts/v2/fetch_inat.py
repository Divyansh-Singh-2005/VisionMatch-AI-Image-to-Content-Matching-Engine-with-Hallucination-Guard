"""v2 corpus: a licensed, labelled image library from iNaturalist.

  python -m scripts.v2.fetch_inat resolve                       # taxon ids + how many photos exist
  python -m scripts.v2.fetch_inat manifest [--classes a,b]      # harvest metadata (resumable)
  python -m scripts.v2.fetch_inat download [--limit-per-class N] [--workers 4] [--prune-missing]
  python -m scripts.v2.fetch_inat status

Only research-grade observations (species confirmed by the community) and photos licensed
CC0 / CC-BY / CC-BY-NC are used. Images are stored outside git; the gzipped manifest is committed.
"""
import argparse
import gzip
import hashlib
import io
import json
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image, UnidentifiedImageError

API = "https://api.inaturalist.org/v1"
ROOT = Path("data/v2")
TAXONOMY = ROOT / "taxonomy.json"
MANIFEST = ROOT / "manifest.jsonl.gz"
IMAGES = ROOT / "images"
FAILURES = ROOT / "download_failures.json"
ALLOWED_LICENSES = ("cc0", "cc-by", "cc-by-nc")
USER_AGENT = (
    "flyrank-capstone-image-relevance/2.0 "
    "(+https://github.com/Divyansh-Singh-2005/flyrank-capstone-image-relevance)"
)
MAX_SIDE = 384
API_PAUSE = 1.0  # stay around one API request per second
MAX_PAGES = 60


# ---------------------------------------------------------------- pure helpers
def medium_url(url: str) -> str:
    """iNaturalist photo URLs end in /square.<ext>; 'medium' is ~500px on the long side."""
    base = url.split("?", 1)[0]
    head, _, tail = base.rpartition("/")
    ext = tail.rpartition(".")[2] if "." in tail else "jpg"
    return f"{head}/medium.{ext}"


def image_path(photo_id: int) -> Path:
    """Neutral, sharded path - the species never appears in the filename."""
    shard = hashlib.sha1(str(photo_id).encode()).hexdigest()[:2]
    return IMAGES / shard / f"{photo_id}.jpg"


def pick_photo(obs: dict) -> dict | None:
    for photo in obs.get("photos") or []:
        if (photo.get("license_code") or "").lower() in ALLOWED_LICENSES and photo.get("url"):
            return photo
    return None


def to_record(obs: dict, cls: dict) -> dict | None:
    photo = pick_photo(obs)
    if photo is None:
        return None
    user = obs.get("user") or {}
    return {
        "photo_id": photo["id"],
        "observation_id": obs["id"],
        "class": cls["slug"],
        "family": cls["family"],
        "taxon_id": cls["taxon_id"],
        "file": image_path(photo["id"]).as_posix(),
        "url": medium_url(photo["url"]),
        "license": photo["license_code"].lower(),
        "attribution": photo.get("attribution") or "",
        "observer": user.get("login") or "",
        "observed_on": obs.get("observed_on"),
        "obs_url": f"https://www.inaturalist.org/observations/{obs['id']}",
    }


def load_taxonomy() -> dict:
    return json.loads(TAXONOMY.read_text(encoding="utf-8-sig"))


def save_taxonomy(tax: dict) -> None:
    TAXONOMY.write_text(json.dumps(tax, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_manifest(path: Path | None = None) -> list[dict]:
    path = path or MANIFEST
    if not path.exists():
        return []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def save_manifest(rows: list[dict], path: Path | None = None) -> None:
    """Deterministic gzip (mtime=0, sorted rows) so git only sees real changes."""
    path = path or MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda r: (r["class"], r["photo_id"]))
    raw = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
        for r in rows:
            gz.write((json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(raw.getvalue())
    tmp.replace(path)


# ---------------------------------------------------------------- API
def client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=30,
        follow_redirects=True,
        limits=httpx.Limits(max_connections=8),
    )


def get_json(c: httpx.Client, path: str, params: dict) -> dict:
    for attempt in range(1, 6):
        time.sleep(API_PAUSE)
        try:
            r = c.get(API + path, params=params)
        except httpx.TransportError as exc:
            print(f"  transport error ({type(exc).__name__}); retry {attempt}/5")
            time.sleep(5 * attempt)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            print(f"  HTTP {r.status_code}; backing off (retry {attempt}/5)")
            time.sleep(15 * attempt)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"iNaturalist API kept failing for {path}")


def obs_params(taxon_id: int) -> dict:
    return {
        "taxon_id": taxon_id,
        "quality_grade": "research",
        "photos": "true",
        "photo_license": ",".join(ALLOWED_LICENSES),
    }


# ---------------------------------------------------------------- commands
def taxon_matches(taxon: dict, cls: dict) -> bool:
    """Accept an active species whose name, common name or matched synonym equals ours."""
    if taxon.get("rank") != "species" or taxon.get("is_active") is False:
        return False
    wanted = {cls["scientific_name"].lower(), cls["common_name"].lower()}
    seen = {
        (taxon.get("name") or "").lower(),
        (taxon.get("preferred_common_name") or "").lower(),
        (taxon.get("matched_term") or "").lower(),
    }
    return bool(wanted & seen)


def find_taxon(c: httpx.Client, cls: dict) -> tuple[dict | None, list[str]]:
    candidates: list[str] = []
    searches = (
        ("/taxa", cls["scientific_name"]),
        ("/taxa/autocomplete", cls["scientific_name"]),
        ("/taxa/autocomplete", cls["common_name"]),
    )
    for path, query in searches:
        data = get_json(c, path, {"q": query, "per_page": 30})
        for taxon in data.get("results", []):
            if taxon_matches(taxon, cls):
                return taxon, []
            candidates.append(f"{taxon.get('name')} [{taxon.get('rank')}]")
    return None, candidates[:6]


def cmd_resolve(_args) -> int:
    tax = load_taxonomy()
    missing = []
    with client() as c:
        for cls in tax["classes"]:
            if not cls.get("taxon_id"):
                match, candidates = find_taxon(c, cls)
                if match is None:
                    missing.append(cls["slug"])
                    print(f"{cls['slug']:<18} NOT FOUND ({cls['scientific_name']}); saw: {candidates}")
                    continue
                cls["taxon_id"] = match["id"]
                if match["name"].lower() != cls["scientific_name"].lower():
                    cls["inat_name"] = match["name"]
                    print(f"{cls['slug']:<18} note: iNaturalist files this species as {match['name']}")
            total = get_json(c, "/observations", {**obs_params(cls["taxon_id"]), "per_page": 0})["total_results"]
            cls["available"] = total
            flag = "" if total >= tax["per_class_target"] * 2 else "  <- LOW"
            print(f"{cls['slug']:<18} taxon {cls['taxon_id']:>7}  licensed research-grade obs: {total:>8}{flag}")
    tax["checked_on"] = date.today().isoformat()
    save_taxonomy(tax)
    if missing:
        print(f"MISSING TAXA: {missing}")
        return 1
    print(f"RESOLVED {len(tax['classes'])} classes")
    return 0


def cmd_manifest(args) -> int:
    tax = load_taxonomy()
    target = args.per_class or tax["per_class_target"]
    cap = tax["max_per_observer"]
    wanted = set(args.classes.split(",")) if args.classes else None
    rows = load_manifest()
    by_class: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_class[r["class"]].append(r)

    with client() as c:
        for cls in tax["classes"]:
            if wanted and cls["slug"] not in wanted:
                continue
            if not cls.get("taxon_id"):
                print(f"{cls['slug']}: no taxon_id - run resolve first")
                return 1
            have = by_class[cls["slug"]]
            if len(have) >= target:
                print(f"{cls['slug']:<18} {len(have)}/{target} (already complete)")
                continue
            seen = {r["observation_id"] for r in have}
            per_user = Counter(r["observer"] for r in have)
            id_above = max(seen, default=0)
            pages = 0
            while len(have) < target and pages < MAX_PAGES:
                data = get_json(c, "/observations", {
                    **obs_params(cls["taxon_id"]),
                    "per_page": 200, "order_by": "id", "order": "asc", "id_above": id_above,
                })
                results = data.get("results") or []
                pages += 1
                if not results:
                    break
                for obs in results:
                    id_above = max(id_above, obs["id"])
                    if obs["id"] in seen:
                        continue
                    rec = to_record(obs, cls)
                    if rec is None or per_user[rec["observer"]] >= cap:
                        continue
                    have.append(rec)
                    seen.add(obs["id"])
                    per_user[rec["observer"]] += 1
                    if len(have) >= target:
                        break
            print(f"{cls['slug']:<18} {len(have)}/{target}  pages={pages}  observers={len(per_user)}")
            save_manifest([r for rs in by_class.values() for r in rs])  # checkpoint per class
    total = sum(len(v) for v in by_class.values())
    print(f"MANIFEST {total} records -> {MANIFEST}")
    return 0


def download_one(c: httpx.Client, rec: dict) -> str:
    path = Path(rec["file"])
    if path.exists() and path.stat().st_size > 0:
        return "exists"
    for attempt in range(1, 4):
        try:
            r = c.get(rec["url"])
            if r.status_code in (403, 404):
                return "missing"
            r.raise_for_status()
            img = Image.open(BytesIO(r.content)).convert("RGB")
            img.thumbnail((MAX_SIDE, MAX_SIDE))
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".part")
            img.save(tmp, "JPEG", quality=85, optimize=True)
            tmp.replace(path)
            return "ok"
        except (httpx.HTTPError, UnidentifiedImageError, OSError):
            time.sleep(2 * attempt)
    return "failed"


def cmd_download(args) -> int:
    rows = load_manifest()
    if not rows:
        print("manifest is empty - run manifest first")
        return 1
    if args.limit_per_class:
        taken: Counter = Counter()
        subset = []
        for r in rows:
            if taken[r["class"]] < args.limit_per_class:
                subset.append(r)
                taken[r["class"]] += 1
        rows_to_get = subset
    else:
        rows_to_get = rows
    counts: Counter = Counter()
    bad: list[dict] = []
    started = time.time()
    with client() as c, ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(download_one, c, r): r for r in rows_to_get}
        for n, fut in enumerate(as_completed(futures), start=1):
            result = fut.result()
            counts[result] += 1
            if result in ("missing", "failed"):
                rec = futures[fut]
                bad.append({"photo_id": rec["photo_id"], "class": rec["class"], "result": result, "url": rec["url"]})
            if n % 500 == 0 or n == len(rows_to_get):
                rate = n / max(time.time() - started, 1e-6)
                print(f"  {n}/{len(rows_to_get)}  {dict(counts)}  {rate:.1f} img/s")
    FAILURES.write_text(json.dumps(sorted(bad, key=lambda b: b["photo_id"]), indent=1) + "\n", encoding="utf-8")
    print(f"DOWNLOAD {dict(counts)}")
    if args.prune_missing and bad:
        drop = {b["photo_id"] for b in bad}
        keep = [r for r in load_manifest() if r["photo_id"] not in drop]
        save_manifest(keep)
        print(f"pruned {len(drop)} records; run 'manifest' again to backfill")
    return 0


def cmd_status(_args) -> int:
    tax = load_taxonomy()
    rows = load_manifest()
    per_class = Counter(r["class"] for r in rows)
    on_disk = Counter(r["class"] for r in rows if Path(r["file"]).exists())
    licenses = Counter(r["license"] for r in rows)
    size = sum(p.stat().st_size for p in IMAGES.rglob("*.jpg")) if IMAGES.exists() else 0
    print(f"{'class':<18} {'family':<10} {'manifest':>8} {'on disk':>8}")
    for cls in tax["classes"]:
        s = cls["slug"]
        print(f"{s:<18} {cls['family']:<10} {per_class[s]:>8} {on_disk[s]:>8}")
    print(f"TOTAL manifest={len(rows)} on_disk={sum(on_disk.values())} "
          f"size={size / 1024 / 1024:.0f} MB  observers={len({r['observer'] for r in rows})}")
    print(f"licenses: {dict(licenses)}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("resolve")
    m = sub.add_parser("manifest")
    m.add_argument("--per-class", type=int, default=None)
    m.add_argument("--classes", default=None)
    d = sub.add_parser("download")
    d.add_argument("--limit-per-class", type=int, default=None)
    d.add_argument("--workers", type=int, default=4)
    d.add_argument("--prune-missing", action="store_true")
    sub.add_parser("status")
    args = parser.parse_args()
    handler = {"resolve": cmd_resolve, "manifest": cmd_manifest,
               "download": cmd_download, "status": cmd_status}[args.cmd]
    sys.exit(handler(args))


if __name__ == "__main__":
    main()