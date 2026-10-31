"""GET /stories/{id}/media/{artifact_id} with a fake detail service and a tmp media dir."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.media.models import MediaKind, MediaOutcome, MediaType
from ai_trend_pipeline.media.storage import write_artifact
from ai_trend_pipeline.services.stories import (
    StoryDetailExpired,
    StoryDetailMalformed,
    StoryDetailNotFound,
    StoryDetailUnavailable,
)
from ai_trend_pipeline.web.app import create_app
from ai_trend_pipeline.web.dependencies import get_story_detail_service
from tests.media_fixtures import make_artifact, make_manifest
from tests.test_web_story_detail import STORY_ID, FakeDetailService, make_detail, make_package

DATA = b"\x89PNG\r\n\x1a\nweb-preview"


def _setup(
    tmp_path: Path, *, with_media: bool = True, error: Exception | None = None
) -> tuple[TestClient, str]:
    stored = write_artifact(tmp_path, DATA, MediaType.PNG)
    artifact = make_artifact(
        MediaKind.STATIC,
        content_hash=stored.content_hash,
        relative_path=stored.relative_path,
        byte_size=stored.byte_size,
    )
    manifest = make_manifest(MediaOutcome.STATIC, static_artifact=artifact)
    package = make_package(media=manifest if with_media else None)
    service = FakeDetailService(make_detail(package=package), show_error=error)
    app = create_app(Settings(media_dir=str(tmp_path)))

    async def fake() -> FakeDetailService:
        return service

    app.dependency_overrides[get_story_detail_service] = fake
    return TestClient(app), artifact.artifact_id


def test_serves_manifest_artifact_with_mime_and_nosniff(tmp_path: Path) -> None:
    client, artifact_id = _setup(tmp_path)
    response = client.get(f"/stories/{STORY_ID}/media/{artifact_id}")
    assert response.status_code == 200
    assert response.content == DATA
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "default-src 'none'"
    assert response.headers["cache-control"] == "private, max-age=3600"


@pytest.mark.parametrize(
    "artifact_id", ["art-v1-0000000000000000", "art-v1-xyz", "ART-V1-0123456789ABCDEF", "abc"]
)
def test_unknown_malformed_or_foreign_artifact_id_is_404(tmp_path: Path, artifact_id: str) -> None:
    client, _ = _setup(tmp_path)
    response = client.get(f"/stories/{STORY_ID}/media/{artifact_id}")
    assert response.status_code == 404
    assert str(tmp_path) not in response.text


def test_missing_file_or_text_only_story_is_404(tmp_path: Path) -> None:
    client, artifact_id = _setup(tmp_path)
    for path in tmp_path.iterdir():
        path.unlink()
    assert client.get(f"/stories/{STORY_ID}/media/{artifact_id}").status_code == 404
    client, artifact_id = _setup(tmp_path / "other", with_media=False)
    assert client.get(f"/stories/{STORY_ID}/media/{artifact_id}").status_code == 404


def test_missing_story_is_404_and_unavailable_is_503(tmp_path: Path) -> None:
    for error, status in [
        (StoryDetailNotFound("x"), 404),
        (StoryDetailExpired("x"), 404),
        (StoryDetailMalformed("x"), 404),
        (StoryDetailUnavailable(), 503),
    ]:
        client, artifact_id = _setup(tmp_path, error=error)
        response = client.get(f"/stories/{STORY_ID}/media/{artifact_id}")
        assert response.status_code == status
        assert str(tmp_path) not in response.text


@pytest.mark.parametrize("artifact_id", ["..%2F..%2Fetc%2Fpasswd", "x.png"])
def test_path_like_artifact_ids_are_404(tmp_path: Path, artifact_id: str) -> None:
    client, _ = _setup(tmp_path)
    assert client.get(f"/stories/{STORY_ID}/media/{artifact_id}").status_code == 404
