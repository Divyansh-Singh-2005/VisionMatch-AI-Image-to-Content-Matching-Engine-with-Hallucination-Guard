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

# ---------------------------------------------------------------- prompt strategies (v2-3)
FAMILY_HINTS = {
    "canid": "a wild dog-like animal",
    "ursid": "a bear",
    "cervid": "a deer with antlers",
    "bovid": "a hoofed animal with horns",
    "felid": "a wild cat",
    "procyonid": "a raccoon",
    "sciurid": "a tree squirrel",
    "mustelid": "a badger",
    "castorid": "a beaver",
}


def _common(cls: dict) -> list[str]:
    return [t.format(cls["common_name"].lower()) for t in TEMPLATES]


def _latin(cls: dict) -> list[str]:
    return [t.format(cls["scientific_name"]) for t in TEMPLATES]


def _descriptive(cls: dict) -> list[str]:
    name = cls["common_name"].lower()
    hint = FAMILY_HINTS.get(cls["family"], "an animal")
    return [
        f"a photo of a {name}, {hint}.",
        f"a wildlife photo of a {name}, {hint}.",
        f"a {name} in its natural habitat.",
        f"a close-up photo of a {name}.",
        f"a camera trap photo of a {name}.",
    ]


def prompt_groups(cls: dict, strategy: str) -> list[list[str]]:
    """Prompt groups for one class. Each group is averaged into one label row;
    rows of the same class are summed after the softmax."""
    if strategy == "both_mean":      # v2-2 baseline: one row mixing both name styles
        return [_common(cls) + _latin(cls)]
    if strategy == "common":
        return [_common(cls)]
    if strategy == "latin":
        return [_latin(cls)]
    if strategy == "both_split":     # common and Latin compete as separate rows
        return [_common(cls), _latin(cls)]
    if strategy == "descriptive":
        return [_descriptive(cls)]
    if strategy == "descriptive_split":
        return [_descriptive(cls), _common(cls), _latin(cls)]
    raise ValueError(f"unknown strategy: {strategy}")


STRATEGIES = ["both_mean", "common", "latin", "both_split", "descriptive", "descriptive_split"]


def aggregate(prob_row, names: list[str]) -> tuple[dict[str, float], dict[str, float]]:
    """Softmax row over label rows -> per-class probability and per-reject-reason probability."""
    classes: dict[str, float] = {}
    rejects: dict[str, float] = {}
    for name, p in zip(names, prob_row):
        target = rejects if name.startswith("reject:") else classes
        key = name.split(":", 1)[1] if name.startswith("reject:") else name
        target[key] = target.get(key, 0.0) + float(p)
    return classes, rejects


def decide(classes: dict[str, float], rejects: dict[str, float], reject_margin: float) -> dict:
    """Reject only when the not-an-animal mass clearly beats the best species.

    reject_margin is how much bigger the reject mass must be; 1.0 means 'strictly bigger',
    2.0 means 'at least twice as big'. Higher values trust the dataset label more.
    """
    ranked = sorted(classes.items(), key=lambda kv: -kv[1])
    best, best_p = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
    reject_mass = sum(rejects.values())
    reason = max(rejects, key=rejects.get) if rejects else None
    rejected = reject_mass > best_p * reject_margin
    return {
        "pred": "other" if rejected else best,
        "pred_animal": best,
        "confidence": round(best_p, 4),
        "animal_margin": round(best_p - runner_up, 4),
        "reject_mass": round(reject_mass, 4),
        "reject_reason": reason if rejected else None,
        "top3": [[k, round(v, 4)] for k, v in ranked[:3]],
    }


def compare_row(name: str, s: dict, extra: dict | None = None) -> str:
    e = extra or {}
    return (f"  {name:<20} top1={s['top1']:.3f}  family={s['family_top1']:.3f}  "
            f"rejected={s['predicted_non_animal']:>5}  "
            f"flag_threshold={s['flag_threshold']}  kept={s['coverage_at_flag_threshold']}"
            + ("".join(f"  {k}={v}" for k, v in e.items())))
