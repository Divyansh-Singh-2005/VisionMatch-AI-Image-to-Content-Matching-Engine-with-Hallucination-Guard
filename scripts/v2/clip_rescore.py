"""Re-score saved CLIP image embeddings: prompt strategies x reject thresholds, two-stage decision.

No image is re-encoded - only the text prompts are embedded, then one matmul.

  python -m scripts.v2.clip_rescore sweep --name full
  python -m scripts.v2.clip_rescore sweep --name full --limit-per-class 100   # same ids as a sample run
  python -m scripts.v2.clip_rescore apply --name full --strategy descriptive --reject-threshold 0.9
"""
import argparse
import json
import sys
import time
from pathlib import Path

from scripts.v2.clip_eval import (
    ANIMAL_ANCHORS, REJECT_PROMPTS, STRATEGIES, compare_row, decide_two_stage, format_report,
    plan_rows, prompt_groups, summarize,
)
from scripts.v2.fetch_inat import load_manifest, load_taxonomy, save_manifest

OUT = Path("data/v2/clip")
EVIDENCE = Path("docs/evidence/v2")
THRESHOLDS = [0.5, 0.7, 0.9, 0.95, 0.99]


def load_inputs(name: str, limit_per_class: int | None):
    import numpy as np
    import open_clip
    import torch

    d = OUT / name
    meta = json.loads((d / "run_meta.json").read_text(encoding="utf-8"))
    with np.load(d / "image_embeddings.npz") as z:
        ids, emb = z["ids"], z["emb"].astype("float32")

    manifest = {r["photo_id"]: r for r in load_manifest()}
    rows = [manifest[i] for i in ids.tolist()]
    if limit_per_class:
        keep = {r["photo_id"] for r in plan_rows(rows, limit_per_class)}
        mask = np.array([i in keep for i in ids.tolist()])
        ids, emb = ids[mask], emb[mask]

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model_name = meta["model"]
    pretrained = None if model_name.startswith("hf-hub:") else meta.get("pretrained")
    model, _, _ = open_clip.create_model_and_transforms(model_name, pretrained=pretrained, device=device)
    model.eval()
    tokenizer = open_clip.get_tokenizer(model_name)
    scale = float(model.logit_scale.detach().exp())
    return d, meta, ids, torch.from_numpy(emb).to(device), model, tokenizer, torch, device, scale


def class_and_reject_sims(strategy, model, tokenizer, torch, img, device, tax):
    """Cosine similarity per class (max over its prompt groups) and per reject reason."""
    import numpy as np

    groups, owners = [], []
    for cls in tax["classes"]:
        for g in prompt_groups(cls, strategy):
            groups.append(g)
            owners.append(("class", cls["slug"]))
    for reason, prompts in REJECT_PROMPTS.items():
        groups.append(prompts + ANIMAL_ANCHORS[:0])
        owners.append(("reject", reason))

    rows = []
    with torch.no_grad():
        for prompts in groups:
            f = model.encode_text(tokenizer(prompts).to(device)).float()
            f = f / f.norm(dim=-1, keepdim=True)
            m = f.mean(dim=0)
            rows.append(m / m.norm())
        sims = (img @ torch.stack(rows).T).cpu().numpy()

    class_names = [c["slug"] for c in tax["classes"]]
    reject_names = list(REJECT_PROMPTS)
    class_cols = {s: [i for i, o in enumerate(owners) if o == ("class", s)] for s in class_names}
    reject_cols = {r: [i for i, o in enumerate(owners) if o == ("reject", r)] for r in reject_names}
    cls_sims = np.stack([sims[:, cols].max(axis=1) for s, cols in class_cols.items()], axis=1)
    rej_sims = np.stack([sims[:, cols].max(axis=1) for r, cols in reject_cols.items()], axis=1)
    return cls_sims, class_names, rej_sims, reject_names


