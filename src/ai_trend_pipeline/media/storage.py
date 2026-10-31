"""Content-addressed, atomic artifact storage and safe manifest-based path resolution."""

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ai_trend_pipeline.media.models import (
    MEDIA_EXTENSIONS,
    MediaArtifact,
    MediaType,
    sha256_hex,
)


class MediaArtifactMissing(Exception):
    """The artifact file is absent, the wrong size, or outside the media root."""


@dataclass(frozen=True)
class StoredArtifact:
    content_hash: str
    relative_path: str
    byte_size: int


def write_artifact(media_root: Path, data: bytes, media_type: MediaType) -> StoredArtifact:
    root = media_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    content_hash = sha256_hex(data)
    relative_path = f"{content_hash}.{MEDIA_EXTENSIONS[media_type]}"
    stored = StoredArtifact(content_hash, relative_path, len(data))
    target = root / relative_path
    if target.is_file() and target.stat().st_size == len(data):
        return stored
    fd, tmp_name = tempfile.mkstemp(dir=root, prefix=".media-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp_name, target)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return stored


def resolve_artifact_path(media_root: Path, artifact: MediaArtifact) -> Path:
    root = media_root.resolve()
    path = (root / artifact.relative_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise MediaArtifactMissing(artifact.artifact_id)
    if path.stat().st_size != artifact.byte_size:
        raise MediaArtifactMissing(artifact.artifact_id)
    return path
