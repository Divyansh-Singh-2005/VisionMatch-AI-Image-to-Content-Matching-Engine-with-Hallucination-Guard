import pytest

from scripts.v2.clip_eval import build_class_prompts, plan_rows, summarize

TAX = {"classes": [
    {"slug": "red_fox", "family": "canid"},
    {"slug": "gray_wolf", "family": "canid"},
    {"slug": "brown_bear", "family": "ursid"},
]}

PREDS = [
    {"class": "red_fox", "pred": "red_fox", "confidence": 0.90},
    {"class": "red_fox", "pred": "gray_wolf", "confidence": 0.40},
    {"class": "gray_wolf", "pred": "gray_wolf", "confidence": 0.80},
    {"class": "brown_bear", "pred": "other", "confidence": 0.70, "reject_reason": "tracks"},
]


def test_prompts_use_common_and_scientific_names():
    prompts = build_class_prompts({"common_name": "Red fox", "scientific_name": "Vulpes vulpes"})
    assert len(prompts) == 10
    assert "a photo of a red fox." in prompts
    assert "a photo of a Vulpes vulpes." in prompts


def test_plan_rows_takes_first_n_per_class():
    rows = [{"class": c, "photo_id": i} for i, c in enumerate("aaabbbc")]
    assert [r["photo_id"] for r in plan_rows(rows, 2)] == [0, 1, 3, 4, 6]
    assert len(plan_rows(rows, None)) == 7


def test_summary_metrics():
    s = summarize(PREDS, TAX)
    assert s["top1"] == 0.5
    assert s["family_top1"] == 0.75
    assert s["wrong_species"] == 1
    assert s["within_family_error_share"] == 1.0
    assert s["predicted_non_animal"] == 1
    assert s["non_animal_reasons"] == {"tracks": 1}
    assert s["top_confusions"] == [["red_fox", "gray_wolf", 1]]
    assert s["per_class"]["red_fox"] == {"n": 2, "top1": 0.5}


def test_flag_threshold_is_lowest_cutoff_meeting_target():
    s = summarize(PREDS, TAX, target=0.95)
    assert s["flag_threshold"] == pytest.approx(0.45)
    assert s["coverage_at_flag_threshold"] == 0.5
    assert s["curve"][0]["accuracy"] == pytest.approx(0.6667, abs=1e-4)


def test_empty_predictions_rejected():
    with pytest.raises(ValueError):
        summarize([], TAX)

from scripts.v2.clip_eval import aggregate, decide, prompt_groups

FOX = {"slug": "red_fox", "common_name": "Red fox", "scientific_name": "Vulpes vulpes", "family": "canid"}


def test_strategies_differ_in_shape():
    assert len(prompt_groups(FOX, "both_mean")) == 1 and len(prompt_groups(FOX, "both_mean")[0]) == 10
    assert len(prompt_groups(FOX, "common")) == 1 and len(prompt_groups(FOX, "common")[0]) == 5
    assert len(prompt_groups(FOX, "both_split")) == 2
    assert len(prompt_groups(FOX, "descriptive_split")) == 3


def test_descriptive_prompts_include_family_hint():
    assert any("wild dog-like animal" in p for p in prompt_groups(FOX, "descriptive")[0])


def test_unknown_strategy_rejected():
    with pytest.raises(ValueError):
        prompt_groups(FOX, "nope")


def test_aggregate_sums_rows_of_the_same_class():
    classes, rejects = aggregate([0.3, 0.25, 0.2, 0.25], ["red_fox", "red_fox", "gray_wolf", "reject:tracks"])
    assert classes["red_fox"] == pytest.approx(0.55)
    assert rejects == {"tracks": pytest.approx(0.25)}


def test_reject_needs_to_beat_the_margin():
    classes, rejects = {"red_fox": 0.30, "gray_wolf": 0.10}, {"tracks": 0.40}
    assert decide(classes, rejects, 1.0)["pred"] == "other"       # 0.40 > 0.30
    assert decide(classes, rejects, 2.0)["pred"] == "red_fox"     # 0.40 < 0.60
    assert decide(classes, rejects, 2.0)["reject_reason"] is None


def test_decide_reports_margin_and_top3():
    d = decide({"red_fox": 0.5, "gray_wolf": 0.2, "dog": 0.1}, {"tracks": 0.05}, 2.0)
    assert d["pred"] == "red_fox" and d["animal_margin"] == pytest.approx(0.3)
    assert [k for k, _ in d["top3"]] == ["red_fox", "gray_wolf", "dog"]


