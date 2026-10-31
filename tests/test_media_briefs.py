"""Brief builder, story hashes and StoryMediaInput validation."""

import re

import pytest
from pydantic import ValidationError

from ai_trend_pipeline.media.briefs import (
    StoryMediaInput,
    media_brief_for,
    media_workflow_id_for,
    package_matches_hash,
    story_content_hash,
    story_package_hash,
    text_only_manifest,
)
from ai_trend_pipeline.media.models import AnimationBeat, MediaOutcome, media_brief_hash
from tests.drafting_fixtures import DEFAULT_FACT_ID
from tests.media_fixtures import make_manifest
from tests.story_generation_fixtures import make_facts, make_package

STORY = "story-1"


def test_brief_uses_first_supported_key_point() -> None:
    package = make_package(
        STORY,
        key_points=[
            {"text": "Unsupported point.", "fact_ids": ["fact-v1-" + "0" * 16]},
            {"text": "Supported point.", "fact_ids": [DEFAULT_FACT_ID]},
            {"text": "Later supported point.", "fact_ids": [DEFAULT_FACT_ID]},
        ],
    )
    brief = media_brief_for(package, make_facts(STORY))
    assert brief.highlight == "Supported point."
    assert brief.highlight_fact_ids == [DEFAULT_FACT_ID]
    assert brief.source_label == "Original report"
    assert brief.animation_beats == [
        AnimationBeat.HEADLINE_REVEAL,
        AnimationBeat.FACT_EMPHASIS,
        AnimationBeat.SOURCE_FOOTER,
    ]
    assert brief.alt_text == f"Story card: {package.headline}. Supported point."


def test_brief_falls_back_to_trimmed_summary() -> None:
    summary = " ".join(["word"] * 120)[:600]
    package = make_package(STORY, key_points=[], summary=summary, source_links=[])
    brief = media_brief_for(package, make_facts(STORY))
    assert brief.highlight is not None
    assert len(brief.highlight) <= 280
    assert brief.highlight.endswith("…")
    assert brief.highlight_fact_ids == []
    assert brief.source_label is None
    assert not brief.highlight[:-1].endswith(" ")


def test_brief_handles_unicode_and_markup_like_text_without_raising() -> None:
    markup_point = {"text": "<<>> ``", "fact_ids": [DEFAULT_FACT_ID]}
    package = make_package(
        STORY,
        key_points=[markup_point],
        summary="Résumé 日本語 ☃ ok",
        headline="Ünïcode ☃ headline",
    )
    brief = media_brief_for(package, make_facts(STORY))
    assert brief.highlight == "Résumé 日本語 ☃ ok"
    assert brief.highlight_fact_ids == []
    bad = make_package(STORY, key_points=[markup_point], summary="<>", headline="a <b> c")
    fallback = media_brief_for(bad, make_facts(STORY))
    assert fallback.story_id == STORY
    assert fallback.highlight is None
    assert AnimationBeat.FACT_EMPHASIS not in fallback.animation_beats


def test_story_content_hash_and_workflow_id_ignore_media() -> None:
    plain = make_package(STORY)
    with_media = make_package(STORY, media=make_manifest())
    assert story_content_hash(plain) == story_content_hash(with_media)
    assert len(story_content_hash(plain)) == 64
    workflow_id = media_workflow_id_for(with_media)
    assert re.fullmatch(r"media-v1-[0-9a-f]{16}", workflow_id)
    assert workflow_id == media_workflow_id_for(plain)
    assert workflow_id != media_workflow_id_for(make_package(STORY, headline="Different"))


def test_story_media_input_rejects_mismatched_ids_or_existing_media() -> None:
    package, facts = make_package(STORY), make_facts(STORY)
    assert StoryMediaInput(story_id=STORY, package=package, facts=facts).include_animation
    with pytest.raises(ValidationError):
        StoryMediaInput(story_id="other", package=package, facts=facts)
    with pytest.raises(ValidationError):
        StoryMediaInput(story_id=STORY, package=package, facts=make_facts("other"))
    with pytest.raises(ValidationError):
        StoryMediaInput(
            story_id=STORY, package=make_package(STORY, media=make_manifest()), facts=facts
        )


def test_story_package_hash_includes_media_and_alt_text() -> None:
    package = make_package(STORY)
    manifest = make_manifest(MediaOutcome.GIF)
    with_media = package.model_copy(update={"media": manifest})
    assert re.fullmatch(r"[0-9a-f]{64}", story_package_hash(package))
    assert story_package_hash(with_media) != story_package_hash(package)
    assert story_content_hash(with_media) == story_content_hash(package)
    assert story_package_hash(with_media) == story_package_hash(with_media.model_copy())
    assert manifest.static_artifact is not None
    new_alt = manifest.static_artifact.model_copy(update={"alt_text": "Different alt text"})
    changed = with_media.model_copy(
        update={"media": manifest.model_copy(update={"static_artifact": new_alt})}
    )
    assert story_package_hash(changed) != story_package_hash(with_media)
    reworded = with_media.model_copy(update={"headline": "Another headline"})
    assert story_package_hash(reworded) != story_package_hash(with_media)


def test_package_matches_hash_detects_post_approval_media_change() -> None:
    package = make_package(STORY).model_copy(update={"media": make_manifest(MediaOutcome.GIF)})
    frozen = story_package_hash(package)
    assert package_matches_hash(package, frozen)
    assert package.media is not None and package.media.animated_artifact is not None
    other_artifact = package.media.animated_artifact.model_copy(update={"byte_size": 101})
    swapped = package.model_copy(
        update={"media": package.media.model_copy(update={"animated_artifact": other_artifact})}
    )
    assert not package_matches_hash(swapped, frozen)
    text_only = package.model_copy(update={"media": make_manifest(MediaOutcome.TEXT_ONLY)})
    assert not package_matches_hash(text_only, frozen)
    assert not package_matches_hash(package, "0" * 64)


def test_text_only_manifest_is_valid_and_deterministic() -> None:
    package = make_package(STORY)
    facts = make_facts(STORY)
    first = text_only_manifest(package, facts)
    assert first == text_only_manifest(package, facts)
    assert first.outcome is MediaOutcome.TEXT_ONLY
    assert first.static_artifact is None and first.animated_artifact is None
    assert first.story_id == STORY
    assert first.story_content_hash == story_content_hash(package)
    assert first.brief_hash == media_brief_hash(media_brief_for(package, facts))
