from app.services.guard import Candidate, decide, evaluate

T, C = 0.70, 0.60


def cand(image_id, subject, score, status="tagged", conf=0.95):
    return Candidate(image_id, score, status, subject.replace("_", " "), subject, conf)


def test_wolf_rejected_on_fox_post_with_explanation():
    v = evaluate("red_fox", cand(10, "gray_wolf", 0.83), threshold=T, min_confidence=C)
    assert not v.accepted
    assert any("expected red fox, detected gray wolf" in r for r in v.reasons)
    assert any("canid" in r for r in v.reasons)


def test_fox_accepted_on_fox_post():
    v = evaluate("red_fox", cand(1, "red_fox", 0.81), threshold=T, min_confidence=C)
    assert v.accepted and v.reasons == []


def test_dog_rejected_on_fox_post():
    v = evaluate("red_fox", cand(19, "dog", 0.75), threshold=T, min_confidence=C)
    assert not v.accepted


def test_flagged_image_rejected():
    v = evaluate("other", cand(49, "other", 0.90, status="flagged", conf=0.50), threshold=T, min_confidence=C)
    assert not v.accepted
    assert any("low vision confidence (0.50 < 0.60)" in r for r in v.reasons)


def test_below_threshold_rejected():
    v = evaluate("red_fox", cand(1, "red_fox", 0.52), threshold=T, min_confidence=C)
    assert not v.accepted
    assert v.reasons == ["Similarity 0.52 below threshold 0.70"]


def test_no_subject_post_uses_similarity_only():
    assert evaluate("other", cand(47, "other", 0.75), threshold=T, min_confidence=C).accepted
    assert not evaluate("other", cand(47, "other", 0.60), threshold=T, min_confidence=C).accepted


def test_antelope_is_not_deer():
    v = evaluate("deer", cand(37, "antelope", 0.80), threshold=T, min_confidence=C)
    assert not v.accepted
    assert any("expected deer, detected antelope" in r for r in v.reasons)


def test_first_accepted_candidate_is_suggested():
    d = decide("red_fox", [cand(10, "gray_wolf", 0.90), cand(1, "red_fox", 0.85)], threshold=T, min_confidence=C)
    assert d.decision == "SUGGESTED"
    assert d.suggested.candidate.image_id == 1


def test_no_confident_match_explains_every_candidate():
    d = decide("dog", [cand(10, "gray_wolf", 0.80), cand(1, "red_fox", 0.60)], threshold=T, min_confidence=C)
    assert d.decision == "NO_CONFIDENT_MATCH" and d.suggested is None
    assert len(d.reasons) == 3
    assert "threshold 0.70" in d.reasons[0]


def test_empty_candidates():
    d = decide("red_fox", [], threshold=T, min_confidence=C)
    assert d.decision == "NO_CONFIDENT_MATCH"
    assert d.reasons == ("No tagged images available to rank",)


def test_top_k_limits_evaluation():
    cands = [cand(i, "gray_wolf", 0.9) for i in range(10, 16)] + [cand(1, "red_fox", 0.85)]
    d = decide("red_fox", cands, threshold=T, min_confidence=C, top_k=5)
    assert d.decision == "NO_CONFIDENT_MATCH" and len(d.verdicts) == 5