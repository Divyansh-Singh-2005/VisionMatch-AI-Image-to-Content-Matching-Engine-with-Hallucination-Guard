"""Offline evaluation on the hand-labelled set - no API calls.

For each labelled post the correct outcome is one of the listed images or, when the list
is empty, a NO_CONFIDENT_MATCH refusal.
top-1 precision = posts with the correct outcome / all labelled posts.

Usage: python -m scripts.eval [--sweep] [--write] [--threshold 0.75]
"""
import argparse
import json
from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Image, Post
from app.db.session import SessionLocal
from app.services.guard import decide
from app.services.matching import rank_images

LABELS = Path("eval/labels.json")
RESULTS = Path("eval/results.json")
SWEEP = [round(0.70 + i * 0.01, 2) for i in range(17)]


def load_cases(s, settings):
    labels = json.loads(LABELS.read_text(encoding="utf-8-sig"))["posts"]
    tenant = settings.default_tenant_id
    by_file = {
        img.file_path.rsplit("/", 1)[-1]: img.id
        for img in s.scalars(select(Image).where(Image.tenant_id == tenant))
    }
    cases = []
    for slug, files in labels.items():
        post = s.scalar(select(Post).where(Post.tenant_id == tenant, Post.slug == slug))
        if post is None:
            raise SystemExit(f"labelled post not in DB: {slug} (run seed + embeddings)")
        missing = [f for f in files if f not in by_file]
        if missing:
            raise SystemExit(f"labelled images not in DB for {slug}: {missing}")
        expected = {by_file[f] for f in files}
        cases.append((post, expected, rank_images(s, post, settings.embedding_model)))
    return cases


def score(cases, threshold, settings):
    rows = []
    for post, expected, cands in cases:
        d = decide(
            post.target_subject,
            cands,
            threshold=threshold,
            min_confidence=settings.min_vision_confidence,
            unverified_threshold=settings.unverified_subject_threshold,
            top_k=settings.top_k,
        )
        got = d.suggested.candidate if d.suggested else None
        if expected:
            correct = got is not None and got.image_id in expected
        else:
            correct = got is None
        rows.append({
            "post": post.slug,
            "target_subject": post.target_subject,
            "expected": f"one of {len(expected)} images" if expected else "NO_CONFIDENT_MATCH",
            "decision": d.decision,
            "suggested_image": got.image_id if got else None,
            "score": got.score if got else None,
            "correct": correct,
        })
    return rows


def metrics(rows):
    suggested = [r for r in rows if r["suggested_image"] is not None]
    refusal_cases = [r for r in rows if r["expected"] == "NO_CONFIDENT_MATCH"]
    correct = sum(r["correct"] for r in rows)
    return {
        "posts": len(rows),
        "correct": correct,
        "top1_precision": round(correct / len(rows), 3) if rows else 0.0,
        "suggestions_made": len(suggested),
        "wrong_suggestions": sum(not r["correct"] for r in suggested),
        "refusal_cases": len(refusal_cases),
        "refusals_correct": sum(r["correct"] for r in refusal_cases),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sweep", action="store_true")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--threshold", type=float, default=None)
    args = parser.parse_args()
    settings = get_settings()
    threshold = args.threshold if args.threshold is not None else settings.similarity_threshold

    with SessionLocal() as s:
        cases = load_cases(s, settings)

    if args.sweep:
        print("== threshold sweep (unverified-subject threshold fixed at "
              f"{settings.unverified_subject_threshold:.2f}) ==")
        results = []
        for t in SWEEP:
            m = metrics(score(cases, t, settings))
            results.append((t, m))
            print(f"  {t:.2f}  correct {m['correct']:>2}/{m['posts']}  "
                  f"wrong_suggestions={m['wrong_suggestions']}  refusals_ok={m['refusals_correct']}/{m['refusal_cases']}")
        best = max(m["correct"] for _, m in results)
        best_ts = [t for t, m in results if m["correct"] == best]
        chosen = best_ts[len(best_ts) // 2]
        print(f"  best range: {best_ts[0]:.2f}-{best_ts[-1]:.2f} ({best}/{len(cases)} correct) -> choosing middle")
        print(f"BEST_THRESHOLD={chosen:.2f}")
        return

    rows = score(cases, threshold, settings)
    m = metrics(rows)
    print(f"== eval @ threshold {threshold:.2f} / unverified {settings.unverified_subject_threshold:.2f} ==")
    print(f"{'post':<26} {'target':<11} {'expected':<20} {'decision':<19} {'img':>4} {'score':>6}  result")
    for r in rows:
        img = "-" if r["suggested_image"] is None else str(r["suggested_image"])
        sc = "-" if r["score"] is None else f"{r['score']:.3f}"
        print(f"{r['post']:<26} {r['target_subject'] or '-':<11} {r['expected']:<20} "
              f"{r['decision']:<19} {img:>4} {sc:>6}  {'OK' if r['correct'] else 'WRONG'}")
    print(json.dumps(m))
    print(f"TOP1_PRECISION={m['top1_precision']:.3f}")

    if args.write:
        RESULTS.write_text(json.dumps({
            "threshold": threshold,
            "unverified_subject_threshold": settings.unverified_subject_threshold,
            "vision_model": settings.vision_model,
            "embedding_model": settings.embedding_model,
            "metrics": m,
            "rows": rows,
        }, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {RESULTS}")


if __name__ == "__main__":
    main()