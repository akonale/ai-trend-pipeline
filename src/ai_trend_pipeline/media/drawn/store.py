"""Pointer files, per-story locks and the per-day call counter for drawn images.

Everything lives under `<media_root>/drawn/`, which the media route never serves. Paths are built
only from a validated sha256 hash or a `date`. Pointers hold metadata only, never SVG.
"""

import fcntl
import os
import re
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ai_trend_pipeline.media.models import SHA256_PATTERN, DrawnArtifact

DRAWN_DIR = "drawn"
POINTER_SCHEMA_VERSION = 1
_HASH = re.compile(SHA256_PATTERN)


class PointerCorrupt(Exception):
    """The pointer file exists but cannot be read as a `DrawnPointer`."""


class DrawnPointer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = POINTER_SCHEMA_VERSION
    story_content_hash: str = Field(pattern=SHA256_PATTERN)
    model: str = Field(min_length=1, max_length=128)
    prompt_hash: str = Field(pattern=SHA256_PATTERN)
    status: Literal["drawn", "failed"]
    images: tuple[DrawnArtifact, ...] = ()

    @model_validator(mode="after")
    def _check_images(self) -> "DrawnPointer":
        if (self.status == "drawn") != bool(self.images):
            raise ValueError("images are set if and only if the status is drawn")
        return self


class DrawnUsage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    day: date
    calls: int = Field(ge=0)


def _drawn_dir(media_root: Path) -> Path:
    path = media_root.resolve() / DRAWN_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _checked_hash(story_content_hash: str) -> str:
    if not _HASH.match(story_content_hash):
        raise ValueError("invalid story content hash")
    return story_content_hash


def pointer_path(media_root: Path, story_content_hash: str) -> Path:
    return media_root.resolve() / DRAWN_DIR / f"{_checked_hash(story_content_hash)}.json"


def _atomic_write(target: Path, text: str) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".drawn-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_name, target)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def read_pointer(media_root: Path, story_content_hash: str) -> DrawnPointer | None:
    path = pointer_path(media_root, story_content_hash)
    if not path.exists():
        return None
    try:
        return DrawnPointer.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, ValidationError):
        raise PointerCorrupt from None


def write_pointer(media_root: Path, pointer: DrawnPointer) -> None:
    _drawn_dir(media_root)
    _atomic_write(pointer_path(media_root, pointer.story_content_hash), pointer.model_dump_json())


@contextmanager
def _flock(path: Path) -> Generator[None]:
    with path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@contextmanager
def story_lock(media_root: Path, story_content_hash: str) -> Generator[None]:
    """Exclusive, blocking lock for one story. Call from a worker thread, not the event loop."""
    lock = _drawn_dir(media_root) / f"{_checked_hash(story_content_hash)}.lock"
    with _flock(lock):
        yield


def reserve_call(media_root: Path, today: date, cap: int) -> bool:
    """Count one API attempt for `today`; False (and no change) when the cap is reached."""
    directory = _drawn_dir(media_root)
    usage_dir = directory / "usage"
    usage_dir.mkdir(exist_ok=True)
    with _flock(directory / "usage.lock"):
        path = usage_dir / f"{today.isoformat()}.json"
        calls = 0
        if path.exists():
            try:
                calls = DrawnUsage.model_validate_json(path.read_text(encoding="utf-8")).calls
            except (OSError, ValueError, ValidationError):
                calls = cap  # unreadable counter: fail closed rather than overspend
        if calls >= cap:
            return False
        _atomic_write(path, DrawnUsage(day=today, calls=calls + 1).model_dump_json())
        return True
