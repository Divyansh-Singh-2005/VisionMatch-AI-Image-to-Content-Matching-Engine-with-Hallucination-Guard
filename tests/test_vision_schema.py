import json

import pytest
from pydantic import ValidationError

from app.schemas.vision import ImageTags

VALID = {
    "subject": "red fox",
    "subject_canonical": "red_fox",
    "category": "animal",
    "attributes": ["orange fur", "wild", "forest"],
    "caption": "A red fox standing in a forest",
    "confidence": 0.94,
}


def test_valid_payload_accepted():
    tags = ImageTags.model_validate_json(json.dumps(VALID))
    assert tags.subject_canonical.value == "red_fox"


@pytest.mark.parametrize("raw", ["not json", "", '{"subject": "fox"}', "[]"])
def test_malformed_output_rejected(raw):
    with pytest.raises(ValidationError):
        ImageTags.model_validate_json(raw)


@pytest.mark.parametrize(
    "patch",
    [
        {"confidence": 1.4},
        {"confidence": -0.1},
        {"subject_canonical": "wolf"},
        {"category": "vehicle"},
        {"attributes": []},
        {"caption": "fox"},
        {"unexpected": "field"},
    ],
)
def test_invalid_fields_rejected(patch):
    with pytest.raises(ValidationError):
        ImageTags.model_validate_json(json.dumps({**VALID, **patch}))


def test_animal_subject_requires_animal_category():
    with pytest.raises(ValidationError):
        ImageTags.model_validate_json(json.dumps({**VALID, "category": "landscape"}))


def test_attributes_normalised_and_deduped():
    tags = ImageTags.model_validate_json(
        json.dumps({**VALID, "attributes": ["Orange  Fur", "orange fur", "Forest"]})
    )
    assert tags.attributes == ["orange fur", "forest"]