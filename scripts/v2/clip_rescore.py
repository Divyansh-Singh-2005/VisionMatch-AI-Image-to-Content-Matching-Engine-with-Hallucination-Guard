"""Re-score saved CLIP image embeddings with different prompt strategies and reject rules.

No image is re-encoded: only ~30 short text prompts per strategy are embedded, then one matmul.

  python -m scripts.v2.clip_rescore sweep --name full
  python -m scripts.v2.clip_rescore apply --name full --strategy descriptive --reject-margin 2.0
"""
import argparse
import json
import sys
import time
from pathlib import Path

from scripts.v2.clip_eval import (
    REJECT_PROMPTS, STRATEGIES, aggregate, compare_row, decide, format_report, prompt_groups, summarize,
)
from scripts.v2.fetch_inat import load_manifest, load_taxonomy, save_manifest

OUT = Path("data/v2/clip")
EVIDENCE = Path("docs/evidence/v2")
MARGINS = [1.0, 1.5, 2.0, 3.0, 5.0]


def text_matrix(model, tokenizer, torch, groups: list[list[str]], device: str):
    rows = []
    with torch.no_grad():
        for prompts in groups:
            f = model.encode_text(tokenizer(prompts).to(device)).float()
            f = f / f.norm(dim=-1, keepdim=True)
            m = f.mean(dim=0)
            rows.append(m / m.norm())
    return torch.stack(rows)


def score(strategy: str, model, tokenizer, torch, img, device: str, tax: dict):
    groups, names = [], []
    for cls in tax["classes"]:
        for g in prompt_groups(cls, strategy):
            groups.append(g)
            names.append(cls["slug"])
    for reason, prompts in REJECT_PROMPTS.items():
        groups.append(prompts)
        names.append(f"reject:{reason}")
    text = text_matrix(model, tokenizer, torch, groups, device)
    scale = float(model.logit_scale.exp())
    with torch.no_grad():
        probs = (scale * img @ text.T).softmax(dim=-1)
    return probs.cpu().numpy(), names


def build_preds(probs, names, ids, by_id, family, margin: float) -> list[dict]:
    preds = []
    for pid, row in zip(ids.tolist(), probs):
        classes, rejects = aggregate(row, names)
        d = decide(classes, rejects, margin)
        rec = by_id[pid]
        preds.append({
            "photo_id": pid, "class": rec["class"], "family": rec["family"],
            "pred_family": "other" if d["pred"] == "other" else family[d["pred"]],
            "label_family_agrees": family.get(d["pred_animal"]) == rec["family"],
            **d,
        })
    return preds


def load_inputs(name: str):
    import numpy as np
    import open_clip
    import torch

    d = OUT / name
    with np.load(d / "image_embeddings.npz") as z:
        ids, emb = z["ids"], z["emb"].astype("float32")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model, _, _ = open_clip.create_model_and_transforms(
        "ViT-B-32", pretrained="laion2b_s34b_b79k", device=device
    )
    model.eval()
    tokenizer = open_clip.get_tokenizer("ViT-B-32")
    tax = load_taxonomy()
    by_id = {r["photo_id"]: r for r in load_manifest()}
    family = {c["slug"]: c["family"] for c in tax["classes"]}
    return d, ids, torch.from_numpy(emb).to(device), model, tokenizer, torch, device, tax, by_id, family


def cmd_sweep(args) -> int:
    d, ids, img, model, tok, torch, device, tax, by_id, family = load_inputs(args.name)
    lines = [f"rescoring {len(ids)} saved embeddings on {device} - no image re-encoded", ""]
    results = {}
    for strategy in STRATEGIES:
        t0 = time.time()
        probs, names = score(strategy, model, tok, torch, img, device, tax)
        lines.append(f"strategy {strategy} ({len(names)} label rows, {time.time() - t0:.1f}s)")
        for margin in MARGINS:
            preds = build_preds(probs, names, ids, by_id, family, margin)
            s = summarize(preds, tax, target=args.target)
            agree = sum(p["label_family_agrees"] for p in preds) / len(preds)
            results[(strategy, margin)] = (s, preds)
            lines.append(compare_row(f"margin {margin}", s, {"family_agrees": f"{agree:.3f}"}))
        lines.append("")
    best_key = max(results, key=lambda k: results[k][0]["top1"])
    best_s = results[best_key][0]
    lines.append(f"BEST strategy={best_key[0]} reject_margin={best_key[1]} "
                 f"top1={best_s['top1']:.3f} family={best_s['family_top1']:.3f} "
                 f"(baseline v2-2 top1=0.461)")
    report = "\n".join(lines)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / f"clip_prompt_sweep_{args.name}.txt").write_text(report + "\n", encoding="utf-8")
    print(report)
    return 0


def cmd_apply(args) -> int:
    d, ids, img, model, tok, torch, device, tax, by_id, family = load_inputs(args.name)
    probs, names = score(args.strategy, model, tok, torch, img, device, tax)
    preds = build_preds(probs, names, ids, by_id, family, args.reject_margin)
    save_manifest(preds, d / "predictions.jsonl.gz")
    meta = json.loads((d / "run_meta.json").read_text(encoding="utf-8"))
    meta.update({"strategy": args.strategy, "reject_margin": args.reject_margin, "labels": names,
                 "rescored": True})
    (d / "run_meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    s = summarize(preds, tax, target=args.target)
    s["label_family_agreement"] = round(sum(p["label_family_agrees"] for p in preds) / len(preds), 4)
    (d / "summary.json").write_text(json.dumps(s, indent=2) + "\n", encoding="utf-8")
    report = (f"strategy={args.strategy} reject_margin={args.reject_margin}\n"
              + format_report(s, meta)
              + f"\nLABEL_FAMILY_AGREEMENT={s['label_family_agreement']}")
    (EVIDENCE / f"clip_eval_{args.name}.txt").write_text(report + "\n", encoding="utf-8")
    print(report)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sweep")
    s.add_argument("--name", default="full")
    s.add_argument("--target", type=float, default=0.95)
    a = sub.add_parser("apply")
    a.add_argument("--name", default="full")
    a.add_argument("--strategy", required=True)
    a.add_argument("--reject-margin", type=float, default=2.0)
    a.add_argument("--target", type=float, default=0.95)
    args = parser.parse_args()
    sys.exit({"sweep": cmd_sweep, "apply": cmd_apply}[args.cmd](args))


if __name__ == "__main__":
    main()