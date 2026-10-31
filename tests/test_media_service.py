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
