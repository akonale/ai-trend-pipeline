"""Image backend protocol, errors and registry. Mirrors `media/providers.py`."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

import anthropic

from ai_trend_pipeline.media.images.models import ImageBatch, ImageBrief


class ImageBackendTransient(Exception):
    """A retryable failure. The message is fixed and never carries model or story text."""


class ImageBackendRefused(Exception):
    """A request that must not be retried. The message is fixed."""


class UnknownImageBackendError(Exception):
    pass


class ImageBackendName(StrEnum):
    ANTHROPIC_SVG = "anthropic-svg"
    FAKE = "fake"


class ImageBackend(Protocol):
    name: ImageBackendName

    async def generate(self, brief: ImageBrief) -> ImageBatch: ...


@dataclass(frozen=True)
class ImageBackendDeps:
    anthropic_client: anthropic.AsyncAnthropic
    model: str


def _make_anthropic_svg(deps: ImageBackendDeps) -> ImageBackend:
    from ai_trend_pipeline.media.images.anthropic_svg import AnthropicSvgBackend

    return AnthropicSvgBackend(deps.anthropic_client, deps.model)


def _make_fake(deps: ImageBackendDeps) -> ImageBackend:
    from ai_trend_pipeline.media.images.fake import FakeImageBackend

    return FakeImageBackend()


IMAGE_BACKENDS: Mapping[ImageBackendName, Callable[[ImageBackendDeps], ImageBackend]] = {
    ImageBackendName.ANTHROPIC_SVG: _make_anthropic_svg,
    ImageBackendName.FAKE: _make_fake,
}


def image_backend_for(name: ImageBackendName | str, deps: ImageBackendDeps) -> ImageBackend:
    try:
        backend = ImageBackendName(name)
    except ValueError:
        raise UnknownImageBackendError(f"Unknown image backend {name!r}") from None
    return IMAGE_BACKENDS[backend](deps)
