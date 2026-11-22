"""The four slide templates: hook, point, number and source."""

from ai_trend_pipeline.media.card import Box
from ai_trend_pipeline.media.carousel.templates.frame import (
    PALETTE,
    Element,
    clean_main,
    clean_optional,
    finish,
    fit_art,
    layout_slide,
    sizes_between,
)
from ai_trend_pipeline.media.carousel.templates.models import (
    ComposedSlide,
    SlideOverlay,
    SlideTemplateName,
)
from ai_trend_pipeline.media.fonts import BODY_FONT_PATH, HEADLINE_FONT_PATH

SLIDE_TEMPLATE_VERSION = "1"
_ART_LEFT, _ART_RIGHT, _ART_TOP = 40, 1028, 40


class _BaseTemplate:
    name: SlideTemplateName
    art_bottom = 640

    def __init__(self) -> None:
        self.version = f"{SLIDE_TEMPLATE_VERSION}-{self.name}-1080x1080"

    def _elements(self, overlay: SlideOverlay) -> list[Element]:
        raise NotImplementedError

    def compose(self, image_png: bytes, overlay: SlideOverlay) -> ComposedSlide:
        art = fit_art(image_png, Box(_ART_LEFT, _ART_TOP, _ART_RIGHT, self.art_bottom))
        layout = layout_slide(
            self.art_bottom, self._elements(overlay), overlay.position, overlay.total
        )
        return finish(self.name, layout, art, overlay)


class HookTemplate(_BaseTemplate):
    name = SlideTemplateName.HOOK

    def _elements(self, overlay: SlideOverlay) -> list[Element]:
        return [
            Element(
                clean_main(overlay.text),
                HEADLINE_FONT_PATH,
                sizes_between(64, 40, 2),
                1.08,
                3,
                PALETTE.text,
            )
        ]


class PointTemplate(_BaseTemplate):
    name = SlideTemplateName.POINT

    def _elements(self, overlay: SlideOverlay) -> list[Element]:
        return [
            Element(
                clean_main(overlay.text),
                BODY_FONT_PATH,
                sizes_between(44, 28, 2),
                1.3,
                4,
                PALETTE.text,
            )
        ]


class NumberTemplate(_BaseTemplate):
    name = SlideTemplateName.NUMBER
    art_bottom = 560

    def _elements(self, overlay: SlideOverlay) -> list[Element]:
        elements = [
            Element(
                clean_main(overlay.text),
                HEADLINE_FONT_PATH,
                sizes_between(150, 96, 6),
                1.0,
                1,
                PALETTE.on_marker,
                band=True,
            )
        ]
        caption = clean_optional(overlay.caption)
        if caption is not None:
            elements.append(
                Element(
                    caption,
                    BODY_FONT_PATH,
                    sizes_between(34, 26, 2),
                    1.3,
                    2,
                    PALETTE.muted,
                    gap_before=20,
                    optional=True,
                )
            )
        return elements


class SourceTemplate(_BaseTemplate):
    name = SlideTemplateName.SOURCE

    def _elements(self, overlay: SlideOverlay) -> list[Element]:
        elements = [
            Element("Source", BODY_FONT_PATH, (26,), 1.3, 1, PALETTE.muted),
            Element(
                clean_main(overlay.text),
                HEADLINE_FONT_PATH,
                sizes_between(52, 34, 2),
                1.08,
                2,
                PALETTE.text,
                gap_before=12,
            ),
        ]
        caption = clean_optional(overlay.caption)
        if caption is not None:
            elements.append(
                Element(
                    caption,
                    BODY_FONT_PATH,
                    sizes_between(30, 24, 2),
                    1.3,
                    2,
                    PALETTE.muted,
                    gap_before=16,
                    optional=True,
                )
            )
        return elements
