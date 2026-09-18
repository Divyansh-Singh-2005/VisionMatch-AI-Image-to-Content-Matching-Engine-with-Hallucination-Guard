import pytest
from pydantic import ValidationError

from scripts.v2.audit_score import build_prompt, format_audit, score_audit, verdict_model

TAX = {"classes": [
    {"slug": "red_fox", "common_name": "Red fox", "scientific_name": "Vulpes vulpes", "family": "canid"},
    {"slug": "gray_wolf", "common_name": "Gray wolf", "scientific_name": "Canis lupus", "family": "canid"},
    {"slug": "brown_bear", "common_name": "Brown bear", "scientific_name": "Ursus arctos", "family": "ursid"},
]}
V = verdict_model(TAX)


def j(**kw):
    base = {"content": "live_animal", "species": "red_fox", "confidence": 0.9, "reason": "orange fox"}
    return __import__("json").dumps({**base, **kw})


def test_prompt_lists_every_species_with_both_names():
    p = build_prompt(TAX)
    assert "red_fox (Red fox, Vulpes vulpes)" in p
    assert "{subjects}" not in p


def test_valid_verdict():
    v = V.model_validate_json(j())
    assert v.species == "red_fox" and v.content.value == "live_animal"


@pytest.mark.parametrize("bad", [
    j(species="wolf"),                       # not in the taxonomy
    j(confidence=1.5),
    j(content="maybe"),
    j(reason=""),
    '{"content": "live_animal", "species": "red_fox", "confidence": 0.9}',   # missing reason
    j(extra="x"),
])
def test_invalid_verdicts_rejected(bad):
    with pytest.raises(ValidationError):
        V.model_validate_json(bad)


def test_non_animal_content_must_use_other():
    with pytest.raises(ValidationError):
        V.model_validate_json(j(content="tracks", species="red_fox"))
    assert V.model_validate_json(j(content="tracks", species="other")).species == "other"


def test_remains_may_name_a_species():
    assert V.model_validate_json(j(content="remains", species="red_fox")).content.value == "remains"


RESULTS = [
    {"bucket": "family_disagreement", "label_class": "red_fox", "label_family": "canid",
     "bio_pred": "brown_bear", "vit_pred": "red_fox", "content": "live_animal", "species": "red_fox",
     "confidence": 0.9},
    {"bucket": "family_disagreement", "label_class": "red_fox", "label_family": "canid",
     "bio_pred": "brown_bear", "vit_pred": "red_fox", "content": "live_animal", "species": "brown_bear",
     "confidence": 0.8},
    {"bucket": "cross_model_non_animal", "label_class": "red_fox", "label_family": "canid",
     "bio_pred": "red_fox", "vit_pred": "other", "content": "tracks", "species": "other", "confidence": 0.3},
]


def test_scoring_splits_label_and_model_agreement():
    b = score_audit(RESULTS, TAX)["family_disagreement"]
    assert b["n"] == 2 and b["rates"]["live_animal"] == 1.0
    assert b["rates"]["label_species"] == 0.5     # one verdict backs the label
    assert b["rates"]["bio_species"] == 0.5       # the other backs BioCLIP
    assert b["rates"]["label_family"] == 0.5


def test_non_animal_bucket_credits_generic_clip():
    b = score_audit(RESULTS, TAX)["cross_model_non_animal"]
    assert b["llm_vs_vit_non_animal"] == 1
    assert b["rates"]["live_animal"] == 0.0
    assert b["content"] == {"tracks": 1}


def test_format_audit_has_a_row_per_bucket():
    out = format_audit(score_audit(RESULTS, TAX), {"calls": 3, "ok": 3, "failed": 0, "est_cost_usd": 0.001})
    assert "family_disagreement" in out and "cross_model_non_animal" in out

from scripts.v2.run_audit import classify


@pytest.mark.parametrize(("message", "expected"), [
    ("503 UNAVAILABLE. The service is currently unavailable.", "transient"),
    ("500 INTERNAL", "transient"),
    ("429 {'quotaId': 'GenerateRequestsPerMinutePerProjectPerModel-FreeTier'}", "rate_limit"),
    ("429 {'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier'}", "quota_daily"),
    ("400 INVALID_ARGUMENT", "other"),
])
def test_classify_audit_errors(message, expected):
    assert classify(Exception(message))[0] == expected


def test_transient_errors_are_retryable_not_fatal():
    """A busy service must not be treated like a broken pipeline."""
    assert classify(Exception("503 UNAVAILABLE"))[0] not in ("other", "quota_daily")


from scripts.v2.audit_score import AuditVerdictLLM, extract_json, normalise_verdict


def test_llm_schema_is_loose_enough_for_the_api():
    """The API enforces this shape; the strict model does the real checking afterwards."""
    fields = set(AuditVerdictLLM.model_fields)
    assert fields == {"content", "species", "confidence", "reason"}


def test_extract_json_handles_fences_and_prose():
    assert extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert extract_json('Here is the answer:\n{"a": 1}\nHope that helps.') == '{"a": 1}'
    assert extract_json('{"a": 1}') == '{"a": 1}'
    assert extract_json("") == ""


def test_normalise_repairs_the_shapes_the_model_returned():
    once = normalise_verdict('[{"content": "live_animal", "species": "red_fox", '
                             '"confidence": 0.9, "reason": "a fox"}]')
    assert V.model_validate_json(once).species == "red_fox"
    joined = normalise_verdict('{"content": "live_animal", "species": "red_fox", '
                               '"confidence": 0.9, "reason": ["orange fur", "bushy tail"]}')
    assert V.model_validate_json(joined).reason == "orange fur bushy tail"


def test_normalise_passes_unparseable_text_through_for_a_clean_error():
    with pytest.raises(ValidationError):
        V.model_validate_json(normalise_verdict("I cannot identify this image."))
