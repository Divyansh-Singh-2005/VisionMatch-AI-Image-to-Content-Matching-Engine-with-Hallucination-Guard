"""Build the v2 image catalog: what the library stores about each photo, and why.

Every rule here traces to a row in docs/evidence/v2/audit_results.txt (250 audited images):

  content gate   generic CLIP was right on 96% of its non-animal calls (cross_model_non_animal: live=0.04)
                 and BioCLIP never rejects, so generic CLIP owns "is there a live animal here".
  subject        the community label beat BioCLIP in every bucket (control 0.98 vs 0.98, family
                 disagreement 0.71 vs 0.00), so the label is stored and BioCLIP verifies it.
  family match   family agreement is 0.91-1.00 everywhere, species agreement as low as 0.12 on
                 look-alikes, so verification is done at family level.
  confidence     93% of low-confidence predictions were still correct, so low confidence alone does
                 NOT flag an image.
"""
import json
from collections import Counter
from pathlib import Path

CLIP = Path("data/v2/clip")
OUT = Path("data/v2/catalog")
EVIDENCE = Path("docs/evidence/v2")

STATUS_OK = "verified"          # label and BioCLIP agree at family level: safe to suggest
STATUS_REVIEW = "needs_review"  # family disagreement: a human or the LLM should look
STATUS_NO_ANIMAL = "no_animal"  # generic CLIP says tracks / scat / remains / empty scene


def classify_image(bio: dict, vit: dict | None, family: dict) -> dict:
    """Decide what the catalog stores for one photo."""
    label = bio["class"]
    bio_family = family.get(bio["pred_animal"])
    label_family = family[label]

    if vit is not None and vit.get("pred") == "other":
        status, reason = STATUS_NO_ANIMAL, f"no live animal detected ({vit.get('reject_reason')})"
    elif bio_family == label_family:
        status, reason = STATUS_OK, f"label confirmed at family level ({label_family})"
    else:
        status = STATUS_REVIEW
        reason = (f"family mismatch: label says {label_family}, "
                  f"vision model sees {bio_family or 'unknown'}")

    return {
        "photo_id": bio["photo_id"],
        "subject": label,                      # the community label is the stored subject
        "family": label_family,
        "status": status,
        "reason": reason,
        "model_subject": bio["pred_animal"],   # kept for auditing, never used as the subject
        "model_family": bio_family,
        "model_confidence": bio["confidence"],
        "species_agrees": bio["pred_animal"] == label,
        "family_agrees": bio_family == label_family,
    }


def build_catalog(bio_rows: list[dict], vit_rows: list[dict], family: dict) -> list[dict]:
    vit_by_id = {r["photo_id"]: r for r in vit_rows}
    return [classify_image(b, vit_by_id.get(b["photo_id"]), family)
            for b in sorted(bio_rows, key=lambda r: r["photo_id"])]


def catalog_stats(catalog: list[dict]) -> dict:
    n = len(catalog)
    status = Counter(c["status"] for c in catalog)
    usable = [c for c in catalog if c["status"] == STATUS_OK]
    per_family = Counter(c["family"] for c in usable)
    return {
        "images": n,
        "status": dict(status),
        "usable_share": round(len(usable) / n, 4) if n else 0.0,
        "species_agreement_on_usable": round(
            sum(c["species_agrees"] for c in usable) / len(usable), 4) if usable else 0.0,
        "usable_per_family": dict(sorted(per_family.items())),
        "species_with_fewest_usable": dict(
            sorted(Counter(c["subject"] for c in usable).items(), key=lambda kv: kv[1])[:5]),
    }


def format_stats(s: dict, audit: dict | None = None) -> str:
    lines = [f"catalog: {s['images']} images", ""]
    for status, n in sorted(s["status"].items(), key=lambda kv: -kv[1]):
        lines.append(f"  {status:<14} {n:>6}  ({n / s['images']:.1%})")
    lines += [
        "",
        f"usable (verified) share            : {s['usable_share']:.1%}",
        f"species agreement within usable    : {s['species_agreement_on_usable']:.1%}",
        "",
        "usable images per family:",
    ]
    for fam, n in s["usable_per_family"].items():
        lines.append(f"  {fam:<12} {n:>6}")
    lines.append("")
    lines.append(f"species with fewest usable images: {s['species_with_fewest_usable']}")
    if audit:
        lines += ["", "audit evidence behind these rules (250 images):"]
        for bucket, b in audit.items():
            r = b["rates"]
            lines.append(f"  {bucket:<24} live={r['live_animal']:.2f} "
                         f"label_species={r['label_species']:.2f} bio_species={r['bio_species']:.2f} "
                         f"family={r['label_family']:.2f}")
    return "\n".join(lines)


def main() -> None:
    from scripts.v2.fetch_inat import load_manifest, load_taxonomy, save_rows

    tax = load_taxonomy()
    family = {c["slug"]: c["family"] for c in tax["classes"]}
    bio = load_manifest(CLIP / "bioclip_full" / "predictions.jsonl.gz")
    vit = load_manifest(CLIP / "full" / "predictions.jsonl.gz")
    catalog = build_catalog(bio, vit, family)
    OUT.mkdir(parents=True, exist_ok=True)
    save_rows(catalog, OUT / "catalog.jsonl.gz", key=lambda r: r["photo_id"])
    stats = catalog_stats(catalog)
    (OUT / "catalog_stats.json").write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")

    audit_path = Path("data/v2/audit/audit_summary.json")
    audit = json.loads(audit_path.read_text(encoding="utf-8"))["buckets"] if audit_path.exists() else None
    report = format_stats(stats, audit)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "catalog.txt").write_text(report + "\n", encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()