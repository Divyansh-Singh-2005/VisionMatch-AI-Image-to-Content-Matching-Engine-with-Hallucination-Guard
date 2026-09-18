from scripts.v2.match_eval import decide_post, evaluate_candidate, generate_posts, score_run

TAX = {"classes": [
    {"slug": "red_fox", "common_name": "Red fox", "scientific_name": "Vulpes vulpes", "family": "canid"},
    {"slug": "gray_wolf", "common_name": "Gray wolf", "scientific_name": "Canis lupus", "family": "canid"},
]}
FOX_POST = {"slug": "red-fox-1", "title": "t", "body": "b", "target_subject": "red_fox",
            "target_family": "canid"}
OTHER_POST = {"slug": "sourdough", "title": "t", "body": "b", "target_subject": None,
              "target_family": None}


def img(pid, subject, family, status="verified", reason="ok"):
    return {"photo_id": pid, "subject": subject, "family": family, "status": status, "reason": reason}


def test_posts_cover_every_species_plus_refusal_cases():
    posts = generate_posts(TAX)
    assert sum(p["target_subject"] == "red_fox" for p in posts) == 3
    assert len([p for p in posts if p["target_subject"] is None]) == 10
    assert any("Vulpes vulpes" in p["title"] or "Vulpes vulpes" in p["body"] for p in posts)


def test_matching_image_is_accepted():
    v = evaluate_candidate(FOX_POST, img(1, "red_fox", "canid"), 0.30, threshold=0.2)
    assert v["accepted"] and v["reasons"] == []


def test_lookalike_is_rejected_with_a_family_reason():
    v = evaluate_candidate(FOX_POST, img(2, "gray_wolf", "canid"), 0.30, threshold=0.2)
    assert not v["accepted"]
    assert "Look-alike rejected" in v["reasons"][0] and "canid" in v["reasons"][0]


def test_no_animal_image_is_rejected_whatever_the_score():
    v = evaluate_candidate(FOX_POST, img(3, "red_fox", "canid", "no_animal", "tracks"), 0.99, threshold=0.2)
    assert not v["accepted"] and "no live animal" in v["reasons"][0]


def test_needs_review_image_is_never_suggested():
    v = evaluate_candidate(FOX_POST, img(4, "red_fox", "canid", "needs_review", "family mismatch"),
                           0.9, threshold=0.2)
    assert not v["accepted"] and "awaiting review" in v["reasons"][0]


def test_post_outside_the_taxonomy_always_refuses():
    v = evaluate_candidate(OTHER_POST, img(5, "red_fox", "canid"), 0.99, threshold=0.2)
    assert not v["accepted"] and "outside the library taxonomy" in v["reasons"][0]


def test_decide_picks_the_first_accepted_candidate():
    ranked = [(img(2, "gray_wolf", "canid"), 0.40), (img(1, "red_fox", "canid"), 0.35)]
    d = decide_post(FOX_POST, ranked, threshold=0.2)
    assert d["decision"] == "SUGGESTED" and d["suggested"]["image_id"] == 1


def test_decide_refuses_and_explains_every_candidate():
    ranked = [(img(2, "gray_wolf", "canid"), 0.40), (img(3, "gray_wolf", "canid"), 0.35)]
    d = decide_post(FOX_POST, ranked, threshold=0.2)
    assert d["decision"] == "NO_CONFIDENT_MATCH" and len(d["reasons"]) == 2


def test_score_run_counts_lookalikes_separately():
    good = decide_post(FOX_POST, [(img(1, "red_fox", "canid"), 0.4)], threshold=0.2)
    refuse = decide_post(OTHER_POST, [(img(1, "red_fox", "canid"), 0.4)], threshold=0.2)
    s = score_run([good, refuse])
    assert s["top1_precision"] == 1.0 and s["refusals_correct"] == 1 and s["wrong_suggestions"] == 0

def test_top_k_controls_how_deep_the_guard_looks():
    """With many look-alikes ahead of it, the right image needs a deeper candidate list."""
    ranked = [(img(i, "gray_wolf", "canid"), 0.4) for i in range(10)]
    ranked.append((img(99, "red_fox", "canid"), 0.35))
    assert decide_post(FOX_POST, ranked, threshold=0.2, top_k=5)["decision"] == "NO_CONFIDENT_MATCH"
    deep = decide_post(FOX_POST, ranked, threshold=0.2, top_k=20)
    assert deep["decision"] == "SUGGESTED" and deep["suggested"]["image_id"] == 99


def test_deeper_top_k_never_admits_a_lookalike():
    """Depth changes what is considered, never what is allowed."""
    ranked = [(img(i, "gray_wolf", "canid"), 0.4) for i in range(30)]
    d = decide_post(FOX_POST, ranked, threshold=0.2, top_k=30)
    assert d["decision"] == "NO_CONFIDENT_MATCH"
    assert all("Look-alike rejected" in v["reasons"][0] for v in d["candidates"])


import pytest

from scripts.v2.match_eval import QUERY_MODES, build_queries, subject_phrase

SEASONAL = {"slug": "brown-bear-3", "title": "Tracking the brown bear through the year",
            "body": "Where it goes as the weather turns and why sightings cluster at dawn and dusk.",
            "target_subject": "brown_bear", "target_family": "ursid"}
TAX2 = {"classes": [{"slug": "brown_bear", "common_name": "Brown bear",
                     "scientific_name": "Ursus arctos", "family": "ursid"}]}


def test_subject_phrase_is_short_and_taxonomic():
    phrase = subject_phrase(SEASONAL, TAX2)
    assert "brown bear" in phrase and "Ursus arctos" in phrase
    assert len(phrase.split()) < 12      # BioCLIP was trained on short captions


def test_refusal_posts_have_no_subject_phrase():
    assert subject_phrase(OTHER_POST, TAX2) is None


def test_query_modes_differ():
    full, _ = build_queries(SEASONAL, TAX2, "full_post")
    title, _ = build_queries(SEASONAL, TAX2, "title_only")
    subj, _ = build_queries(SEASONAL, TAX2, "subject_query")
    assert "dawn and dusk" in full and "dawn and dusk" not in title
    assert "Ursus arctos" in subj


def test_hybrid_returns_both_parts():
    primary, secondary = build_queries(SEASONAL, TAX2, "hybrid")
    assert "dawn and dusk" in primary and "Ursus arctos" in secondary


def test_hybrid_falls_back_for_refusal_posts():
    primary, secondary = build_queries(OTHER_POST, TAX2, "hybrid")
    assert secondary is None and primary.startswith(OTHER_POST["title"])


def test_unknown_mode_rejected():
    with pytest.raises(ValueError):
        build_queries(SEASONAL, TAX2, "vibes")


def test_every_mode_is_usable():
    for mode in QUERY_MODES:
        primary, _ = build_queries(SEASONAL, TAX2, mode)
        assert primary
