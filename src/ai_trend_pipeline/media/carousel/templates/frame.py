"""Shared slide drawing: frame, text fitting, image fit, PNG encoding and alt text."""

import io
import itertools
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw

from ai_trend_pipeline.media.card import (
    CARD_SQUARE,
    CARD_THEMES,
    WORDMARK,
    Box,
    CardTheme,
    TextLine,
    clean_text,
    ellipsise,
    encode_png,
    is_supported,
    place_line,
    wrap_text,
)
from ai_trend_pipeline.media.carousel.templates.models import (
    ComposedSlide,
    SlideOverlay,
    SlideTemplateName,
)
from ai_trend_pipeline.media.fonts import BODY_FONT_PATH, HEADLINE_FONT_PATH, load_font
from ai_trend_pipeline.media.models import (
    MAX_ALT_TEXT_CHARS,
    MAX_STATIC_BYTES,
    MediaType,
    clean_media_text,
    has_media_signature,
)
from ai_trend_pipeline.media.providers import MediaContentUnsupported

WIDTH, HEIGHT = CARD_SQUARE
MARGIN = 40
SHADOW = 12
BORDER = 6
PANEL_INSET = BORDER + 40
PANEL_GAP = 28
TEXT_GAP = 24
FOOT_SIZE = 22
RULE = 3
RULE_GAP = 16
BAND_PAD = 10
MIN_IMAGE_SIDE = 256
MAX_IMAGE_SIDE = 4096
EDGE = WIDTH - MARGIN - SHADOW  # 1028: right and bottom edge of every boxed element
PALETTE = CARD_THEMES[CardTheme.LIGHT]

Rgb = tuple[int, int, int]


def _rgb(value: str) -> Rgb:
    return ImageColor.getrgb(value)[:3]


@dataclass(frozen=True)
class Element:
    """One text element in the panel, fitted from `sizes` (largest first)."""

    text: str
    path: Path
    sizes: tuple[int, ...]
    line_height: float
    max_lines: int
    colour: str
    gap_before: int = 0
    band: bool = False  # drawn on a marker band; one line, never ellipsised
    optional: bool = False  # may be dropped entirely when nothing fits


@dataclass(frozen=True)
class PlacedElement:
    path: Path
    size: int
    colour: str
    lines: tuple[TextLine, ...]
    band: Box | None


@dataclass(frozen=True)
class SlideLayout:
    art: Box
    panel: Box
    content: Box
    footer_top: int
    elements: tuple[PlacedElement, ...]
    counter: TextLine
    wordmark: TextLine
    truncated: bool


def sizes_between(start: int, stop: int, step: int) -> tuple[int, ...]:
    sizes = list(range(start, stop, -step))
    if not sizes or sizes[-1] != stop:
        sizes.append(stop)
    return tuple(sizes)


def clean_main(text: str) -> str:
    cleaned = clean_text(text)
    if not cleaned:
        raise MediaContentUnsupported("slide text has nothing the fonts can draw")
    if not is_supported(cleaned):
        raise MediaContentUnsupported("slide text has characters the fonts cannot draw")
    return cleaned


def clean_optional(text: str | None) -> str | None:
    if text is None:
        return None
    cleaned = clean_text(text)
    return cleaned if cleaned and is_supported(cleaned) else None


def _height(element: Element, size: int, count: int) -> int:
    if element.band:
        return sum(load_font(element.path, size).getmetrics())
    return count * round(size * element.line_height)


def _line_height(element: Element, size: int) -> int:
    return _height(element, size, 1)


def _total(elements: Sequence[Element], sizes: Sequence[int], counts: Sequence[int]) -> int:
    total = 0
    for e, s, c in zip(elements, sizes, counts, strict=True):
        if c:
            total += (e.gap_before if total else 0) + _height(e, s, c)
    return total


def _fit(
    elements: Sequence[Element], width: int, available: int
) -> tuple[list[int], list[list[str]], bool]:
    """Pick sizes and wrapped lines. Returns (sizes, lines per element, truncated)."""

    def room(e: Element) -> int:
        return width - 2 * BAND_PAD if e.band else width

    for sizes in itertools.product(*(e.sizes for e in elements)):
        wrapped = [
            wrap_text(e.text, load_font(e.path, s), room(e))
            for e, s in zip(elements, sizes, strict=True)
        ]
        if any(len(w) > e.max_lines for w, e in zip(wrapped, elements, strict=True)):
            continue
        if _total(elements, sizes, [len(w) for w in wrapped]) <= available:
            return list(sizes), wrapped, False

    sizes = [e.sizes[-1] for e in elements]
    wrapped = [
        wrap_text(e.text, load_font(e.path, s), room(e))
        for e, s in zip(elements, sizes, strict=True)
    ]
    for e, w in zip(elements, wrapped, strict=True):
        if e.band and len(w) > 1:
            raise MediaContentUnsupported("slide number does not fit on one line")
    counts = [min(len(w), e.max_lines) for w, e in zip(wrapped, elements, strict=True)]
    # Take lines from the last element first, then upwards; keep one line of each main element.
    for index in reversed(range(len(elements))):
        floor = 0 if elements[index].optional else 1
        while counts[index] > floor and _total(elements, sizes, counts) > available:
            counts[index] -= 1
    result: list[list[str]] = []
    for e, s, w, c in zip(elements, sizes, wrapped, counts, strict=True):
        lines = w[:c]
        if lines and c < len(w):
            lines[-1] = ellipsise(lines[-1], load_font(e.path, s), room(e))
        result.append(lines)
    return sizes, result, True


