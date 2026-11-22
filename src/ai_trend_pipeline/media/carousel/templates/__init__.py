"""Slide templates: public names."""

from ai_trend_pipeline.media.carousel.templates.models import (
    MAX_SLIDE_CAPTION_CHARS,
    MAX_SLIDE_TEXT_CHARS,
    ComposedSlide,
    SlideOverlay,
    SlideTemplateName,
)
from ai_trend_pipeline.media.carousel.templates.registry import (
    SLIDE_TEMPLATE_VERSION,
    SLIDE_TEMPLATES,
    SlideTemplate,
    UnknownSlideTemplateError,
    slide_template_for,
)

__all__ = [
    "MAX_SLIDE_CAPTION_CHARS",
    "MAX_SLIDE_TEXT_CHARS",
    "SLIDE_TEMPLATES",
    "SLIDE_TEMPLATE_VERSION",
    "ComposedSlide",
    "SlideOverlay",
    "SlideTemplate",
    "SlideTemplateName",
    "UnknownSlideTemplateError",
    "slide_template_for",
]
