"""MediaService resolves artifacts only through a manifest."""

from pathlib import Path

import pytest

from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.media.models import (
    MediaArtifact,
    MediaKind,
    MediaManifest,
    MediaOutcome,
    MediaType,
)
from ai_trend_pipeline.media.storage import write_artifact
from ai_trend_pipeline.services.media import MediaArtifactNotFound, MediaService
from tests.media_fixtures import make_artifact, make_manifest

DATA = b"\x89PNG\r\n\x1a\nservice"


def _static_manifest(root: Path) -> tuple[MediaArtifact, MediaManifest]:
    stored = write_artifact(root, DATA, MediaType.PNG)
    artifact = make_artifact(
        MediaKind.STATIC,
        content_hash=stored.content_hash,
        relative_path=stored.relative_path,
        byte_size=stored.byte_size,
    )
    return artifact, make_manifest(MediaOutcome.STATIC, static_artifact=artifact)


def test_artifact_resolves_by_manifest_id(tmp_path: Path) -> None:
    artifact, manifest = _static_manifest(tmp_path)
    served = MediaService(Settings(media_dir=str(tmp_path))).artifact(
        manifest, artifact.artifact_id
    )
    assert served.media_type == "image/png"
    assert served.path.read_bytes() == DATA


def test_unknown_id_missing_file_and_text_only_manifest_raise_not_found(tmp_path: Path) -> None:
    artifact, manifest = _static_manifest(tmp_path)
    service = MediaService(Settings(media_dir=str(tmp_path)))
    with pytest.raises(MediaArtifactNotFound):
        service.artifact(manifest, "art-v1-0000000000000000")
    with pytest.raises(MediaArtifactNotFound):
        service.artifact(make_manifest(MediaOutcome.TEXT_ONLY), artifact.artifact_id)
    (tmp_path / artifact.relative_path).unlink()
    with pytest.raises(MediaArtifactNotFound):
        service.artifact(manifest, artifact.artifact_id)


@pytest.mark.asyncio
async def test_preview_resolves_only_manifest_listed_artifacts(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from ai_trend_pipeline.services.media import StoryMediaPreviewService
    from tests.test_web_story_detail import FakeDetailService, make_detail, make_package

    artifact, manifest = _static_manifest(tmp_path)
    media = MediaService(Settings(media_dir=str(tmp_path)))
    now = datetime(2026, 10, 30, tzinfo=UTC)

    def service_for(package_media: MediaManifest | None, *, package: bool = True):
        detail = make_detail(package=make_package(media=package_media) if package else None)
        return StoryMediaPreviewService(FakeDetailService(detail), media)  # pyright: ignore[reportArgumentType]

    served = await service_for(manifest).preview("d", artifact.artifact_id, now=now)
    assert served.path.read_bytes() == DATA and served.media_type == "image/png"
    other = "art-v1-0000000000000000"
    for bad in (other, "../etc/passwd", "x.png", "art-v1-ZZZZZZZZZZZZZZZZ"):
        with pytest.raises(MediaArtifactNotFound):
            await service_for(manifest).preview("d", bad, now=now)
    with pytest.raises(MediaArtifactNotFound):
        await service_for(None, package=False).preview("d", artifact.artifact_id, now=now)
    with pytest.raises(MediaArtifactNotFound):
        await service_for(None).preview("d", artifact.artifact_id, now=now)