def _inside(inner: Box, outer: Box) -> bool:
    return (
        inner.left >= outer.left
        and inner.top >= outer.top
        and inner.right <= outer.right
        and inner.bottom <= outer.bottom
    )


def _union(lines: Sequence[TextLine]) -> Box:
    return Box(
        min(line.ink.left for line in lines),
        min(line.ink.top for line in lines),
        max(line.ink.right for line in lines),
        max(line.ink.bottom for line in lines),
    )


def _check(layout: SlideLayout) -> None:
    """Invariants. A failure here is a layout bug, not bad input."""
    content = layout.content
    for placed in layout.elements:
        for line in placed.lines:
            assert _inside(line.ink, content), f"text ink {line.ink} escapes {content}"
        if placed.band is not None:
            assert _inside(placed.band, content), f"band {placed.band} escapes {content}"
    for line in (layout.counter, layout.wordmark):
        assert _inside(line.ink, content), f"footer ink {line.ink} escapes {content}"
    boxes = [placed.band or _union(placed.lines) for placed in layout.elements if placed.lines]
    for box in boxes:
        assert box.bottom <= layout.footer_top, "text runs into the footer"
    boxes.sort(key=lambda box: box.top)
    for upper, lower in zip(boxes, boxes[1:], strict=False):
        assert upper.bottom <= lower.top, "text blocks overlap"


def layout_slide(
    art_bottom: int, elements: Sequence[Element], position: int, total: int
) -> SlideLayout:
    panel = Box(MARGIN, art_bottom + PANEL_GAP, EDGE, EDGE)
    content = Box(
        panel.left + PANEL_INSET,
        panel.top + PANEL_INSET,
        panel.right - PANEL_INSET,
        panel.bottom - PANEL_INSET,
    )
    foot_font = load_font(BODY_FONT_PATH, FOOT_SIZE)
    mark_font = load_font(HEADLINE_FONT_PATH, FOOT_SIZE)
    footer_top = content.bottom - (RULE + RULE_GAP + sum(foot_font.getmetrics()))
    foot_y = footer_top + RULE + RULE_GAP
    mark_w = round(mark_font.getlength(WORDMARK))
    wordmark = place_line(WORDMARK, content.right - mark_w, foot_y, mark_font, content.right)
    counter = place_line(f"{position} / {total}", content.left, foot_y, foot_font, content.right)

    available = footer_top - TEXT_GAP - content.top
    content_w = content.right - content.left
    sizes, lines, truncated = _fit(elements, content_w, available)
    placed: list[PlacedElement] = []
    y = content.top
    for e, size, texts in zip(elements, sizes, lines, strict=True):
        if not texts:
            continue
        if placed:
            y += e.gap_before
        font = load_font(e.path, size)
        lh = _line_height(e, size)
        x = content.left + (BAND_PAD if e.band else 0)
        right = content.right - (BAND_PAD if e.band else 0)
        placed_lines = tuple(
            place_line(text, x, y + i * lh, font, right) for i, text in enumerate(texts)
        )
        band: Box | None = None
        if e.band:
            band = Box(content.left, y, x + round(font.getlength(texts[0])) + BAND_PAD, y + lh)
        placed.append(PlacedElement(e.path, size, e.colour, placed_lines, band))
        y += lh * len(texts)
    layout = SlideLayout(
        art=Box(MARGIN, MARGIN, EDGE, art_bottom),
        panel=panel,
        content=content,
        footer_top=footer_top,
        elements=tuple(placed),
        counter=counter,
        wordmark=wordmark,
        truncated=truncated,
    )
    _check(layout)
    return layout


def fit_art(image_png: bytes, box: Box) -> Image.Image:
    """Decode, validate, flatten and cover-fit PNG art into `box`."""
    message = "slide image is not a usable PNG"
    if not has_media_signature(image_png, MediaType.PNG):
        raise MediaContentUnsupported(message)
    source: Image.Image
    try:
        opened = Image.open(io.BytesIO(image_png))
        width, height = opened.size
        if not all(MIN_IMAGE_SIDE <= side <= MAX_IMAGE_SIDE for side in (width, height)):
            raise MediaContentUnsupported("slide image size is out of range")
        opened.load()
        if opened.mode in ("RGBA", "LA", "PA") or "transparency" in opened.info:
            rgba = opened.convert("RGBA")
            flat = Image.new("RGB", rgba.size, _rgb(PALETTE.panel))
            flat.paste(rgba, mask=rgba.getchannel("A"))
            source = flat
        else:
            source = opened.convert("RGB")
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        raise MediaContentUnsupported(message) from None
    target_w, target_h = box.right - box.left, box.bottom - box.top
    scale = max(target_w / width, target_h / height)
    new_w, new_h = max(target_w, round(width * scale)), max(target_h, round(height * scale))
    # Pillow's stub for `resize` has a partly unknown `size` type.
    scaled = source.resize((new_w, new_h), Image.Resampling.LANCZOS)  # pyright: ignore[reportUnknownMemberType]
    left, top = (new_w - target_w) // 2, (new_h - target_h) // 2
    return scaled.crop((left, top, left + target_w, top + target_h))


