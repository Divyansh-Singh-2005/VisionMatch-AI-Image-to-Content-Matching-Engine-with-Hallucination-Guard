"""Pure-Python helpers for v2 CLIP tagging: prompts, sampling plan and evaluation metrics.

Kept free of torch/numpy so the test suite runs inside the Docker image unchanged.
"""
from collections import Counter

TEMPLATES = [
    "a photo of a {}.",
    "a wildlife photo of a {}.",
    "a {} in the wild.",
    "a close-up photo of a {}.",
    "a blurry photo of a {}.",
]

# "Not an animal photo" options - research-grade observations can show tracks, droppings or remains.
REJECT_PROMPTS = {
    "tracks": ["a photo of animal footprints in snow or mud.", "a photo of animal tracks on the ground."],
    "scat": ["a photo of animal droppings on the ground."],
    "remains": ["a photo of an animal skull or bones.", "a photo of a dead animal on a road."],
    "no_animal": ["a photo of an empty landscape.", "a photo of a forest with no animals.",
                  "a photo of a sign with text."],
}


def build_class_prompts(cls: dict) -> list[str]:
    names = [cls["common_name"].lower(), cls["scientific_name"]]
    return [t.format(n) for n in names for t in TEMPLATES]


def plan_rows(rows: list[dict], limit_per_class: int | None) -> list[dict]:
    """All rows, or the first N per class (manifest order) for a quick smoke run."""
    if not limit_per_class:
        return list(rows)
    taken: Counter = Counter()
    out = []
    for r in rows:
        if taken[r["class"]] < limit_per_class:
            out.append(r)
            taken[r["class"]] += 1
    return out


def summarize(preds: list[dict], taxonomy: dict, target: float = 0.95) -> dict:
    fam = {c["slug"]: c["family"] for c in taxonomy["classes"]}
    n = len(preds)
    if n == 0:
        raise ValueError("no predictions")
    correct = [p["pred"] == p["class"] for p in preds]
    family_ok = sum(fam.get(p["pred"]) == fam[p["class"]] for p in preds)
    non_animal = [p for p in preds if p["pred"] == "other"]
    wrong_species = [p for p in preds if p["pred"] not in (p["class"], "other")]
    same_family = sum(fam.get(p["pred"]) == fam[p["class"]] for p in wrong_species)

    per_class = {}
    for slug in fam:
        sub = [p for p in preds if p["class"] == slug]
        if sub:
            per_class[slug] = {"n": len(sub), "top1": round(sum(p["pred"] == slug for p in sub) / len(sub), 4)}

    confusions = Counter((p["class"], p["pred"]) for p in wrong_species).most_common(12)

    curve = []
    for i in range(20):
        t = i / 20
        kept = [p for p in preds if p["pred"] != "other" and p["confidence"] >= t]
        acc = sum(p["pred"] == p["class"] for p in kept) / len(kept) if kept else None
        curve.append({"threshold": t, "coverage": round(len(kept) / n, 4),
                      "accuracy": None if acc is None else round(acc, 4)})
    chosen = next((c for c in curve if c["accuracy"] is not None and c["accuracy"] >= target), None)

    return {
        "images": n,
        "top1": round(sum(correct) / n, 4),
        "family_top1": round(family_ok / n, 4),
        "wrong_species": len(wrong_species),
        "within_family_error_share": round(same_family / len(wrong_species), 4) if wrong_species else 0.0,
        "predicted_non_animal": len(non_animal),
        "non_animal_reasons": dict(Counter(p.get("reject_reason") for p in non_animal)),
        "per_class": per_class,
        "top_confusions": [[t, p, c] for (t, p), c in confusions],
        "target": target,
        "curve": curve,
        "flag_threshold": chosen["threshold"] if chosen else None,
        "coverage_at_flag_threshold": chosen["coverage"] if chosen else None,
    }


def format_report(s: dict, meta: dict) -> str:
    lines = [
        f"model {meta.get('model')}/{meta.get('pretrained')}  device {meta.get('device')}  "
        f"images {s['images']}  speed {meta.get('images_per_second', 0):.1f} img/s  "
        f"runtime {meta.get('seconds', 0) / 60:.1f} min",
        f"species top-1 accuracy        : {s['top1']:.3f}",
        f"family top-1 accuracy         : {s['family_top1']:.3f}",
        f"wrong-species predictions     : {s['wrong_species']} "
        f"({s['within_family_error_share']:.1%} of them inside the true family)",
        f"predicted not-an-animal photo : {s['predicted_non_animal']} {s['non_animal_reasons']}",
        "",
        "per species (lowest first):  n  top-1",
    ]
    for slug, v in sorted(s["per_class"].items(), key=lambda kv: kv[1]["top1"]):
        lines.append(f"  {slug:<18} {v['n']:>4}  {v['top1']:.3f}")
    lines += ["", "top confusions (true -> predicted: count):"]
    for t, p, c in s["top_confusions"]:
        lines.append(f"  {t:<18} -> {p:<18} {c}")
    lines += ["", f"confidence cutoff vs accuracy (target {s['target']:.2f}):", "  cutoff  kept    accuracy"]
    for c in s["curve"]:
        acc = "-" if c["accuracy"] is None else f"{c['accuracy']:.3f}"
        lines.append(f"  {c['threshold']:.2f}    {c['coverage']:.3f}   {acc}")
    lines.append(f"FLAG_THRESHOLD={s['flag_threshold']}  KEPT_AT_THRESHOLD={s['coverage_at_flag_threshold']}")
    return "\n".join(lines)