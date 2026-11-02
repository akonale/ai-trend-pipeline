"""Illustration provider slot: protocol, names, static registry and alt text.

A hosted image model would be one more provider here, with no workflow or manifest change.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from ai_trend_pipeline.media.models import (
    MAX_ALT_TEXT_CHARS,
    IllustrateInput,
    IllustrationStatus,
    clean_media_text,
)
from ai_trend_pipeline.media.providers import RenderedMedia


@dataclass(frozen=True)
class IllustrationOutput:
    status: IllustrationStatus  # matched or generic
    rendered: RenderedMedia
    description: str | None = None  # plain noun phrase of the drawing, for alt text


class IllustrationProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    def illustrate(self, data: IllustrateInput) -> IllustrationOutput: ...


class UnknownIllustrationProviderError(Exception):
    pass


class IllustrationProviderName(StrEnum):
    OFF = "off"
    ILLUSTRATED_CARD = "illustrated-card"
    FAKE = "fake"


def _make_illustrated_card() -> IllustrationProvider:
    from ai_trend_pipeline.media.illustrations.renderer import IllustratedCardProvider

    return IllustratedCardProvider()


def _make_fake() -> IllustrationProvider:
    from ai_trend_pipeline.media.fake import FakeIllustrationProvider

    return FakeIllustrationProvider()


ILLUSTRATION_PROVIDERS: Mapping[IllustrationProviderName, Callable[[], IllustrationProvider]] = {
    IllustrationProviderName.ILLUSTRATED_CARD: _make_illustrated_card,
    IllustrationProviderName.FAKE: _make_fake,
}


def illustration_provider_for(name: IllustrationProviderName | str) -> IllustrationProvider | None:
    """The provider for `name`; `off` gives `None` (the kill switch)."""
    try:
        provider = IllustrationProviderName(name)
    except ValueError:
        raise UnknownIllustrationProviderError(f"Unknown illustration provider {name!r}") from None
    if provider is IllustrationProviderName.OFF:
        return None
    return ILLUSTRATION_PROVIDERS[provider]()


def illustration_alt_text(headline: str, description: str | None) -> str:
    """Code-built alt text from approved story text and the catalogue description only."""
    if description is None:
        text = (
            f"Illustrated card with the headline: {headline}. "
            "Abstract shapes on a patterned background."
        )
    else:
        text = (
            f"Illustrated card with the headline: {headline}. "
            f"Drawing: {description}, on a patterned background."
        )
    cleaned = clean_media_text(text)
    assert isinstance(cleaned, str)
    return cleaned[:MAX_ALT_TEXT_CHARS]
