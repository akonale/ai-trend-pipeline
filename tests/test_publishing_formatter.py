"""Pure formatter behaviour."""

import pytest

from ai_trend_pipeline.media.models import MediaOutcome, MediaType
from ai_trend_pipeline.publishing.formatter import PublicationRejected, format_publication
from ai_trend_pipeline.publishing.models import PublicationErrorCode
from ai_trend_pipeline.publishing.registry import BLUESKY_CAPABILITIES, capabilities_for
from tests.drafting_fixtures import make_source_link
from tests.publishing_fixtures import make_approved_package

CAPS = BLUESKY_CAPABILITIES
ANIMATED_CAPS = CAPS.model_copy(
    update={
        "supports_animation": True,
        "animated_media_types": (MediaType.GIF,),
        "max_animated_bytes": 4_000_000,
    }
)


def rejected_code(package: object, content_hash: str, caps: object = CAPS) -> PublicationErrorCode:
    with pytest.raises(PublicationRejected) as info:
        format_publication(package, content_hash, caps)  # pyright: ignore[reportArgumentType]
    return info.value.code


def test_text_only_uses_post_text_and_source_link_facet() -> None:
    package, digest = make_approved_package(None)
    out = format_publication(package, digest, CAPS)
    assert out.text == "Life expectancy reached 72.5 years in 2020.\n\nSource: news.example"
    assert out.media is None
    (facet,) = out.facets
    raw = out.text.encode()
    assert raw[facet.byte_start : facet.byte_end] == b"news.example"
    assert str(facet.uri) == "https://news.example/story"
    assert capabilities_for(CAPS.destination) is CAPS


def test_headline_used_when_post_text_missing() -> None:
    package, digest = make_approved_package(None, post_text=None)
    out = format_publication(package, digest, CAPS)
    assert out.text.startswith("Life expectancy reaches 72.5 years\n\nSource:")


def test_facet_byte_offsets_with_non_ascii_body() -> None:
    link = make_source_link(url="https://www.news.example/story")
    package, digest = make_approved_package(
        None, post_text="Zoë’s café 日本語 ✓", source_links=[link]
    )
    out = format_publication(package, digest, CAPS)
    (facet,) = out.facets
    assert out.text.endswith("Source: news.example")
    assert out.text.encode()[facet.byte_start : facet.byte_end] == b"news.example"
    assert facet.byte_start > len(out.text.split("Source")[0])  # bytes exceed code points


def test_no_source_link_means_no_link_line() -> None:
    package, digest = make_approved_package(None, source_links=[])
    out = format_publication(package, digest, CAPS)
    assert out.text == "Life expectancy reached 72.5 years in 2020."
    assert out.facets == []


def test_static_and_static_fallback_select_static_artifact() -> None:
    for outcome in (MediaOutcome.STATIC, MediaOutcome.STATIC_FALLBACK):
        package, digest = make_approved_package(outcome)
        out = format_publication(package, digest, CAPS)
        assert package.media is not None
        assert out.media is not None
        assert out.media.artifact == package.media.static_artifact
        assert out.media.alt_text == out.media.artifact.alt_text


def test_gif_falls_back_to_static_on_bluesky() -> None:
    package, digest = make_approved_package(MediaOutcome.GIF)
    out = format_publication(package, digest, CAPS)
    assert package.media is not None
    assert out.media is not None
    assert out.media.artifact == package.media.static_artifact


def test_gif_uses_animated_artifact_when_capability_allows() -> None:
    package, digest = make_approved_package(MediaOutcome.GIF)
    out = format_publication(package, digest, ANIMATED_CAPS)
    assert package.media is not None
    assert out.media is not None
    assert out.media.artifact == package.media.animated_artifact


def test_text_only_manifest_and_no_manifest_give_no_media() -> None:
    for outcome in (MediaOutcome.TEXT_ONLY, None):
        package, digest = make_approved_package(outcome)
        assert format_publication(package, digest, CAPS).media is None


def test_rejects_content_hash_mismatch() -> None:
    package, digest = make_approved_package(MediaOutcome.STATIC)
    assert package.media is not None
    assert package.media.static_artifact is not None
    changed_artifact = package.media.static_artifact.model_copy(update={"alt_text": "Changed"})
    changed_media = package.media.model_copy(update={"static_artifact": changed_artifact})
    changed = package.model_copy(update={"media": changed_media})
    assert rejected_code(changed, digest) is PublicationErrorCode.CONTENT_HASH_MISMATCH


def test_rejects_manifest_story_content_hash_mismatch() -> None:
    package, _ = make_approved_package(MediaOutcome.STATIC)
    assert package.media is not None
    stale = package.media.model_copy(update={"story_content_hash": "c" * 64})
    tampered = package.model_copy(update={"media": stale})
    from ai_trend_pipeline.media.briefs import story_package_hash

    digest = story_package_hash(tampered)
    assert rejected_code(tampered, digest) is PublicationErrorCode.MANIFEST_MISMATCH


def test_too_long_text_raises_and_never_truncates() -> None:
    package, digest = make_approved_package(None, post_text="x" * 290)
    assert rejected_code(package, digest) is PublicationErrorCode.POST_TEXT_TOO_LONG
    no_link, no_link_digest = make_approved_package(None, post_text="x" * 300, source_links=[])
    assert len(format_publication(no_link, no_link_digest, CAPS).text) == 300
    bytes_caps = CAPS.model_copy(update={"max_text_bytes": 50})
    assert rejected_code(no_link, no_link_digest, bytes_caps) is (
        PublicationErrorCode.POST_TEXT_TOO_LONG
    )
    assert "x" * 20 not in str(PublicationRejected(PublicationErrorCode.POST_TEXT_TOO_LONG))


def test_unsupported_type_oversize_media_and_long_alt_text_raise() -> None:
    package, digest = make_approved_package(MediaOutcome.STATIC)
    webp_only = CAPS.model_copy(update={"static_media_types": (MediaType.WEBP,)})
    assert rejected_code(package, digest, webp_only) is PublicationErrorCode.MEDIA_NOT_SUPPORTED
    no_images = CAPS.model_copy(update={"max_images": 0})
    assert rejected_code(package, digest, no_images) is PublicationErrorCode.MEDIA_NOT_SUPPORTED
    small = CAPS.model_copy(update={"max_static_bytes": 10})
    assert rejected_code(package, digest, small) is PublicationErrorCode.MEDIA_TOO_LARGE
    short_alt = CAPS.model_copy(update={"max_alt_text_chars": 3})
    assert rejected_code(package, digest, short_alt) is PublicationErrorCode.ALT_TEXT_TOO_LONG


def test_output_is_deterministic() -> None:
    package, digest = make_approved_package(MediaOutcome.GIF)
    first = format_publication(package, digest, CAPS)
    assert first == format_publication(package, digest, CAPS)
    assert first.model_dump_json() == format_publication(package, digest, CAPS).model_dump_json()
