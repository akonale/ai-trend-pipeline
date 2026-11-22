"""Slide template registry."""

from collections.abc import Mapping
from typing import Protocol

from ai_trend_pipeline.media.carousel.templates.models import (
    ComposedSlide,
    SlideOverlay,
    SlideTemplateName,
)
from ai_trend_pipeline.media.carousel.templates.slides import (
    SLIDE_TEMPLATE_VERSION,
    HookTemplate,
    NumberTemplate,
    PointTemplate,
    SourceTemplate,
)

__all__ = [
    "SLIDE_TEMPLATES",
    "SLIDE_TEMPLATE_VERSION",
    "SlideTemplate",
    "UnknownSlideTemplateError",
    "slide_template_for",
]


class UnknownSlideTemplateError(Exception):
    pass


class SlideTemplate(Protocol):
    name: SlideTemplateName
    version: str

    def compose(self, image_png: bytes, overlay: SlideOverlay) -> ComposedSlide: ...


SLIDE_TEMPLATES: Mapping[SlideTemplateName, SlideTemplate] = {
    SlideTemplateName.HOOK: HookTemplate(),
    SlideTemplateName.POINT: PointTemplate(),
    SlideTemplateName.NUMBER: NumberTemplate(),
    SlideTemplateName.SOURCE: SourceTemplate(),
}


def slide_template_for(name: SlideTemplateName | str) -> SlideTemplate:
    try:
        return SLIDE_TEMPLATES[SlideTemplateName(name)]
    except ValueError:
        raise UnknownSlideTemplateError(f"Unknown slide template {name!r}") from None
