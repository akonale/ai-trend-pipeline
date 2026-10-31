"""Serve a stored media artifact by ID, looked up only through its manifest."""

from dataclasses import dataclass
from pathlib import Path

from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.media.models import MediaManifest, MediaType
from ai_trend_pipeline.media.storage import MediaArtifactMissing, resolve_artifact_path


class MediaArtifactNotFound(Exception):
    pass


@dataclass(frozen=True)
class ServedArtifact:
    path: Path
    media_type: str


class MediaService:
    def __init__(self, settings: Settings) -> None:
        self._media_root = Path(settings.media_dir)

    def artifact(self, manifest: MediaManifest, artifact_id: str) -> ServedArtifact:
        artifact = manifest.artifact(artifact_id)
        if artifact is None or artifact.media_type not in set(MediaType):
            raise MediaArtifactNotFound(artifact_id)
        try:
            path = resolve_artifact_path(self._media_root, artifact)
        except MediaArtifactMissing as exc:
            raise MediaArtifactNotFound(artifact_id) from exc
        return ServedArtifact(path=path, media_type=artifact.media_type.value)
