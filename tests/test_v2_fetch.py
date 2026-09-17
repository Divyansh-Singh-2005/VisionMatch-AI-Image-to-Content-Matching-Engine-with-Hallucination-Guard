from scripts.v2 import fetch_inat as f

CLS = {"slug": "red_fox", "family": "canid", "taxon_id": 42069}


def obs(photos, user="alice"):
    return {"id": 7, "observed_on": "2024-05-01", "user": {"login": user}, "photos": photos}


def test_medium_url_rewrites_size_and_drops_query():
    url = "https://inaturalist-open-data.s3.amazonaws.com/photos/123/square.jpeg?1699"
    assert f.medium_url(url) == "https://inaturalist-open-data.s3.amazonaws.com/photos/123/medium.jpeg"


def test_image_path_is_neutral_and_stable():
    p = f.image_path(123456)
    assert p == f.image_path(123456)
    assert p.name == "123456.jpg" and "fox" not in p.as_posix()
    assert len(p.parent.name) == 2


def test_pick_photo_skips_unlicensed():
    photos = [
        {"id": 1, "license_code": None, "url": "u/square.jpg"},
        {"id": 2, "license_code": "cc-by-nd", "url": "u/square.jpg"},
        {"id": 3, "license_code": "CC-BY", "url": "u/square.jpg"},
    ]
    assert f.pick_photo(obs(photos))["id"] == 3
    assert f.pick_photo(obs(photos[:2])) is None


def test_record_fields():
    rec = f.to_record(obs([{"id": 9, "license_code": "cc0", "url": "https://x/photos/9/square.jpg",
                            "attribution": "no rights reserved"}]), CLS)
    assert rec["class"] == "red_fox" and rec["family"] == "canid" and rec["license"] == "cc0"
    assert rec["url"].endswith("/9/medium.jpg")
    assert rec["obs_url"].endswith("/observations/7")


def test_manifest_roundtrip_is_deterministic(tmp_path):
    path = tmp_path / "m.jsonl.gz"
    rows = [{"class": "b", "photo_id": 2}, {"class": "a", "photo_id": 5}, {"class": "a", "photo_id": 1}]
    f.save_manifest(rows, path)
    first = path.read_bytes()
    f.save_manifest(list(reversed(rows)), path)
    assert path.read_bytes() == first
    assert [r["photo_id"] for r in f.load_manifest(path)] == [1, 5, 2]

BISON = {"slug": "american_bison", "scientific_name": "Bison bison", "common_name": "American bison"}


def test_taxon_match_by_synonym_and_common_name():
    renamed = {"name": "Bos bison", "rank": "species", "is_active": True, "matched_term": "Bison bison"}
    by_common = {"name": "Bos bison", "rank": "species", "preferred_common_name": "American Bison"}
    assert f.taxon_matches(renamed, BISON)
    assert f.taxon_matches(by_common, BISON)


def test_taxon_match_rejects_genus_and_inactive():
    assert not f.taxon_matches({"name": "Bison bison", "rank": "genus"}, BISON)
    assert not f.taxon_matches({"name": "Bison bison", "rank": "species", "is_active": False}, BISON)
