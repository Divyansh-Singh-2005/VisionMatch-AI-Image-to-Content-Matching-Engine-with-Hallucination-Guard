"""Local CLIP zero-shot tagging + image embeddings for the v2 corpus (resumable).

  python -m scripts.v2.clip_tag run --name smoke --limit-per-class 20
  python -m scripts.v2.clip_tag run --name full
  python -m scripts.v2.clip_tag eval --name full
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

from scripts.v2.clip_eval import REJECT_PROMPTS, build_class_prompts, format_report, plan_rows, summarize
from scripts.v2.fetch_inat import load_manifest, load_taxonomy, save_manifest

OUT = Path("data/v2/clip")
EVIDENCE = Path("docs/evidence/v2")
DEFAULT_MODEL = "ViT-B-32"
DEFAULT_PRETRAINED = "laion2b_s34b_b79k"
CHUNK = 1024


class ImageDataset:
    """Map-style dataset (torch DataLoader only needs __len__ / __getitem__)."""

    def __init__(self, files: list[str], preprocess) -> None:
        self.files = files
        self.preprocess = preprocess

    def __len__(self) -> int:
        return len(self.files)

    def __getitem__(self, i: int):
        from PIL import Image

        with Image.open(self.files[i]) as im:
            return self.preprocess(im.convert("RGB")), i


def cmd_run(args) -> int:
    import numpy as np
    import open_clip
    import torch

    workers = max(0, args.workers)
    torch.set_num_threads(max(1, (os.cpu_count() or 2) - workers))
    tax = load_taxonomy()
    family = {c["slug"]: c["family"] for c in tax["classes"]}
    all_rows = load_manifest()
    rows = [r for r in all_rows if Path(r["file"]).exists()]
    if len(rows) != len(all_rows):
        print(f"warning: {len(all_rows) - len(rows)} manifest images missing on disk - skipped")
    rows = plan_rows(rows, args.limit_per_class)
    d = OUT / args.name
    parts = d / "parts"
    parts.mkdir(parents=True, exist_ok=True)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    pretrained = None if args.model.startswith("hf-hub:") else args.pretrained
    model, _, preprocess = open_clip.create_model_and_transforms(
        args.model, pretrained=pretrained, device=device
    )
    model.eval()
    tokenizer = open_clip.get_tokenizer(args.model)

    labels = [(c["slug"], build_class_prompts(c)) for c in tax["classes"]]
    labels += [(f"reject:{k}", v) for k, v in REJECT_PROMPTS.items()]
    names = [n for n, _ in labels]
    with torch.no_grad():
        feats = []
        for _, prompts in labels:
            f = model.encode_text(tokenizer(prompts).to(device)).float()
            f = f / f.norm(dim=-1, keepdim=True)
            m = f.mean(dim=0)
            feats.append(m / m.norm())
        text = torch.stack(feats)
        scale = float(model.logit_scale.detach().exp())
    np.savez(d / "class_embeddings.npz", labels=np.array(names), emb=text.cpu().numpy().astype("float32"))

    chunks = [rows[i:i + CHUNK] for i in range(0, len(rows), CHUNK)]
    print(f"{len(rows)} images | {len(chunks)} chunks | {args.model}/{args.pretrained} | {device} | "
          f"torch threads {torch.get_num_threads()} | loader workers {workers}")
    started = time.time()
    processed = 0
    for ci, chunk in enumerate(chunks):
        part = parts / f"part_{ci:04d}.npz"
        ids = np.array([r["photo_id"] for r in chunk], dtype=np.int64)
        if part.exists():
            with np.load(part) as z:
                if np.array_equal(z["ids"], ids):
                    continue
        loader = torch.utils.data.DataLoader(
            ImageDataset([r["file"] for r in chunk], preprocess),
            batch_size=args.batch_size, num_workers=workers,
        )
        emb = np.zeros((len(chunk), text.shape[1]), dtype=np.float32)
        probs = np.zeros((len(chunk), len(names)), dtype=np.float32)
        t0 = time.time()
        with torch.no_grad():
            for x, idx in loader:
                f = model.encode_image(x.to(device)).float()
                f = f / f.norm(dim=-1, keepdim=True)
                p = (scale * f @ text.T).softmax(dim=-1)
                emb[idx.numpy()] = f.cpu().numpy()
                probs[idx.numpy()] = p.cpu().numpy()
        tmp = part.with_name(part.stem + ".tmp.npz")
        np.savez(tmp, ids=ids, emb=emb.astype(np.float16), probs=probs)
        tmp.replace(part)
        processed += len(chunk)
        rate = len(chunk) / max(time.time() - t0, 1e-6)
        left = sum(len(c) for c in chunks[ci + 1:])
        print(f"  chunk {ci + 1}/{len(chunks)}  {rate:.1f} img/s  eta {left / rate / 60:.1f} min")
    seconds = time.time() - started

    # ---- merge parts -> predictions + embeddings
    all_ids, all_emb, all_probs = [], [], []
    for ci in range(len(chunks)):
        with np.load(parts / f"part_{ci:04d}.npz") as z:
            all_ids.append(z["ids"])
            all_emb.append(z["emb"])
            all_probs.append(z["probs"])
    ids = np.concatenate(all_ids)
    probs = np.concatenate(all_probs)
    np.savez(d / "image_embeddings.npz", ids=ids, emb=np.concatenate(all_emb))

    by_id = {r["photo_id"]: r for r in rows}
    animal = np.array([not n.startswith("reject:") for n in names])
    preds = []
    for pid, pr in zip(ids.tolist(), probs):
        order = np.argsort(-pr)
        best = names[order[0]]
        a_order = [j for j in order if animal[j]]
        a1, a2 = a_order[0], a_order[1]
        reject = best.startswith("reject:")
        rec = by_id[pid]
        preds.append({
            "photo_id": pid,
            "class": rec["class"],
            "family": rec["family"],
            "pred": "other" if reject else best,
            "pred_family": "other" if reject else family[best],
            "pred_animal": names[a1],
            "confidence": round(float(pr[order[0]]), 4),
            "animal_margin": round(float(pr[a1] - pr[a2]), 4),
            "reject_reason": best.split(":", 1)[1] if reject else None,
            "top3": [[names[j], round(float(pr[j]), 4)] for j in order[:3]],
        })
    save_manifest(preds, d / "predictions.jsonl.gz")
    meta = {
        "model": args.model, "pretrained": args.pretrained, "device": device,
        "images": len(preds), "seconds": round(seconds, 1),
        "images_per_second": round(processed / seconds, 2) if processed and seconds else 0.0,
        "labels": names, "chunk_size": CHUNK,
    }
    (d / "run_meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"RUN {args.name}: {len(preds)} predictions -> {d / 'predictions.jsonl.gz'}")
    return 0


def cmd_eval(args) -> int:
    d = OUT / args.name
    preds = load_manifest(d / "predictions.jsonl.gz")
    meta = json.loads((d / "run_meta.json").read_text(encoding="utf-8"))
    s = summarize(preds, load_taxonomy(), target=args.target)
    (d / "summary.json").write_text(json.dumps(s, indent=2) + "\n", encoding="utf-8")
    report = format_report(s, meta)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / f"clip_eval_{args.name}.txt").write_text(report + "\n", encoding="utf-8")
    print(report)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--name", default="full")
    r.add_argument("--limit-per-class", type=int, default=None)
    r.add_argument("--model", default=DEFAULT_MODEL)
    r.add_argument("--pretrained", default=DEFAULT_PRETRAINED)
    r.add_argument("--batch-size", type=int, default=64)
    r.add_argument("--workers", type=int, default=4)
    e = sub.add_parser("eval")
    e.add_argument("--name", default="full")
    e.add_argument("--target", type=float, default=0.95)
    args = parser.parse_args()
    sys.exit({"run": cmd_run, "eval": cmd_eval}[args.cmd](args))


if __name__ == "__main__":
    main()