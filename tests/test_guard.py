from app.services.guard import Candidate, decide, evaluate

T, C, U = 0.75, 0.60, 0.80


def cand(image_id, subject, score, status="tagged", conf=0.95):
    return Candidate(image_id, score, status, subject.replace("_", " "), subject, conf)


def ev(target, c):
    return evaluate(target, c, threshold=T, min_confidence=C, unverified_threshold=U)


def test_wolf_rejected_on_fox_post_with_explanation():
    v = ev("red_fox", cand(10, "gray_wolf", 0.83))
    assert not v.accepted
    assert any("expected red fox, detected gray wolf" in r for r in v.reasons)
    assert any("canid" in r for r in v.reasons)


def test_fox_accepted_on_fox_post():
    v = ev("red_fox", cand(1, "red_fox", 0.81))
    assert v.accepted and v.reasons == []


def test_dog_rejected_on_fox_post():
    assert not ev("red_fox", cand(19, "dog", 0.79)).accepted


def test_red_fox_rejected_on_arctic_fox_post():
    v = ev("arctic_fox", cand(1, "red_fox", 0.90))
    assert not v.accepted
    assert any("expected arctic fox, detected red fox" in r for r in v.reasons)


def test_flagged_image_rejected():
    v = ev("red_fox", cand(49, "red_fox", 0.90, status="flagged", conf=0.50))
    assert not v.accepted
    assert any("low vision confidence (0.50 < 0.60)" in r for r in v.reasons)


def test_below_threshold_rejected_with_three_decimals():
    v = ev("red_fox", cand(1, "red_fox", 0.748))
    assert v.reasons == ["Similarity 0.748 below threshold 0.75"]


def test_antelope_is_not_deer():
    v = ev("deer", cand(37, "antelope", 0.80))
    assert not v.accepted
    assert any("expected deer, detected antelope" in r for r in v.reasons)


def test_other_post_rejects_known_animal():
    v = ev("other", cand(31, "brown_bear", 0.95))
    assert not v.accepted
    assert any("outside the library taxonomy, image shows a brown bear" in r for r in v.reasons)


def test_other_post_needs_stricter_bar():
    low = ev("other", cand(50, "other", 0.78))
    assert not low.accepted
    assert low.reasons == ["Similarity 0.780 below unverified-subject threshold 0.80"]
    assert ev("other", cand(50, "other", 0.82)).accepted


def test_first_accepted_candidate_is_suggested():
    d = decide("red_fox", [cand(10, "gray_wolf", 0.90), cand(1, "red_fox", 0.85)],
               threshold=T, min_confidence=C, unverified_threshold=U)
    assert d.decision == "SUGGESTED"
    assert d.suggested.candidate.image_id == 1


def test_no_confident_match_explains_every_candidate():
    d = decide("dog", [cand(10, "gray_wolf", 0.80), cand(1, "red_fox", 0.60)],
               threshold=T, min_confidence=C, unverified_threshold=U)
    assert d.decision == "NO_CONFIDENT_MATCH" and d.suggested is None
    assert len(d.reasons) == 3
    assert "threshold 0.75" in d.reasons[0]
    assert "image 10 (rank 1, score 0.800)" in d.reasons[1]


def test_empty_candidates():
    d = decide("red_fox", [], threshold=T, min_confidence=C)
    assert d.decision == "NO_CONFIDENT_MATCH"
    assert d.reasons == ("No tagged images available to rank",)


def test_top_k_limits_evaluation():
    cands = [cand(i, "gray_wolf", 0.9) for i in range(10, 16)] + [cand(1, "red_fox", 0.85)]
    d = decide("red_fox", cands, threshold=T, min_confidence=C, top_k=5)
    assert d.decision == "NO_CONFIDENT_MATCH" and len(d.verdicts) == 5