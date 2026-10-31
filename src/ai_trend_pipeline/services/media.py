"""Serve a stored media artifact by ID, looked up only through its manifest."""

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.media.models import ARTIFACT_ID_PATTERN, MediaManifest, MediaType
from ai_trend_pipeline.media.storage import MediaArtifactMissing, resolve_artifact_path
from ai_trend_pipeline.services.stories import (
    StoryDetailExpired,
    StoryDetailMalformed,
    StoryDetailNotFound,
    StoryDetailService,
)


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


class StoryMediaPreviewService:
    """Serve an artifact only when the story's own manifest lists it."""

    def __init__(self, details: StoryDetailService, media: MediaService) -> None:
        self._details = details
        self._media = media

    async def preview(self, detail_id: str, artifact_id: str, *, now: datetime) -> ServedArtifact:
        if re.fullmatch(ARTIFACT_ID_PATTERN, artifact_id) is None:
            raise MediaArtifactNotFound(artifact_id)
        try:
            detail = await self._details.show(detail_id, now=now)
        except (StoryDetailNotFound, StoryDetailExpired, StoryDetailMalformed) as exc:
            raise MediaArtifactNotFound(artifact_id) from exc
        if detail.package is None or detail.package.media is None:
            raise MediaArtifactNotFound(artifact_id)
        return self._media.artifact(detail.package.media, artifact_id)
