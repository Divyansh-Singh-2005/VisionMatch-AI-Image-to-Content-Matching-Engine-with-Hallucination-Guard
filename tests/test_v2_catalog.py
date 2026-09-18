from scripts.v2.catalog import (
    STATUS_NO_ANIMAL, STATUS_OK, STATUS_REVIEW, build_catalog, catalog_stats, classify_image,
)

FAMILY = {"red_fox": "canid", "gray_wolf": "canid", "brown_bear": "ursid", "deer": "cervid"}


def bio(pid, label, pred, conf=0.8):
    return {"photo_id": pid, "class": label, "pred": pred, "pred_animal": pred, "confidence": conf}


def test_family_agreement_verifies_the_label():
    c = classify_image(bio(1, "red_fox", "gray_wolf"), None, FAMILY)
    assert c["status"] == STATUS_OK          # same family: a look-alike is not a rejection
    assert c["subject"] == "red_fox"         # the community label is what gets stored
    assert c["species_agrees"] is False and c["family_agrees"] is True


def test_family_mismatch_needs_review():
    c = classify_image(bio(2, "red_fox", "brown_bear"), None, FAMILY)
    assert c["status"] == STATUS_REVIEW and "canid" in c["reason"] and "ursid" in c["reason"]


def test_content_gate_overrides_everything():
    """Audit: generic CLIP was right on 96% of its non-animal calls."""
    v = {"photo_id": 3, "pred": "other", "reject_reason": "tracks"}
    c = classify_image(bio(3, "red_fox", "red_fox", conf=0.99), v, FAMILY)
    assert c["status"] == STATUS_NO_ANIMAL and "tracks" in c["reason"]


def test_low_confidence_alone_does_not_flag():
    """Audit: 93% of low-confidence predictions were still correct."""
    c = classify_image(bio(4, "red_fox", "red_fox", conf=0.05), None, FAMILY)
    assert c["status"] == STATUS_OK


def test_model_subject_is_recorded_but_never_stored_as_the_subject():
    c = classify_image(bio(5, "red_fox", "gray_wolf"), None, FAMILY)
    assert c["model_subject"] == "gray_wolf" and c["subject"] == "red_fox"


def test_catalog_stats_counts_usable_images():
    rows = [bio(1, "red_fox", "red_fox"), bio(2, "red_fox", "brown_bear"), bio(3, "red_fox", "red_fox")]
    vit = [{"photo_id": 3, "pred": "other", "reject_reason": "scat"}]
    stats = catalog_stats(build_catalog(rows, vit, FAMILY))
    assert stats["images"] == 3
    assert stats["status"] == {STATUS_OK: 1, STATUS_REVIEW: 1, STATUS_NO_ANIMAL: 1}
    assert stats["usable_share"] == 0.3333
    assert stats["usable_per_family"] == {"canid": 1}