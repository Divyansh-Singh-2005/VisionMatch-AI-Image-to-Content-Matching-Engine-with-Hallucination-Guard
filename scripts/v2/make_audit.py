"""Build the LLM audit plan from the BioCLIP and ViT-B-32 prediction sets.

  python -m scripts.v2.make_audit --bio bioclip_full --vit full --per-bucket 50
"""
import argparse
import json
from pathlib import Path

from scripts.v2.audit_plan import plan_summary, select_audit
from scripts.v2.fetch_inat import load_manifest, save_rows

OUT = Path("data/v2/audit")
CLIP = Path("data/v2/clip")
EVIDENCE = Path("docs/evidence/v2")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bio", default="bioclip_full")
    parser.add_argument("--vit", default="full")
    parser.add_argument("--per-bucket", type=int, default=50)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    bio = load_manifest(CLIP / args.bio / "predictions.jsonl.gz")
    vit = load_manifest(CLIP / args.vit / "predictions.jsonl.gz")
    summary = json.loads((CLIP / args.bio / "summary.json").read_text(encoding="utf-8"))
    threshold = summary.get("flag_threshold") or 0.9

    plan = select_audit(bio, vit, flag_threshold=threshold, per_bucket=args.per_bucket, seed=args.seed)
    OUT.mkdir(parents=True, exist_ok=True)
    save_rows(plan, OUT / "plan.jsonl.gz", key=lambda r: (r["bucket"], r["photo_id"]))
    s = plan_summary(plan, len(bio))
    s.update({"flag_threshold": threshold, "bio_run": args.bio, "vit_run": args.vit, "seed": args.seed})
    (OUT / "plan_summary.json").write_text(json.dumps(s, indent=2) + "\n", encoding="utf-8")

    lines = [f"audit plan from {args.bio} vs {args.vit}  (flag threshold {threshold})",
             f"{'bucket':<24} {'pool':>7} {'sampled':>8}"]
    for bucket, v in s["buckets"].items():
        lines.append(f"{bucket:<24} {v['pool']:>7} {v['sampled']:>8}")
    lines.append(f"{'TOTAL':<24} {len(bio):>7} {s['audit_calls']:>8}  "
                 f"(pools cover {s['pool_coverage']:.1%} of the corpus)")
    report = "\n".join(lines)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "audit_plan.txt").write_text(report + "\n", encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()