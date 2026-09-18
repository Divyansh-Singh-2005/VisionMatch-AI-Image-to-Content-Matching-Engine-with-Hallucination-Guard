"""Choose which images an LLM should audit, and why.

Auditing 15,000 images with an LLM is pointless and slow. We sample the buckets where the
pipeline is most likely to be wrong, plus a random control so agreement can be measured on
ordinary images too.
"""
import random

BUCKETS = (
    "family_disagreement",       # BioCLIP says a different family than the community label
    "species_disagreement",      # same family, different species (fox vs wolf, lynx vs bobcat)
    "low_confidence",            # below the flag threshold: the guard would flag these anyway
    "cross_model_non_animal",    # generic CLIP says "not an animal photo", BioCLIP names a species
    "control_random",            # agreeing, confident images - the baseline for agreement
)


def select_audit(
    bio: list[dict],
    vit: list[dict],
    *,
    flag_threshold: float,
    per_bucket: int = 50,
    seed: int = 7,
) -> list[dict]:
    """Deterministic stratified sample. Each image lands in exactly one bucket (first match wins)."""
    vit_by_id = {p["photo_id"]: p for p in vit}
    pools: dict[str, list[dict]] = {b: [] for b in BUCKETS}

    for p in sorted(bio, key=lambda r: r["photo_id"]):
        v = vit_by_id.get(p["photo_id"])
        if p["pred"] != "other" and p["pred_family"] != p["family"]:
            bucket = "family_disagreement"
        elif p["pred"] != "other" and p["pred"] != p["class"]:
            bucket = "species_disagreement"
        elif p["confidence"] < flag_threshold:
            bucket = "low_confidence"
        elif v is not None and v["pred"] == "other":
            bucket = "cross_model_non_animal"
        else:
            bucket = "control_random"
        pools[bucket].append(p)

    rng = random.Random(seed)
    out = []
    for bucket in BUCKETS:
        pool = pools[bucket]
        chosen = pool if len(pool) <= per_bucket else rng.sample(pool, per_bucket)
        for p in sorted(chosen, key=lambda r: r["photo_id"]):
            v = vit_by_id.get(p["photo_id"], {})
            out.append({
                "photo_id": p["photo_id"],
                "bucket": bucket,
                "label_class": p["class"],
                "label_family": p["family"],
                "bio_pred": p["pred"],
                "bio_pred_animal": p["pred_animal"],
                "bio_confidence": p["confidence"],
                "bio_top3": p["top3"],
                "vit_pred": v.get("pred"),
                "vit_reject_reason": v.get("reject_reason"),
                "pool_size": len(pool),
            })
    return out


def plan_summary(plan: list[dict], bio_total: int) -> dict:
    counts: dict[str, dict] = {}
    for item in plan:
        b = counts.setdefault(item["bucket"], {"sampled": 0, "pool": item["pool_size"]})
        b["sampled"] += 1
    covered = sum(v["pool"] for v in counts.values())
    return {
        "images_total": bio_total,
        "audit_calls": len(plan),
        "buckets": counts,
        "pool_coverage": round(covered / bio_total, 4) if bio_total else 0.0,
    }