def draw_slide(layout: SlideLayout, art: Image.Image) -> Image.Image:
    ink = _rgb(PALETTE.text)
    image = Image.new("RGB", (WIDTH, HEIGHT), _rgb(PALETTE.ground))
    draw = ImageDraw.Draw(image)
    for x in range(0, WIDTH, 24):
        draw.line([(x, 0), (x, HEIGHT - 1)], fill=_rgb(PALETTE.grid), width=1)
    for y in range(0, HEIGHT, 24):
        draw.line([(0, y), (WIDTH - 1, y)], fill=_rgb(PALETTE.grid), width=1)

    a, p = layout.art, layout.panel
    for b in (a, p):
        draw.rectangle(
            [b.left + SHADOW, b.top + SHADOW, b.right - 1 + SHADOW, b.bottom - 1 + SHADOW],
            fill=ink,
        )
    image.paste(art, (a.left, a.top))
    draw.rectangle([a.left, a.top, a.right - 1, a.bottom - 1], outline=ink, width=BORDER)
    draw.rectangle(
        [p.left, p.top, p.right - 1, p.bottom - 1],
        fill=_rgb(PALETTE.panel),
        outline=ink,
        width=BORDER,
    )
    for placed in layout.elements:
        if placed.band is not None:
            b = placed.band
            draw.rectangle([b.left, b.top, b.right - 1, b.bottom - 1], fill=_rgb(PALETTE.marker))
        font = load_font(placed.path, placed.size)
        for line in placed.lines:
            draw.text((line.x, line.y), line.text, font=font, fill=_rgb(placed.colour), anchor="la")

    c = layout.content
    draw.rectangle([c.left, layout.footer_top, c.right - 1, layout.footer_top + RULE - 1], fill=ink)
    draw.text(
        (layout.counter.x, layout.counter.y),
        layout.counter.text,
        font=load_font(BODY_FONT_PATH, FOOT_SIZE),
        fill=_rgb(PALETTE.muted),
        anchor="la",
    )
    draw.text(
        (layout.wordmark.x, layout.wordmark.y),
        layout.wordmark.text,
        font=load_font(HEADLINE_FONT_PATH, FOOT_SIZE),
        fill=_rgb(PALETTE.accent),
        anchor="la",
    )
    return image


def encode_within(image: Image.Image, limit: int = MAX_STATIC_BYTES) -> bytes:
    """Lossless PNG first, then fewer colours (median cut, no dither) until it fits `limit`."""
    data = encode_png(image)
    if len(data) <= limit:
        return data
    for colours in (256, 128, 64):
        reduced = image.quantize(
            colors=colours, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE
        )
        data = encode_png(reduced)
        if len(data) <= limit:
            return data
    raise MediaContentUnsupported("slide is too large")


def _sentence(text: str) -> str:
    return text if text.endswith((".", "!", "?")) else f"{text}."


def slide_alt_text(template: SlideTemplateName, overlay: SlideOverlay) -> str:
    parts = [f"Slide {overlay.position} of {overlay.total}."]
    caption = overlay.caption
    if template is SlideTemplateName.NUMBER:
        parts.append(_sentence(f"{overlay.text}: {caption}" if caption else overlay.text))
    elif template is SlideTemplateName.SOURCE:
        parts.append(_sentence(f"Source: {overlay.text}"))
        if caption:
            parts.append(_sentence(caption))
    else:
        parts.append(_sentence(overlay.text))
    if overlay.image_description:
        parts.append(f"Illustration: {_sentence(overlay.image_description)}")
    joined = " ".join(parts)
    try:
        cleaned = clean_media_text(joined)
    except ValueError:
        # The words of each field were already checked; joining can only create a false hit
        # (for example a field ending in "www" followed by the added full stop).
        cleaned = " ".join(joined.split())
    assert isinstance(cleaned, str)
    return cleaned[:MAX_ALT_TEXT_CHARS]


def finish(
    template: SlideTemplateName,
    layout: SlideLayout,
    art: Image.Image,
    overlay: SlideOverlay,
) -> ComposedSlide:
    png = encode_within(draw_slide(layout, art))
    return ComposedSlide(
        template=template,
        png=png,
        width=WIDTH,
        height=HEIGHT,
        alt_text=slide_alt_text(template, overlay),
        truncated=layout.truncated,
    )
