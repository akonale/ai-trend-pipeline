"""Content-addressed atomic storage and safe path resolution."""

import hashlib
import os
from pathlib import Path

import pytest

from ai_trend_pipeline.media import storage
from ai_trend_pipeline.media.models import MediaKind, MediaType
from ai_trend_pipeline.media.storage import (
    MediaArtifactMissing,
    resolve_artifact_path,
    write_artifact,
)
from tests.media_fixtures import make_artifact

DATA = b"\x89PNG\r\n\x1a\nhello"


def test_write_is_content_addressed_and_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "media"
    first = write_artifact(root, DATA, MediaType.PNG)
    second = write_artifact(root, DATA, MediaType.PNG)
    digest = hashlib.sha256(DATA).hexdigest()
    assert first == second
    assert first.relative_path == f"{digest}.png"
    assert first.byte_size == len(DATA)
    assert [p.name for p in root.iterdir()] == [f"{digest}.png"]
    assert (root / first.relative_path).read_bytes() == DATA


def test_failed_write_removes_temp_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(src: object, dst: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(storage.os, "replace", boom)
    with pytest.raises(OSError):
        write_artifact(tmp_path, DATA, MediaType.PNG)
    assert list(tmp_path.iterdir()) == []


def test_resolve_rejects_missing_file_size_mismatch_and_escaping_symlink(tmp_path: Path) -> None:
    root = tmp_path / "media"
    stored = write_artifact(root, DATA, MediaType.PNG)
    artifact = make_artifact(
        content_hash=stored.content_hash,
        relative_path=stored.relative_path,
        byte_size=stored.byte_size,
    )
    assert resolve_artifact_path(root, artifact) == (root / stored.relative_path).resolve()

    with pytest.raises(MediaArtifactMissing):
        resolve_artifact_path(root, artifact.model_copy(update={"byte_size": 3}))

    (root / stored.relative_path).unlink()
    with pytest.raises(MediaArtifactMissing):
        resolve_artifact_path(root, artifact)

    outside = tmp_path / "outside.png"
    outside.write_bytes(DATA)
    os.symlink(outside, root / stored.relative_path)
    with pytest.raises(MediaArtifactMissing):
        resolve_artifact_path(root, artifact)
    assert artifact.kind is MediaKind.STATIC
