import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db, get_tenant_id
from app.main import app


def _no_db():
    yield None


@pytest.fixture(scope="module")
def client():
    app.dependency_overrides[get_db] = _no_db
    app.dependency_overrides[get_tenant_id] = lambda: 1
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


KEY = {"Idempotency-Key": "test-key-123"}


def test_review_rejects_unknown_action(client):
    assert client.post("/suggestions/1/review", json={"action": "maybe"}, headers=KEY).status_code == 422


def test_review_requires_idempotency_key(client):
    assert client.post("/suggestions/1/review", json={"action": "approve"}).status_code == 422


def test_review_rejects_short_idempotency_key(client):
    r = client.post("/suggestions/1/review", json={"action": "approve"}, headers={"Idempotency-Key": "abc"})
    assert r.status_code == 422


def test_review_rejects_extra_fields(client):
    r = client.post("/suggestions/1/review", json={"action": "approve", "approved_by": "me"}, headers=KEY)
    assert r.status_code == 422


def test_review_rejects_long_note(client):
    r = client.post("/suggestions/1/review", json={"action": "reject", "note": "x" * 501}, headers=KEY)
    assert r.status_code == 422


@pytest.mark.parametrize("path", ["/jobs/0", "/images/abc", "/suggestions/-1", "/posts/Bad_Slug!/images"])
def test_bad_path_params(client, path):
    assert client.get(path).status_code == 422


@pytest.mark.parametrize("query", ["limit=0", "limit=501", "status=weird", "subject=unicorn", "offset=-1"])
def test_bad_image_queries(client, query):
    assert client.get(f"/images?{query}").status_code == 422


def test_tag_job_body_validation(client):
    assert client.post("/jobs/tag-images", json={"limit": 0}).status_code == 422
    assert client.post("/jobs/tag-images", json={"retag": True}).status_code == 422


def test_embed_job_kind_validation(client):
    assert client.post("/jobs/embed", json={"kind": "videos"}).status_code == 422