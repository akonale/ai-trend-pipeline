"""Deterministic builders for media tests."""

from ai_trend_pipeline.media.models import (
    AnimationBeat,
    MediaArtifact,
    MediaBrief,
    MediaKind,
    MediaManifest,
    MediaOutcome,
    MediaType,
    artifact_id_for,
)

HASH_A = "a" * 64
HASH_B = "b" * 64


def make_brief(**overrides: object) -> MediaBrief:
    payload: dict[str, object] = {
        "story_id": "story-1",
        "headline": "Life expectancy reaches 72.5 years",
        "highlight": "World life expectancy was 72.5 years in 2020.",
        "highlight_fact_ids": [],
        "source_label": "Original report",
        "animation_beats": [
            AnimationBeat.HEADLINE_REVEAL,
            AnimationBeat.FACT_EMPHASIS,
            AnimationBeat.SOURCE_FOOTER,
        ],
        "alt_text": "Story card: Life expectancy reaches 72.5 years.",
    }
    payload.update(overrides)
    return MediaBrief.model_validate(payload)


def artifact_payload(
    kind: MediaKind = MediaKind.STATIC, /, **overrides: object
) -> dict[str, object]:
    animated = kind is MediaKind.ANIMATED
    media_type = MediaType.GIF if animated else MediaType.PNG
    content_hash = HASH_B if animated else HASH_A
    payload: dict[str, object] = {
        "artifact_id": artifact_id_for(make_brief(), kind, "fake", "1"),
        "kind": kind,
        "media_type": media_type,
        "width": 1200,
        "height": 675,
        "duration_ms": 4000 if animated else None,
        "byte_size": 100,
        "alt_text": "Story card",
        "content_hash": content_hash,
        "relative_path": f"{content_hash}.{'gif' if animated else 'png'}",
    }
    payload.update(overrides)
    return payload


def make_artifact(kind: MediaKind = MediaKind.STATIC, /, **overrides: object) -> MediaArtifact:
    return MediaArtifact.model_validate(artifact_payload(kind, **overrides))


def make_manifest(outcome: MediaOutcome = MediaOutcome.GIF, **overrides: object) -> MediaManifest:
    payload: dict[str, object] = {
        "story_id": "story-1",
        "story_content_hash": HASH_A,
        "brief_hash": HASH_B,
        "outcome": outcome,
        "static_artifact": None
        if outcome is MediaOutcome.TEXT_ONLY
        else make_artifact(MediaKind.STATIC),
        "animated_artifact": make_artifact(MediaKind.ANIMATED)
        if outcome is MediaOutcome.GIF
        else None,
    }
    payload.update(overrides)
    return MediaManifest.model_validate(payload)