from scripts.v2.clip_eval import decide_two_stage, softmax


def test_softmax_sums_to_one_and_ranks():
    p = softmax([0.1, 0.3, 0.2], scale=10)
    assert sum(p) == pytest.approx(1.0)
    assert p[1] > p[2] > p[0]


def test_species_stage_ignores_reject_prompts():
    """Background scoring high must not change which species wins."""
    cls = {"red_fox": 0.30, "gray_wolf": 0.22}
    quiet = decide_two_stage(cls, {"no_animal": 0.05}, reject_threshold=0.9)
    loud = decide_two_stage(cls, {"no_animal": 0.29}, reject_threshold=0.9)
    assert quiet["pred_animal"] == loud["pred_animal"] == "red_fox"
    assert quiet["confidence"] == loud["confidence"]


def test_reject_needs_clear_dominance():
    cls = {"red_fox": 0.30}
    assert decide_two_stage(cls, {"tracks": 0.31}, reject_threshold=0.9)["pred"] == "red_fox"
    hard = decide_two_stage(cls, {"tracks": 0.45}, reject_threshold=0.9)
    assert hard["pred"] == "other" and hard["reject_reason"] == "tracks"


def test_reject_probability_is_reported_even_when_not_rejected():
    d = decide_two_stage({"red_fox": 0.30}, {"tracks": 0.25}, reject_threshold=0.99)
    assert d["pred"] == "red_fox" and 0.0 < d["reject_prob"] < 0.99


def test_no_reject_prompts_means_never_rejected():
    d = decide_two_stage({"red_fox": 0.3, "dog": 0.1}, {}, reject_threshold=0.5)
    assert d["pred"] == "red_fox" and d["reject_prob"] == 0.0


from scripts.v2.audit_plan import BUCKETS, plan_summary, select_audit


def _bio(pid, cls, fam, pred, pred_fam, conf):
    return {"photo_id": pid, "class": cls, "family": fam, "pred": pred, "pred_family": pred_fam,
            "pred_animal": pred, "confidence": conf, "top3": [[pred, conf]]}


BIO = [
    _bio(1, "red_fox", "canid", "brown_bear", "ursid", 0.9),    # family disagreement
    _bio(2, "red_fox", "canid", "gray_wolf", "canid", 0.9),     # species disagreement
    _bio(3, "red_fox", "canid", "red_fox", "canid", 0.2),       # low confidence
    _bio(4, "red_fox", "canid", "red_fox", "canid", 0.9),       # cross-model non-animal
    _bio(5, "red_fox", "canid", "red_fox", "canid", 0.9),       # control
]
VIT = [{"photo_id": 4, "pred": "other", "reject_reason": "tracks"},
       {"photo_id": 5, "pred": "red_fox", "reject_reason": None}]


def test_each_image_lands_in_exactly_one_bucket():
    plan = select_audit(BIO, VIT, flag_threshold=0.5, per_bucket=10)
    assert [i["bucket"] for i in plan] == list(BUCKETS)
    assert len({i["photo_id"] for i in plan}) == 5


def test_sampling_is_deterministic_and_capped():
    big = [_bio(i, "red_fox", "canid", "gray_wolf", "canid", 0.9) for i in range(100, 200)]
    a = select_audit(big, [], flag_threshold=0.5, per_bucket=10, seed=7)
    b = select_audit(big, [], flag_threshold=0.5, per_bucket=10, seed=7)
    assert len(a) == 10 and [i["photo_id"] for i in a] == [i["photo_id"] for i in b]
    assert a[0]["pool_size"] == 100


def test_rejected_images_are_not_called_family_disagreements():
    plan = select_audit([{**_bio(9, "red_fox", "canid", "other", "other", 0.9)}], [],
                        flag_threshold=0.5, per_bucket=5)
    assert plan[0]["bucket"] == "cross_model_non_animal" or plan[0]["bucket"] == "control_random"


def test_plan_summary_counts():
    plan = select_audit(BIO, VIT, flag_threshold=0.5, per_bucket=10)
    s = plan_summary(plan, bio_total=5)
    assert s["audit_calls"] == 5 and s["buckets"]["low_confidence"]["sampled"] == 1