def build_preds(cls_sims, class_names, rej_sims, reject_names, ids, by_id, family, scale, threshold):
    preds = []
    for k, pid in enumerate(ids.tolist()):
        d = decide_two_stage(
            {n: float(v) for n, v in zip(class_names, cls_sims[k])},
            {n: float(v) for n, v in zip(reject_names, rej_sims[k])},
            scale=scale, reject_threshold=threshold,
        )
        rec = by_id[pid]
        preds.append({
            "photo_id": pid, "class": rec["class"], "family": rec["family"],
            "pred_family": "other" if d["pred"] == "other" else family[d["pred"]],
            "label_family_agrees": family.get(d["pred_animal"]) == rec["family"],
            **d,
        })
    return preds


def cmd_sweep(args) -> int:
    d, meta, ids, img, model, tok, torch, device, scale = load_inputs(args.name, args.limit_per_class)
    tax = load_taxonomy()
    by_id = {r["photo_id"]: r for r in load_manifest()}
    family = {c["slug"]: c["family"] for c in tax["classes"]}
    tag = f"{args.name}" + (f"_top{args.limit_per_class}" if args.limit_per_class else "")
    lines = [f"model {meta['model']} | {len(ids)} images | {device} | two-stage decision", ""]
    best = None
    for strategy in STRATEGIES:
        t0 = time.time()
        cs, cn, rs, rn = class_and_reject_sims(strategy, model, tok, torch, img, device, tax)
        lines.append(f"strategy {strategy} ({time.time() - t0:.1f}s)")
        for threshold in THRESHOLDS:
            preds = build_preds(cs, cn, rs, rn, ids, by_id, family, scale, threshold)
            s = summarize(preds, tax, target=args.target)
            agree = sum(p["label_family_agrees"] for p in preds) / len(preds)
            lines.append(compare_row(f"reject>={threshold}", s, {"family_agrees": f"{agree:.3f}"}))
            if best is None or s["top1"] > best[0]["top1"]:
                best = (s, strategy, threshold, agree)
        lines.append("")
    s, strategy, threshold, agree = best
    lines.append(f"BEST strategy={strategy} reject_threshold={threshold} top1={s['top1']:.3f} "
                 f"family={s['family_top1']:.3f} family_agrees={agree:.3f} rejected={s['predicted_non_animal']}")
    report = "\n".join(lines)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / f"clip_sweep_{tag}.txt").write_text(report + "\n", encoding="utf-8")
    print(report)
    return 0


def cmd_apply(args) -> int:
    d, meta, ids, img, model, tok, torch, device, scale = load_inputs(args.name, None)
    tax = load_taxonomy()
    by_id = {r["photo_id"]: r for r in load_manifest()}
    family = {c["slug"]: c["family"] for c in tax["classes"]}
    cs, cn, rs, rn = class_and_reject_sims(args.strategy, model, tok, torch, img, device, tax)
    preds = build_preds(cs, cn, rs, rn, ids, by_id, family, scale, args.reject_threshold)
    save_manifest(preds, d / "predictions.jsonl.gz")
    meta.update({"strategy": args.strategy, "reject_threshold": args.reject_threshold, "decision": "two_stage"})
    (d / "run_meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    s = summarize(preds, tax, target=args.target)
    s["label_family_agreement"] = round(sum(p["label_family_agrees"] for p in preds) / len(preds), 4)
    (d / "summary.json").write_text(json.dumps(s, indent=2) + "\n", encoding="utf-8")
    report = (f"model={meta['model']} strategy={args.strategy} reject_threshold={args.reject_threshold}\n"
              + format_report(s, meta) + f"\nLABEL_FAMILY_AGREEMENT={s['label_family_agreement']}")
    (EVIDENCE / f"clip_eval_{args.name}.txt").write_text(report + "\n", encoding="utf-8")
    print(report)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sweep")
    s.add_argument("--name", default="full")
    s.add_argument("--limit-per-class", type=int, default=None)
    s.add_argument("--target", type=float, default=0.95)
    a = sub.add_parser("apply")
    a.add_argument("--name", default="full")
    a.add_argument("--strategy", required=True)
    a.add_argument("--reject-threshold", type=float, default=0.9)
    a.add_argument("--target", type=float, default=0.95)
    args = parser.parse_args()
    sys.exit({"sweep": cmd_sweep, "apply": cmd_apply}[args.cmd](args))


if __name__ == "__main__":
    main()