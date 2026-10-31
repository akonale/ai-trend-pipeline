"""Media contract validation, hashes and IDs."""

import pytest
from pydantic import ValidationError

from ai_trend_pipeline.media.models import (
    MediaArtifact,
    MediaKind,
    MediaManifest,
    MediaOutcome,
    MediaType,
    artifact_id_for,
    media_brief_hash,
)
from tests.media_fixtures import (
    HASH_A,
    HASH_B,
    artifact_payload,
    make_artifact,
    make_brief,
    make_manifest,
)


@pytest.mark.parametrize(
    "path",
    [
        "../x.png",
        "/etc/passwd",
        "a/b.png",
        "x.png",
        f"{HASH_A}.gif",
        f"{HASH_A}.png.exe",
        f"{HASH_B}.png",
        f"..\\{HASH_A}.png",
    ],
)
def test_artifact_rejects_unsafe_relative_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        MediaArtifact.model_validate(artifact_payload(relative_path=path))


@pytest.mark.parametrize(
    "overrides",
    [
        {"media_type": "image/svg+xml"},
        {"media_type": "text/html"},
        {"media_type": MediaType.GIF, "relative_path": f"{HASH_A}.gif"},
    ],
)
def test_artifact_rejects_disallowed_or_mismatched_mime(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        MediaArtifact.model_validate(artifact_payload(**overrides))


def test_animated_png_is_rejected() -> None:
    with pytest.raises(ValidationError):
        MediaArtifact.model_validate(
            artifact_payload(
                MediaKind.ANIMATED, media_type=MediaType.PNG, relative_path=f"{HASH_B}.png"
            )
        )


@pytest.mark.parametrize("alt", ["", "   \n", "x" * 1001])
def test_artifact_rejects_missing_or_oversized_alt_text(alt: str) -> None:
    with pytest.raises(ValidationError):
        MediaArtifact.model_validate(artifact_payload(alt_text=alt))


@pytest.mark.parametrize(
    ("kind", "overrides"),
    [
        (MediaKind.STATIC, {"width": 63}),
        (MediaKind.STATIC, {"height": 2049}),
        (MediaKind.STATIC, {"duration_ms": 2000}),
        (MediaKind.ANIMATED, {"duration_ms": None}),
        (MediaKind.ANIMATED, {"duration_ms": 999}),
        (MediaKind.ANIMATED, {"duration_ms": 6001}),
    ],
)
def test_artifact_rejects_invalid_dimensions_and_duration(
    kind: MediaKind, overrides: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        MediaArtifact.model_validate(artifact_payload(kind, **overrides))


def test_artifact_rejects_oversized_byte_size_per_kind() -> None:
    assert make_artifact(byte_size=1_000_000).byte_size == 1_000_000
    with pytest.raises(ValidationError):
        make_artifact(byte_size=1_000_001)
    assert make_artifact(MediaKind.ANIMATED, byte_size=4_000_000)
    with pytest.raises(ValidationError):
        make_artifact(MediaKind.ANIMATED, byte_size=4_000_001)
    with pytest.raises(ValidationError):
        make_artifact(byte_size=0)


def test_manifest_outcome_invariants() -> None:
    static = make_artifact(MediaKind.STATIC)
    animated = make_artifact(MediaKind.ANIMATED)
    for outcome in MediaOutcome:
        assert make_manifest(outcome).outcome is outcome
    bad: list[dict[str, object]] = [
        {"outcome": MediaOutcome.GIF, "animated_artifact": None},
        {"outcome": MediaOutcome.GIF, "static_artifact": None},
        {"outcome": MediaOutcome.STATIC, "animated_artifact": animated},
        {"outcome": MediaOutcome.STATIC, "static_artifact": None, "animated_artifact": None},
        {"outcome": MediaOutcome.STATIC_FALLBACK, "animated_artifact": animated},
        {"outcome": MediaOutcome.TEXT_ONLY, "static_artifact": static, "animated_artifact": None},
        {"outcome": MediaOutcome.GIF, "static_artifact": animated, "animated_artifact": static},
        {
            "outcome": MediaOutcome.GIF,
            "animated_artifact": make_artifact(MediaKind.ANIMATED, artifact_id=static.artifact_id),
        },
    ]
    for overrides in bad:
        payload: dict[str, object] = {
            "story_id": "s",
            "story_content_hash": HASH_A,
            "brief_hash": HASH_B,
            "static_artifact": static,
            "animated_artifact": animated,
        }
        payload.update(overrides)
        with pytest.raises(ValidationError):
            MediaManifest.model_validate(payload)


def test_manifest_artifact_lookup_uses_only_the_two_slots() -> None:
    manifest = make_manifest(MediaOutcome.GIF)
    assert manifest.static_artifact is not None
    assert manifest.artifact(manifest.static_artifact.artifact_id) == manifest.static_artifact
    assert manifest.artifact("art-v1-0000000000000000") is None


def test_manifest_rejects_oversized_json() -> None:
    big = "\U0001f600" * 1000  # 4 UTF-8 bytes each, so two artifacts exceed 8,192 bytes
    assert len(make_manifest(MediaOutcome.GIF).model_dump_json()) < 8_192
    with pytest.raises(ValidationError, match="too large"):
        make_manifest(
            MediaOutcome.GIF,
            static_artifact=make_artifact(MediaKind.STATIC, alt_text=big),
            animated_artifact=make_artifact(MediaKind.ANIMATED, alt_text=big),
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"headline": "See https://example.com"},
        {"headline": "Visit www.example.com"},
        {"highlight": "a <b>bold</b> claim"},
        {"highlight": "a > b"},
        {"alt_text": "use `code`"},
        {"source_label": ""},
        {"unknown": "field"},
        {"animation_beats": []},
        {"highlight": None, "highlight_fact_ids": []},
    ],
)
def test_brief_rejects_urls_markup_and_extra_fields(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        make_brief(**overrides)


def test_brief_collapses_whitespace_and_controls() -> None:
    assert make_brief(headline="  A \x00 b\n\tc ").headline == "A b c"


def test_identical_briefs_give_identical_artifact_ids_and_hashes() -> None:
    a, b = make_brief(), make_brief()
    assert media_brief_hash(a) == media_brief_hash(b)
    assert len(media_brief_hash(a)) == 64
    first = artifact_id_for(a, MediaKind.STATIC, "fake", "1")
    assert first == artifact_id_for(b, MediaKind.STATIC, "fake", "1")
    assert first.startswith("art-v1-") and len(first) == len("art-v1-") + 16


def test_artifact_id_changes_with_brief_kind_or_renderer_version() -> None:
    brief = make_brief()
    base = artifact_id_for(brief, MediaKind.STATIC, "fake", "1")
    assert base != artifact_id_for(make_brief(headline="Other"), MediaKind.STATIC, "fake", "1")
    assert base != artifact_id_for(brief, MediaKind.ANIMATED, "fake", "1")
    assert base != artifact_id_for(brief, MediaKind.STATIC, "fake", "2")
    assert base != artifact_id_for(brief, MediaKind.STATIC, "other", "1")
