"""Mismatch guard: decides whether a ranked candidate is good enough, and explains why not.

Pure functions, no I/O - every rule is unit-testable.
G1 vision quality  - image must be 'tagged' (flagged / failed images never get suggested)
G2 subject match   - when the post has a target subject, the image's canonical subject must equal it
G3 similarity      - cosine similarity must reach the threshold
"""
from dataclasses import dataclass

from app.schemas.vision import SUBJECT_FAMILY, Subject


@dataclass(frozen=True)
class Candidate:
    image_id: int
    score: float
    status: str
    subject: str | None
    subject_canonical: str | None
    confidence: float | None


@dataclass(frozen=True)
class GateResult:
    gate: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class Verdict:
    candidate: Candidate
    accepted: bool
    gates: tuple[GateResult, ...]

    @property
    def reasons(self) -> list[str]:
        return [g.detail for g in self.gates if not g.passed]


@dataclass(frozen=True)
class Decision:
    decision: str  # SUGGESTED | NO_CONFIDENT_MATCH
    suggested: Verdict | None
    verdicts: tuple[Verdict, ...]
    reasons: tuple[str, ...]


def _label(value: str | None) -> str:
    return (value or "unknown").replace("_", " ")


def _family(value: str | None) -> str | None:
    try:
        return SUBJECT_FAMILY[Subject(value)]
    except (ValueError, KeyError):
        return None


def evaluate(target_subject: str | None, c: Candidate, *, threshold: float, min_confidence: float) -> Verdict:
    gates: list[GateResult] = []

    # G1 - vision quality
    if c.status == "tagged" and c.subject_canonical is not None:
        gates.append(GateResult("G1_vision_quality", True, f"vision confidence {c.confidence:.2f}"))
    elif c.status == "flagged":
        conf = f"{c.confidence:.2f}" if c.confidence is not None else "n/a"
        gates.append(GateResult(
            "G1_vision_quality", False,
            f"Image flagged for review: low vision confidence ({conf} < {min_confidence:.2f})",
        ))
    else:
        gates.append(GateResult("G1_vision_quality", False, f"Image not usable: status '{c.status}'"))

    # G2 - subject match
    target = target_subject or Subject.other.value
    if target == Subject.other.value:
        gates.append(GateResult("G2_subject_match", True, "post has no specific subject; similarity decides"))
    elif c.subject_canonical == target:
        gates.append(GateResult("G2_subject_match", True, f"subject matches: {_label(target)}"))
    elif c.subject_canonical is None:
        gates.append(GateResult(
            "G2_subject_match", False,
            f"Subject unknown: expected {_label(target)}, image has no validated tags",
        ))
    else:
        fam_t, fam_d = _family(target), _family(c.subject_canonical)
        if fam_t and fam_t == fam_d and fam_t != "other":
            detail = (
                f"Animal category mismatch: expected {_label(target)}, detected {_label(c.subject_canonical)} "
                f"(both {fam_t}s - look-alike rejected)"
            )
        else:
            detail = f"Category mismatch: expected {_label(target)}, detected {_label(c.subject_canonical)}"
        gates.append(GateResult("G2_subject_match", False, detail))

    # G3 - similarity threshold
    if c.score >= threshold:
        gates.append(GateResult("G3_similarity", True, f"similarity {c.score:.2f} >= {threshold:.2f}"))
    else:
        gates.append(GateResult(
            "G3_similarity", False, f"Similarity {c.score:.2f} below threshold {threshold:.2f}"
        ))

    return Verdict(candidate=c, accepted=all(g.passed for g in gates), gates=tuple(gates))


def decide(
    target_subject: str | None,
    candidates: list[Candidate],
    *,
    threshold: float,
    min_confidence: float,
    top_k: int = 5,
) -> Decision:
    verdicts = tuple(
        evaluate(target_subject, c, threshold=threshold, min_confidence=min_confidence)
        for c in candidates[:top_k]
    )
    accepted = [v for v in verdicts if v.accepted]
    if accepted:
        return Decision("SUGGESTED", accepted[0], verdicts, ())
    if not verdicts:
        return Decision("NO_CONFIDENT_MATCH", None, verdicts, ("No tagged images available to rank",))
    reasons = [f"None of the top {len(verdicts)} candidates cleared all gates (threshold {threshold:.2f})"]
    for rank, v in enumerate(verdicts, start=1):
        reasons.append(
            f"image {v.candidate.image_id} (rank {rank}, score {v.candidate.score:.2f}): " + "; ".join(v.reasons)
        )
    return Decision("NO_CONFIDENT_MATCH", None, verdicts, tuple(reasons))