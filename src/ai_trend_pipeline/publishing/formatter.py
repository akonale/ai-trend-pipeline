"""Pure formatter: an approved `StoryPackage` becomes post text, facets and one media item."""

from urllib.parse import urlsplit

from ai_trend_pipeline.media.briefs import package_matches_hash, story_content_hash
from ai_trend_pipeline.media.models import MediaArtifact, MediaKind, MediaOutcome, MediaType
from ai_trend_pipeline.publishing.models import (
    DestinationCapabilities,
    LinkFacet,
    PublicationErrorCode,
    PublicationMedia,
    PublicationPackage,
)
from ai_trend_pipeline.story_drafting.models import StoryPackage

_MESSAGES: dict[PublicationErrorCode, str] = {
    PublicationErrorCode.CONTENT_HASH_MISMATCH: "The story does not match the approved hash",
    PublicationErrorCode.MANIFEST_MISMATCH: "The media manifest does not match the story",
    PublicationErrorCode.POST_TEXT_TOO_LONG: "The post text is too long for the destination",
    PublicationErrorCode.ALT_TEXT_TOO_LONG: "The alt text is too long for the destination",
    PublicationErrorCode.MEDIA_NOT_SUPPORTED: "The destination does not support this media",
    PublicationErrorCode.MEDIA_TOO_LARGE: "The media is too large for the destination",
}


class PublicationRejected(ValueError):
    """A local check failed. The message is fixed and holds no story text."""

    def __init__(self, code: PublicationErrorCode) -> None:
        super().__init__(_MESSAGES.get(code, code.value))
        self.code = code


def _anchor(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return host.removeprefix("www.")


def _choose_artifact(
    package: StoryPackage, capabilities: DestinationCapabilities
) -> MediaArtifact | None:
    manifest = package.media
    if manifest is None or manifest.outcome is MediaOutcome.TEXT_ONLY:
        return None
    if (
        manifest.outcome is MediaOutcome.GIF
        and capabilities.supports_animation
        and MediaType.GIF in capabilities.animated_media_types
        and manifest.animated_artifact is not None
    ):
        return manifest.animated_artifact
    return manifest.static_artifact


def _media_for(
    package: StoryPackage, capabilities: DestinationCapabilities
) -> PublicationMedia | None:
    artifact = _choose_artifact(package, capabilities)
    if artifact is None:
        return None
    animated = artifact.kind is MediaKind.ANIMATED
    allowed = capabilities.animated_media_types if animated else capabilities.static_media_types
    limit = capabilities.max_animated_bytes if animated else capabilities.max_static_bytes
    if capabilities.max_images == 0 or artifact.media_type not in allowed:
        raise PublicationRejected(PublicationErrorCode.MEDIA_NOT_SUPPORTED)
    if artifact.byte_size > limit:
        raise PublicationRejected(PublicationErrorCode.MEDIA_TOO_LARGE)
    if len(artifact.alt_text) > capabilities.max_alt_text_chars:
        raise PublicationRejected(PublicationErrorCode.ALT_TEXT_TOO_LONG)
    return PublicationMedia(artifact=artifact, alt_text=artifact.alt_text)


def formatted_post(
    package: StoryPackage, capabilities: DestinationCapabilities
) -> tuple[str, list[LinkFacet], str | None]:
    """The post text, its link facets and the link anchor text (or None). Pure, no checks."""
    body = package.post_text if package.post_text is not None else package.headline
    if capabilities.supports_link_facets and package.source_links:
        link = package.source_links[0]
        anchor = _anchor(str(link.url))
        prefix = f"{body}\n\nSource: "
        start = len(prefix.encode("utf-8"))
        facet = LinkFacet(
            byte_start=start, byte_end=start + len(anchor.encode("utf-8")), uri=link.url
        )
        return prefix + anchor, [facet], anchor
    return body, [], None


def format_publication(
    package: StoryPackage, content_hash: str, capabilities: DestinationCapabilities
) -> PublicationPackage:
    if not package_matches_hash(package, content_hash):
        raise PublicationRejected(PublicationErrorCode.CONTENT_HASH_MISMATCH)
    manifest = package.media
    if manifest is not None and (
        manifest.story_id != package.story_id
        or manifest.story_content_hash != story_content_hash(package)
    ):
        raise PublicationRejected(PublicationErrorCode.MANIFEST_MISMATCH)

    text, facets, _ = formatted_post(package, capabilities)
    if len(text) > capabilities.max_text_chars or len(text.encode("utf-8")) > (
        capabilities.max_text_bytes
    ):
        raise PublicationRejected(PublicationErrorCode.POST_TEXT_TOO_LONG)

    return PublicationPackage(
        destination=capabilities.destination,
        story_id=package.story_id,
        content_hash=content_hash,
        text=text,
        facets=facets,
        media=_media_for(package, capabilities),
    )
