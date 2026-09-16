import pytest

from app.jobs.tagging import classify_api_error


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("429 RESOURCE_EXHAUSTED {'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier'}", "quota_daily"),
        ("429 RESOURCE_EXHAUSTED {'quotaId': 'GenerateRequestsPerMinutePerProjectPerModel-FreeTier'}", "rate_limit"),
        ("503 UNAVAILABLE. This model is currently experiencing high demand.", "transient"),
        ("400 INVALID_ARGUMENT. Unsupported model.", "other"),
    ],
)
def test_classify_api_error(message, expected):
    kind, _ = classify_api_error(Exception(message))
    assert kind == expected


def test_quota_id_is_extracted():
    _, detail = classify_api_error(
        Exception("429 {'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier'}")
    )
    assert detail == "429 quota=GenerateRequestsPerDayPerProjectPerModel-FreeTier"