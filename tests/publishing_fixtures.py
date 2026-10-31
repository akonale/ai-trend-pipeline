"""Builders for approved story packages with real media manifests."""

from pathlib import Path

from ai_trend_pipeline.media.briefs import story_content_hash, story_package_hash
from ai_trend_pipeline.media.models import (
    MediaArtifact,
    MediaKind,
    MediaManifest,
    MediaOutcome,
    MediaType,
    artifact_id_for,
    sha256_hex,
)
from ai_trend_pipeline.media.storage import write_artifact
from ai_trend_pipeline.story_drafting.models import StoryPackage
from tests.drafting_fixtures import make_clean_package
from tests.media_fixtures import make_brief

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"static-card" * 4
GIF_BYTES = b"GIF89a" + b"animated-card" * 4


def make_real_artifact(
    kind: MediaKind, data: bytes, media_root: Path | None, alt_text: str = "Story card"
) -> MediaArtifact:
    media_type = MediaType.GIF if kind is MediaKind.ANIMATED else MediaType.PNG
    if media_root is not None:
        write_artifact(media_root, data, media_type)
    digest = sha256_hex(data)
    return MediaArtifact(
        artifact_id=artifact_id_for(make_brief(), kind, "fake", "1"),
        kind=kind,
        media_type=media_type,
        width=1200,
        height=675,
        duration_ms=4000 if kind is MediaKind.ANIMATED else None,
        byte_size=len(data),
        alt_text=alt_text,
        content_hash=digest,
        relative_path=f"{digest}.{'gif' if kind is MediaKind.ANIMATED else 'png'}",
    )


def make_approved_package(
    outcome: MediaOutcome | None = MediaOutcome.STATIC,
    *,
    media_root: Path | None = None,
    **overrides: object,
) -> tuple[StoryPackage, str]:
    """A package with a consistent manifest, and its approved package hash."""
    base = make_clean_package(**overrides)
    if outcome is None:
        return base, story_package_hash(base)
    static = (
        None
        if outcome is MediaOutcome.TEXT_ONLY
        else make_real_artifact(MediaKind.STATIC, PNG_BYTES, media_root)
    )
    animated = (
        make_real_artifact(MediaKind.ANIMATED, GIF_BYTES, media_root)
        if outcome is MediaOutcome.GIF
        else None
    )
    manifest = MediaManifest(
        story_id=base.story_id,
        story_content_hash=story_content_hash(base),
        brief_hash="b" * 64,
        outcome=outcome,
        static_artifact=static,
        animated_artifact=animated,
    )
    package = make_clean_package(media=manifest, **overrides)
    return package, story_package_hash(package)
