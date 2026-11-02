"""Pure, deterministic path from one untrusted model SVG to a safe PNG.

Every failure raises `DrawingRejected` whose text is the reason value only, raised `from None`, so
nothing the model wrote (the sanitiser quotes attribute names) can reach a log or a record.
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from enum import StrEnum

from PIL import Image

from ai_trend_pipeline.media.illustrations.rasterise import rasterise_svg
from ai_trend_pipeline.media.illustrations.sanitize import UnsafeSvg, sanitize_svg
from ai_trend_pipeline.media.illustrations.scene import SCENE_PALETTE, encode_png
from ai_trend_pipeline.media.models import MAX_STATIC_BYTES, MediaType, has_media_signature

MAX_DRAWN_SVG_BYTES = 4096
DRAWN_VIEWBOX = "0 0 800 600"
DRAWN_SIZE = (1200, 900)
MAX_DRAWN_ELEMENTS = 300
DRAWN_COLOURS = frozenset(
    {
        SCENE_PALETTE.ground,
        SCENE_PALETTE.grid,
        SCENE_PALETTE.paper,
        SCENE_PALETTE.ink,
        SCENE_PALETTE.accent,
        *SCENE_PALETTE.supports,
    }
)

_VIEWBOX_SEPARATORS = re.compile(r"[\s,]+")


class DrawingRejectReason(StrEnum):
    TOO_LARGE = "too_large"
    UNSAFE = "unsafe"
    WRONG_VIEWBOX = "wrong_viewbox"
    TOO_MANY_ELEMENTS = "too_many_elements"
    RENDER_FAILED = "render_failed"
    BLANK = "blank"
    PNG_TOO_LARGE = "png_too_large"


class DrawingRejected(ValueError):
    """A drawing was refused. `str()` is the reason value only."""

    def __init__(self, reason: DrawingRejectReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


@dataclass(frozen=True)
class RenderedDrawing:
    png: bytes
    width: int
    height: int


def _parse_checked(svg: str) -> ET.Element:
    """Parse an SVG that already passed the sanitiser; a failure here is still a plain reject."""
    try:
        return ET.fromstring(svg)
    except ET.ParseError:
        raise DrawingRejected(DrawingRejectReason.UNSAFE) from None


def render_drawn_svg(svg: str) -> RenderedDrawing:
    if len(svg.encode("utf-8")) > MAX_DRAWN_SVG_BYTES:
        raise DrawingRejected(DrawingRejectReason.TOO_LARGE) from None
    try:
        sanitize_svg(svg, DRAWN_COLOURS)
    except UnsafeSvg:
        raise DrawingRejected(DrawingRejectReason.UNSAFE) from None
    root = _parse_checked(svg)
    viewbox = _VIEWBOX_SEPARATORS.sub(" ", root.attrib.get("viewBox", "").strip())
    if viewbox != DRAWN_VIEWBOX:
        raise DrawingRejected(DrawingRejectReason.WRONG_VIEWBOX) from None
    if sum(1 for _ in root.iter()) > MAX_DRAWN_ELEMENTS:
        raise DrawingRejected(DrawingRejectReason.TOO_MANY_ELEMENTS) from None
    width, height = DRAWN_SIZE
    try:
        drawing = rasterise_svg(svg, width, height)
    except Exception:
        raise DrawingRejected(DrawingRejectReason.RENDER_FAILED) from None
    paper = Image.new("RGBA", drawing.size, SCENE_PALETTE.paper)
    flat = Image.alpha_composite(paper, drawing).convert("RGB")
    if flat.getcolors(maxcolors=1) is not None:  # None means more than one colour
        raise DrawingRejected(DrawingRejectReason.BLANK) from None
    png = encode_png(flat)
    if not has_media_signature(png, MediaType.PNG) or len(png) > MAX_STATIC_BYTES:
        raise DrawingRejected(DrawingRejectReason.PNG_TOO_LARGE) from None
    return RenderedDrawing(png=png, width=flat.width, height=flat.height)
