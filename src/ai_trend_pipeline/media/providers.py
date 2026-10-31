"""Static renderer registry. Renderers never cross a Temporal boundary."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from ai_trend_pipeline.media.models import MediaBrief, MediaType


class MediaRenderError(Exception):
    """A transient render failure; the activity maps it to a retryable error."""


class MediaContentUnsupported(Exception):
    """The brief cannot be drawn (for example an unsupported glyph). Never retried."""


class UnknownMediaProviderError(Exception):
    pass


@dataclass(frozen=True)
class RenderedMedia:
    data: bytes
    media_type: MediaType
    width: int
    height: int
    duration_ms: int | None


class MediaRenderer(Protocol):
    name: str
    version: str

    def render_static(self, brief: MediaBrief) -> RenderedMedia: ...

    def render_animated(self, brief: MediaBrief) -> RenderedMedia: ...


class MediaProviderName(StrEnum):
    FAKE = "fake"
    STORY_CARD = "story-card"


def _make_fake() -> MediaRenderer:
    # Imported here because `fake` imports this module for the shared contracts.
    from ai_trend_pipeline.media.fake import FakeMediaRenderer

    return FakeMediaRenderer()


def _make_story_card() -> MediaRenderer:
    from ai_trend_pipeline.media.card import StoryCardRenderer

    return StoryCardRenderer()


MEDIA_RENDERERS: Mapping[MediaProviderName, Callable[[], MediaRenderer]] = {
    MediaProviderName.FAKE: _make_fake,
    MediaProviderName.STORY_CARD: _make_story_card,
}


def renderer_for(name: MediaProviderName | str) -> MediaRenderer:
    try:
        provider = MediaProviderName(name)
    except ValueError:
        raise UnknownMediaProviderError(f"Unknown media provider {name!r}") from None
    return MEDIA_RENDERERS[provider]()
