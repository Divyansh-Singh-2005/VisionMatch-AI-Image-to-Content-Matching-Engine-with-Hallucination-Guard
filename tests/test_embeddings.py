import math

import pytest

from app.services.embeddings import estimate_tokens, normalize, text_hash


def test_normalize_unit_length():
    v = normalize([3.0, 4.0])
    assert math.isclose(math.sqrt(sum(x * x for x in v)), 1.0)


def test_normalize_rejects_zero_vector():
    with pytest.raises(ValueError):
        normalize([0.0, 0.0])


def test_text_hash_stable_and_sensitive():
    assert text_hash("red fox") == text_hash("red fox")
    assert text_hash("red fox") != text_hash("red  fox")


def test_estimate_tokens_minimum_one():
    assert estimate_tokens("") == 1
    assert estimate_tokens("abcdefgh") == 2