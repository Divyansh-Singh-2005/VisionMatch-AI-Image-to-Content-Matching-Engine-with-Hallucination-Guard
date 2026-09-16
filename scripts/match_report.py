"""Phase 3 evidence: concept check, ranked + guarded suggestions per post, forced candidate checks.

Usage: python -m scripts.match_report [--no-concept] [--no-posts] [--force SLUG IMAGE_ID ...]
"""
import argparse

from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Post
from app.db.session import SessionLocal
from app.jobs.tagging import classify_api_error
from app.services.cost import record_call
from app.services.embeddings import GeminiEmbedder
from app.services.matching import check_candidate, match_post

PHRASES = ["red fox", "Vulpes vulpes", "wild fox species", "gray wolf", "sourdough bread"]


def concept_check(s, settings) -> None:
    emb = GeminiEmbedder(settings)
    vecs: dict[str, list[float]] = {}
    for phrase in PHRASES:
        ref = f"concept:{phrase}"[:64]
        try:
            call = emb.embed(phrase)
        except Exception as exc:
            kind, detail = classify_api_error(exc)
            record_call(s, tenant_id=settings.default_tenant_id, kind="embed", model=emb.model,
                        target_ref=ref, ok=False, error=f"api_error[{kind}]: {detail}")
            s.commit()
            print(f"concept embed failed for {phrase!r}: {detail}")
            return
        record_call(s, tenant_id=settings.default_tenant_id, kind="embed", model=emb.model,
                    target_ref=ref, input_tokens=call.input_tokens, latency_ms=call.latency_ms, ok=True)
        s.commit()
        vecs[phrase] = call.vector
    base = vecs["red fox"]
    print("== concept check: cosine similarity to 'red fox' ==")
    for phrase in PHRASES[1:]:
        print(f"  {phrase:<18} {sum(a * b for a, b in zip(base, vecs[phrase])):.3f}")


def post_report(s, settings) -> None:
    posts = s.scalars(
        select(Post).where(Post.tenant_id == settings.default_tenant_id).order_by(Post.id)
    ).all()
    correct_top, other_top, wrong_best = [], [], []
    for post in posts:
        decision, cands = match_post(s, post, settings)
        target = post.target_subject or "other"
        print(f"\n== post {post.id} {post.slug} | target={target} | {decision.decision}")
        for i, v in enumerate(decision.verdicts, start=1):
            c = v.candidate
            mark = "ACCEPT" if v.accepted else "REJECT"
            chosen = "  <= SUGGESTED" if decision.suggested is v else ""
            print(f"  #{i} img {c.image_id:>2} {c.subject_canonical or '-':<11} score={c.score:.3f} {mark}{chosen}")
            if not v.accepted:
                print("       " + " | ".join(v.reasons))
        if decision.decision == "NO_CONFIDENT_MATCH":
            print("  reason: " + decision.reasons[0])
        if target != "other":
            first_wrong = next((i for i, c in enumerate(cands, 1) if c.subject_canonical != target), None)
            print(f"  first non-{target} image at rank {first_wrong} of {len(cands)}")
            best_correct = max((c.score for c in cands if c.subject_canonical == target and c.status == "tagged"), default=None)
            best_wrong = max((c.score for c in cands if c.subject_canonical != target), default=None)
            if best_correct is not None:
                correct_top.append((best_correct, post.slug))
            if best_wrong is not None:
                wrong_best.append((best_wrong, post.slug))
        elif cands:
            other_top.append((cands[0].score, post.slug))

    print("\n== threshold analysis ==")
    if correct_top:
        lo = min(correct_top)
        print(f"  lowest best-correct score on subject posts : {lo[0]:.3f} ({lo[1]})")
    if other_top:
        hi = max(other_top)
        print(f"  highest top-1 score on no-subject posts    : {hi[0]:.3f} ({hi[1]})")
    if wrong_best:
        hw = max(wrong_best)
        print(f"  highest wrong-subject score (G2 blocks it) : {hw[0]:.3f} ({hw[1]})")
    if correct_top and other_top:
        print(f"  safe threshold window: ({max(other_top)[0]:.3f}, {min(correct_top)[0]:.3f}]"
              f"  current={settings.similarity_threshold:.2f}")


def forced(s, settings, slug: str, image_id: int) -> None:
    post = s.scalar(select(Post).where(Post.slug == slug, Post.tenant_id == settings.default_tenant_id))
    if post is None:
        print(f"no post {slug}")
        return
    v = check_candidate(s, post, image_id, settings)
    print(f"\n== forced check: post={slug} image={image_id} ==")
    print(f"  Post:      {post.title}  (target={post.target_subject})")
    print(f"  Candidate: {v.candidate.subject} [{v.candidate.subject_canonical}] score={v.candidate.score:.3f}")
    print(f"  Result:    {'ACCEPTED' if v.accepted else 'REJECTED'}")
    for g in v.gates:
        print(f"  [{'PASS' if g.passed else 'FAIL'}] {g.gate}: {g.detail}")
    if not v.accepted:
        print(f"  Reason:    {'; '.join(v.reasons)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-concept", action="store_true")
    parser.add_argument("--no-posts", action="store_true")
    parser.add_argument("--force", nargs=2, action="append", metavar=("SLUG", "IMAGE_ID"), default=[])
    args = parser.parse_args()
    settings = get_settings()
    with SessionLocal() as s:
        if not args.no_concept:
            concept_check(s, settings)
        if not args.no_posts:
            post_report(s, settings)
        for slug, image_id in args.force:
            forced(s, settings, slug, int(image_id))


if __name__ == "__main__":
    main()