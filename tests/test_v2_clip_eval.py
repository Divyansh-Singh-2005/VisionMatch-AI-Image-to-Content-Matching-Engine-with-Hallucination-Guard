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