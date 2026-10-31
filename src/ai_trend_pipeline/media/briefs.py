"""Code-only media brief builder, story hashes and the `StoryMediaWorkflow` input and result."""

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ai_trend_pipeline.analysis.models import FactSet
from ai_trend_pipeline.media.models import (
    MAX_ALT_TEXT_CHARS,
    MAX_HIGHLIGHT_CHARS,
    MAX_MEDIA_STORY_ID_CHARS,
    AnimationBeat,
    MediaBrief,
    MediaManifest,
    MediaOutcome,
    canonical_json,
    media_brief_hash,
    sha256_hex,
)
from ai_trend_pipeline.story_drafting.models import StoryPackage

MEDIA_WORKFLOW_ID_PREFIX = "media-v1-"
MEDIA_WORKFLOW_ID_HASH_LENGTH = 16
ELLIPSIS = "…"


def story_content_hash(package: StoryPackage) -> str:
    return sha256_hex(canonical_json(package.model_dump(mode="json", exclude={"media"})))


def story_package_hash(package: StoryPackage) -> str:
    """Hash of text and media together. Final approval freezes this value."""
    return sha256_hex(canonical_json(package.model_dump(mode="json")))


def package_matches_hash(package: StoryPackage, content_hash: str) -> bool:
    return story_package_hash(package) == content_hash


def media_workflow_id_for(package: StoryPackage) -> str:
    return (
        f"{MEDIA_WORKFLOW_ID_PREFIX}{story_content_hash(package)[:MEDIA_WORKFLOW_ID_HASH_LENGTH]}"
    )


def _cut(text: str, limit: int) -> str:
    """Cut at the last space within the limit, adding an ellipsis when text was cut."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    window = collapsed[: limit - 1]
    space = window.rfind(" ")
    if space > 0:
        window = window[:space]
    return window.rstrip() + ELLIPSIS


def _alt_text(headline: str, highlight: str | None) -> str:
    text = f"Story card: {headline}."
    if highlight is not None:
        text = f"{text} {highlight}"
    return text[:MAX_ALT_TEXT_CHARS]


def _build(
    package: StoryPackage,
    highlight: str | None,
    fact_ids: list[str],
    source_label: str | None,
) -> MediaBrief:
    beats = [AnimationBeat.HEADLINE_REVEAL]
    if highlight is not None:
        beats.append(AnimationBeat.FACT_EMPHASIS)
    beats.append(AnimationBeat.SOURCE_FOOTER)
    return MediaBrief(
        story_id=package.story_id,
        headline=package.headline,
        highlight=highlight,
        highlight_fact_ids=fact_ids,
        source_label=source_label,
        animation_beats=beats,
        alt_text=_alt_text(package.headline, highlight),
    )


def media_brief_for(package: StoryPackage, facts: FactSet) -> MediaBrief:
    """Build a brief from a package in code. Never raises for a valid `StoryPackage`."""
    known = {fact.fact_id for fact in facts.facts}
    source_label = package.source_links[0].label if package.source_links else None
    candidates: list[tuple[str, list[str]]] = []
    for point in package.key_points:
        if point.fact_ids and all(fact_id in known for fact_id in point.fact_ids):
            candidates.append((_cut(point.text, MAX_HIGHLIGHT_CHARS), list(point.fact_ids)))
            break
    candidates.append((_cut(package.summary, MAX_HIGHLIGHT_CHARS), []))
    for highlight, fact_ids in candidates:
        for label in (source_label, None):
            try:
                return _build(package, highlight, fact_ids, label)
            except ValidationError:
                continue
    # Headline-only: the headline was already validated by the same cleaning as the brief.
    try:
        return _build(package, None, [], None)
    except ValidationError:
        return MediaBrief(
            story_id=package.story_id,
            headline="Story",
            animation_beats=[AnimationBeat.HEADLINE_REVEAL, AnimationBeat.SOURCE_FOOTER],
            alt_text="Story card",
        )


class StoryMediaInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    story_id: str = Field(min_length=1, max_length=MAX_MEDIA_STORY_ID_CHARS)
    package: StoryPackage
    facts: FactSet
    include_animation: bool = True

    @model_validator(mode="after")
    def _check_input(self) -> "StoryMediaInput":
        if not (self.package.story_id == self.story_id == self.facts.story_id):
            raise ValueError("package, facts and input must share the same story_id")
        if self.package.media is not None:
            raise ValueError("package.media must be empty before media is rendered")
        return self


class StoryMediaResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    manifest: MediaManifest


def text_only_manifest(package: StoryPackage, facts: FactSet) -> MediaManifest:
    """The manifest used when the media child cannot run."""
    return MediaManifest(
        story_id=package.story_id,
        story_content_hash=story_content_hash(package),
        brief_hash=media_brief_hash(media_brief_for(package, facts)),
        outcome=MediaOutcome.TEXT_ONLY,
    )
