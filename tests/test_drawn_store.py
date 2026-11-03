"""Pointer and usage store tests."""

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_trend_pipeline.media.drawn.store import (
    DrawnPointer,
    PointerCorrupt,
    pointer_path,
    read_pointer,
    reserve_call,
    story_lock,
    write_pointer,
)
from ai_trend_pipeline.media.models import (
    DrawnArtifact,
    DrawnRole,
    MediaArtifact,
    MediaKind,
    MediaType,
)

STORY = "a" * 64
PROMPT = "b" * 64


def _image() -> DrawnArtifact:
    artifact = MediaArtifact(
        artifact_id="art-v1-0123456789abcdef",
        kind=MediaKind.STATIC,
        media_type=MediaType.PNG,
        width=1200,
        height=900,
        byte_size=10,
        alt_text="alt",
        content_hash="c" * 64,
        relative_path=f"{'c' * 64}.png",
    )
    return DrawnArtifact(role=DrawnRole.HERO, artifact=artifact)


def _pointer(status: str = "drawn") -> DrawnPointer:
    images = (_image(),) if status == "drawn" else ()
    return DrawnPointer.model_validate(
        {
            "story_content_hash": STORY,
            "model": "m",
            "prompt_hash": PROMPT,
            "status": status,
            "images": images,
        }
    )


def test_pointer_round_trip(tmp_path: Path) -> None:
    pointer = _pointer()
    write_pointer(tmp_path, pointer)
    assert read_pointer(tmp_path, STORY) == pointer


def test_atomic_write_leaves_no_tmp_file(tmp_path: Path) -> None:
    write_pointer(tmp_path, _pointer())
    assert reserve_call(tmp_path, date(2026, 1, 1), 5)
    assert not list(tmp_path.rglob("*.tmp"))


def test_read_pointer_missing_and_corrupt(tmp_path: Path) -> None:
    assert read_pointer(tmp_path, STORY) is None
    write_pointer(tmp_path, _pointer("failed"))
    pointer_path(tmp_path, STORY).write_text("garbage{")
    with pytest.raises(PointerCorrupt):
        read_pointer(tmp_path, STORY)


def test_reserve_call_counts_to_cap_and_resets_per_date(tmp_path: Path) -> None:
    day = date(2026, 1, 1)
    assert reserve_call(tmp_path, day, 2)
    assert reserve_call(tmp_path, day, 2)
    assert not reserve_call(tmp_path, day, 2)
    assert reserve_call(tmp_path, date(2026, 1, 2), 2)


def test_pointer_status_rules() -> None:
    for status in ("capped", "disabled"):
        with pytest.raises(ValidationError):
            DrawnPointer.model_validate(
                {"story_content_hash": STORY, "model": "m", "prompt_hash": PROMPT, "status": status}
            )
    with pytest.raises(ValidationError):
        DrawnPointer.model_validate(
            {"story_content_hash": STORY, "model": "m", "prompt_hash": PROMPT, "status": "drawn"}
        )


def test_paths_reject_bad_hashes(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        pointer_path(tmp_path, "../x")
    with pytest.raises(ValueError):
        with story_lock(tmp_path, "../x"):
            pass
