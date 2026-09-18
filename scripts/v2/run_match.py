"""v2 matching run: embed posts with BioCLIP's text encoder, rank the catalog, apply the guard.

  python -m scripts.v2.run_match sweep
  python -m scripts.v2.run_match run --threshold 0.20
"""
import argparse
import json
import sys
from pathlib import Path

from scripts.v2.fetch_inat import load_manifest, load_taxonomy, save_rows
from scripts.v2.match_eval import decide_post, format_run, generate_posts, post_text, score_run

CLIP = Path("data/v2/clip")
CATALOG = Path("data/v2/catalog")
OUT = Path("data/v2/match")
EVIDENCE = Path("docs/evidence/v2")
THRESHOLDS = [0.10, 0.15, 0.18, 0.20, 0.22, 0.25, 0.30]
# v1 used top_k=5 for a 52-image library. With 15,000 images and near-identical species,
# the correct image can sit below rank 5 behind look-alikes the guard correctly rejects.
TOP_KS = [5, 10, 20, 50]


def load_all():
    import numpy as np
    import open_clip
    import torch

    tax = load_taxonomy()
    posts = generate_posts(tax)
    catalog = {c["photo_id"]: c for c in load_manifest(CATALOG / "catalog.jsonl.gz")}
    with np.load(CLIP / "bioclip_full" / "image_embeddings.npz") as z:
        ids, emb = z["ids"], z["emb"].astype("float32")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model, _, _ = open_clip.create_model_and_transforms("hf-hub:imageomics/bioclip", device=device)
    model.eval()
    tokenizer = open_clip.get_tokenizer("hf-hub:imageomics/bioclip")
    with torch.no_grad():
        text = model.encode_text(tokenizer([post_text(p) for p in posts]).to(device)).float()
        text = text / text.norm(dim=-1, keepdim=True)
        sims = (text @ torch.from_numpy(emb).to(device).T).cpu().numpy()
    return posts, catalog, ids, sims


def rank_for(post_index: int, ids, sims, catalog: dict, top_n: int = 50):
    import numpy as np

    row = sims[post_index]
    order = np.argsort(-row)[:top_n]
    return [(catalog[int(ids[i])], float(row[i])) for i in order if int(ids[i]) in catalog]


def decisions_at(threshold: float, posts, catalog, ids, sims, top_k: int = 5) -> list[dict]:
    return [decide_post(p, rank_for(i, ids, sims, catalog), threshold=threshold, top_k=top_k)
            for i, p in enumerate(posts)]


def corpus_stats(catalog: dict) -> dict:
    usable = sum(c["status"] == "verified" for c in catalog.values())
    return {"images": len(catalog), "usable": usable,
            "usable_share": usable / len(catalog) if catalog else 0.0}


def cmd_sweep(args) -> int:
    posts, catalog, ids, sims = load_all()
    lines = [f"sweep over {len(posts)} posts x {len(ids)} images", "",
             f"  {'top_k':>5} {'thr':>5} {'top1':>7} {'recall':>7} {'wrong':>6} {'lookalike':>10} {'refusals':>9}"]
    best = None
    for k in TOP_KS:
        for t in THRESHOLDS:
            s = score_run(decisions_at(t, posts, catalog, ids, sims, top_k=k))
            lines.append(f"  {k:>5} {t:>5.2f} {s['top1_precision']:>7.3f} {s['subject_recall']:>7.3f} "
                         f"{s['wrong_suggestions']:>6} {s['lookalike_suggestions']:>10} "
                         f"{s['refusals_correct']:>4}/{s['refusal_posts']}")
            if best is None or s["top1_precision"] > best[2]["top1_precision"]:
                best = (k, t, s)
        lines.append("")
    lines.append(f"BEST_TOP_K={best[0]} BEST_THRESHOLD={best[1]:.2f} top1={best[2]['top1_precision']:.3f} lookalikes={best[2]['lookalike_suggestions']}")
    report = "\n".join(lines)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "match_sweep.txt").write_text(report + "\n", encoding="utf-8")
    print(report)
    return 0


def cmd_run(args) -> int:
    posts, catalog, ids, sims = load_all()
    decisions = decisions_at(args.threshold, posts, catalog, ids, sims, top_k=args.top_k)
    s = score_run(decisions)
    OUT.mkdir(parents=True, exist_ok=True)
    save_rows(decisions, OUT / "decisions.jsonl.gz", key=lambda r: r["slug"])
    (OUT / "results.json").write_text(
        json.dumps({"threshold": args.threshold, "metrics": s}, indent=2) + "\n", encoding="utf-8")
    report = format_run(s, args.threshold, corpus_stats(catalog)) + f"\ntop_k: {args.top_k}"

    examples = ["", "examples:"]
    for slug in ("eurasian-lynx-1", "gray-wolf-1", "emperor-penguins"):
        d = next((x for x in decisions if x["slug"] == slug), None)
        if d is None:
            continue
        examples.append(f"  {slug}: {d['decision']}")
        if d["suggested"]:
            examples.append(f"    image {d['suggested']['image_id']} ({d['suggested']['subject']}, "
                            f"score {d['suggested']['score']:.3f})")
        for v in d["candidates"][:3]:
            if not v["accepted"]:
                examples.append(f"    rejected {v['image_id']}: {v['reasons'][0]}")
    report += "\n".join(examples)
    (EVIDENCE / "match_results.txt").write_text(report + "\n", encoding="utf-8")
    print(report)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sweep")
    r = sub.add_parser("run")
    r.add_argument("--threshold", type=float, default=0.20)
    r.add_argument("--top-k", type=int, default=20)
    args = parser.parse_args()
    sys.exit({"sweep": cmd_sweep, "run": cmd_run}[args.cmd](args))


if __name__ == "__main__":
    main()