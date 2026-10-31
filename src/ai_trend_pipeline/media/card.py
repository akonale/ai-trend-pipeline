"""Deterministic story card: pure layout, Pillow drawing and PNG encoding with vendored fonts."""

import io
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ai_trend_pipeline.media.fonts import (
    BODY_FONT_PATH,
    HEADLINE_FONT_PATH,
    load_font,
    supported_codepoints,
)
from ai_trend_pipeline.media.models import MediaBrief, MediaType
from ai_trend_pipeline.media.providers import MediaContentUnsupported, RenderedMedia

CARD_TEMPLATE_VERSION = "1"
GIF_TEMPLATE_VERSION = "1"
CARD_LANDSCAPE = (1200, 675)
CARD_SQUARE = (1080, 1080)
CARD_MIN = (640, 360)
MAX_CARD_SIDE = 2048
ELLIPSIS = "…"
WORDMARK = "Topic Studio"
MAX_HEADLINE_LINES = 4
MAX_BODY_LINES = 4

Rgb = tuple[int, int, int]


class CardTheme(StrEnum):
    LIGHT = "light"
    DARK = "dark"


@dataclass(frozen=True)
class CardPalette:
    ground: str
    grid: str
    panel: str
    text: str  # headline text, panel border, footer rule and shadow
    muted: str
    accent: str
    marker: str
    on_marker: str


# Values copied from `web/static/app.css`; a test keeps them in sync.
CARD_THEMES: dict[CardTheme, CardPalette] = {
    CardTheme.LIGHT: CardPalette(
        ground="#e3ecdf",
        grid="#d2dfcc",
        panel="#ffffff",
        text="#15172b",
        muted="#4a4f5e",
        accent="#2448d8",
        marker="#ffc93c",
        on_marker="#15172b",
    ),
    CardTheme.DARK: CardPalette(
        ground="#191c30",
        grid="#23273f",
        panel="#262a44",
        text="#e3ecdf",
        muted="#b4bacb",
        accent="#8daeff",
        marker="#ffc93c",
        on_marker="#15172b",
    ),
}


def _rgb(value: str) -> Rgb:
    return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16))


@dataclass(frozen=True)
class Box:
    left: int
    top: int
    right: int
    bottom: int


@dataclass(frozen=True)
class TextLine:
    text: str
    x: int
    y: int
    ink: Box


@dataclass(frozen=True)
class TextBlock:
    font_size: int
    line_height: int
    lines: tuple[TextLine, ...]


@dataclass(frozen=True)
class CardLayout:
    width: int
    height: int
    theme: CardTheme
    content_box: Box
    footer_top: int
    headline: TextBlock
    body: TextBlock | None
    body_is_fact: bool
    bands: tuple[Box, ...]
    source: TextLine | None
    wordmark: TextLine
    truncated: bool


_SCRATCH = ImageDraw.Draw(Image.new("L", (1, 1)))
_STRIPPABLE = frozenset({"So", "Sk", "Cf", "Mn", "Me"})


def _scale(width: int, height: int) -> float:
    return min(width / 1200, height / 675)


def _px(value: float, scale: float, minimum: int = 1) -> int:
    return max(minimum, round(value * scale))


def _sizes(start: int, stop: int, step: int) -> list[int]:
    sizes = list(range(start, stop, -step))
    if not sizes or sizes[-1] != stop:
        sizes.append(stop)
    return sizes


def _coverage() -> frozenset[int]:
    return supported_codepoints(HEADLINE_FONT_PATH) & supported_codepoints(BODY_FONT_PATH)


def _clean(text: str) -> str:
    """NFC, drop uncovered symbols/format/combining characters, collapse spaces."""
    covered = _coverage()
    kept = [
        char
        for char in unicodedata.normalize("NFC", text)
        if ord(char) in covered or unicodedata.category(char) not in _STRIPPABLE
    ]
    return " ".join("".join(kept).split())


def _supported(text: str) -> bool:
    covered = _coverage()
    return all(char == " " or ord(char) in covered for char in text)


def _optional_text(text: str | None) -> str | None:
    """Optional elements are left out when empty or when any letter cannot be drawn."""
    if text is None:
        return None
    cleaned = _clean(text)
    return cleaned if cleaned and _supported(cleaned) else None


def _break_word(word: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    parts: list[str] = []
    current = ""
    for char in word:
        if current and font.getlength(current + char) > width:
            parts.append(current)
            current = char
        else:
            current += char
    if current:
        parts.append(current)
    return parts


def _wrap(text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}" if current else word
        if font.getlength(candidate) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        if font.getlength(word) <= width:
            current = word
            continue
        pieces = _break_word(word, font, width)
        lines.extend(pieces[:-1])
        current = pieces[-1]
    if current:
        lines.append(current)
    return lines


def _ellipsise(text: str, font: ImageFont.FreeTypeFont, width: int) -> str:
    """Trim words, then characters, until `text…` fits `width`."""
    words = text.split()
    while words:
        candidate = " ".join(words).rstrip() + ELLIPSIS
        if font.getlength(candidate) <= width:
            return candidate
        if len(words) == 1:
            break
        words.pop()
    chars = words[0] if words else ""
    while chars:
        candidate = chars + ELLIPSIS
        if font.getlength(candidate) <= width:
            return candidate
        chars = chars[:-1]
    return ELLIPSIS


def _line(text: str, x: int, y: int, font: ImageFont.FreeTypeFont, right_limit: int) -> TextLine:
    """Place a line at (x, y); nudge it so the ink stays between `x` and `right_limit`."""
    left, top, right, bottom = _SCRATCH.textbbox((x, y), text, font=font, anchor="la")
    shift = 0
    if left < x:
        shift = x - int(left)
    elif right > right_limit:
        shift = right_limit - int(right)
    return TextLine(
        text,
        x + shift,
        y,
        Box(int(left) + shift, int(top), int(right) + shift, int(bottom)),
    )


def _block(
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    size: int,
    x: int,
    top: int,
    line_height: int,
    right_limit: int,
) -> TextBlock:
    return TextBlock(
        size,
        line_height,
        tuple(
            _line(text, x, top + i * line_height, font, right_limit) for i, text in enumerate(lines)
        ),
    )


def _inside(inner: Box, outer: Box) -> bool:
    return (
        inner.left >= outer.left
        and inner.top >= outer.top
        and inner.right <= outer.right
        and inner.bottom <= outer.bottom
    )


def _union(lines: tuple[TextLine, ...]) -> Box:
    return Box(
        min(line.ink.left for line in lines),
        min(line.ink.top for line in lines),
        max(line.ink.right for line in lines),
        max(line.ink.bottom for line in lines),
    )


def _panel_box(width: int, height: int) -> Box:
    s = _scale(width, height)
    margin, shadow = _px(40, s), _px(12, s)
    return Box(margin, margin, width - margin - shadow, height - margin - shadow)


def layout_story_card(brief: MediaBrief, theme: CardTheme, width: int, height: int) -> CardLayout:
    """Place all text and bands. Pure: no drawing. Raises `MediaContentUnsupported`."""
    headline_text = _clean(brief.headline)
    if not headline_text:
        raise MediaContentUnsupported("headline has nothing the card fonts can draw")
    if not _supported(headline_text):
        raise MediaContentUnsupported("headline has characters the card fonts cannot draw")
    body_text = _optional_text(brief.highlight)
    source_text = _optional_text(brief.source_label)
    is_fact = body_text is not None and bool(brief.highlight_fact_ids)

    s = _scale(width, height)
    panel = _panel_box(width, height)
    inset = _px(6, s) + _px(48, s)
    content = Box(panel.left + inset, panel.top + inset, panel.right - inset, panel.bottom - inset)
    content_w = content.right - content.left

    # Footer, pinned to the bottom of the content box.
    foot_size = _px(22, s)
    foot_font = load_font(BODY_FONT_PATH, foot_size)
    mark_font = load_font(HEADLINE_FONT_PATH, foot_size)
    rule, rule_gap = _px(3, s), _px(16, s)
    footer_top = content.bottom - (rule + rule_gap + sum(foot_font.getmetrics()))
    foot_y = footer_top + rule + rule_gap
    mark_w = round(mark_font.getlength(WORDMARK))
    wordmark = _line(WORDMARK, content.right - mark_w, foot_y, mark_font, content.right)
    source: TextLine | None = None
    if source_text is not None:
        room = content_w - mark_w - _px(24, s)
        label = f"Source: {source_text}"
        if foot_font.getlength(label) > room:
            label = _ellipsise(label, foot_font, room)
        if label != ELLIPSIS and foot_font.getlength(label) <= room:
            source = _line(label, content.left, foot_y, foot_font, content.right)

    # Fit headline and body sizes.
    gap = _px(28, s)
    pad = _px(10, s)
    available = footer_top - gap - content.top
    body_x = content.left + (pad if is_fact else 0)
    body_w = content_w - (2 * pad if is_fact else 0)
    body_lh_factor = 1.4 if is_fact else 1.35
    head_sizes = _sizes(_px(72, s), _px(40, s), _px(2, s))
    body_sizes = _sizes(_px(32, s), _px(22, s), _px(2, s))

    def head_lh(size: int) -> int:
        return round(size * 1.08)

    def body_lh(size: int) -> int:
        return round(size * body_lh_factor)

    chosen: tuple[int, list[str], int | None, list[str]] | None = None
    for hs in head_sizes:
        h_lines = _wrap(headline_text, load_font(HEADLINE_FONT_PATH, hs), content_w)
        if len(h_lines) > MAX_HEADLINE_LINES:
            continue
        h_height = len(h_lines) * head_lh(hs)
        if body_text is None:
            if h_height <= available:
                chosen = (hs, h_lines, None, [])
                break
            continue
        for bs in body_sizes:
            b_lines = _wrap(body_text, load_font(BODY_FONT_PATH, bs), body_w)
            if len(b_lines) > MAX_BODY_LINES:
                continue
            if h_height + gap + len(b_lines) * body_lh(bs) <= available:
                chosen = (hs, h_lines, bs, b_lines)
                break
        if chosen is not None:
            break

    truncated = False
    if chosen is None:
        truncated = True
        hs = head_sizes[-1]
        h_font = load_font(HEADLINE_FONT_PATH, hs)
        h_lines = _wrap(headline_text, h_font, content_w)
        if len(h_lines) > MAX_HEADLINE_LINES:
            h_lines = h_lines[:MAX_HEADLINE_LINES]
            h_lines[-1] = _ellipsise(h_lines[-1], h_font, content_w)
        bs = body_sizes[-1]
        b_lines: list[str] = []
        if body_text is not None:
            b_font = load_font(BODY_FONT_PATH, bs)
            b_lines = _wrap(body_text, b_font, body_w)
            room_h = available - len(h_lines) * head_lh(hs) - gap
            keep = min(MAX_BODY_LINES, max(0, room_h // body_lh(bs)))
            if keep < len(b_lines):
                b_lines = b_lines[:keep]
                if b_lines:
                    b_lines[-1] = _ellipsise(b_lines[-1], b_font, body_w)
        chosen = (hs, h_lines, bs if b_lines else None, b_lines)

    hs, h_lines, bs, b_lines = chosen
    headline = _block(
        h_lines,
        load_font(HEADLINE_FONT_PATH, hs),
        hs,
        content.left,
        content.top,
        head_lh(hs),
        content.right,
    )
    body: TextBlock | None = None
    bands: list[Box] = []
    if bs is not None and b_lines:
        b_font = load_font(BODY_FONT_PATH, bs)
        lh = body_lh(bs)
        top = content.top + len(h_lines) * head_lh(hs) + gap
        body = _block(b_lines, b_font, bs, body_x, top, lh, content.right)
        if is_fact:
            trim = round(0.1 * lh)
            for i, text in enumerate(b_lines):
                right = content.left + pad + round(b_font.getlength(text)) + pad
                line_top = top + i * lh
                bands.append(
                    Box(
                        content.left,
                        line_top + trim,
                        min(right, content.right),
                        line_top + lh - trim,
                    )
                )

    layout = CardLayout(
        width=width,
        height=height,
        theme=theme,
        content_box=content,
        footer_top=footer_top,
        headline=headline,
        body=body,
        body_is_fact=is_fact and body is not None,
        bands=tuple(bands),
        source=source,
        wordmark=wordmark,
        truncated=truncated,
    )
    _check(layout)
    return layout


def _check(layout: CardLayout) -> None:
    """Invariants. A failure here is a layout bug, not bad input."""
    content = layout.content_box
    inks = [line.ink for b in (layout.headline, layout.body) if b for line in b.lines]
    inks += [line.ink for line in (layout.source, layout.wordmark) if line is not None]
    for ink in inks:
        assert _inside(ink, content), f"text ink {ink} escapes content box {content}"
    for band in layout.bands:
        assert _inside(band, content), f"band {band} escapes content box {content}"
    for block in (layout.headline, layout.body):
        if block is not None:
            assert _union(block.lines).bottom <= layout.footer_top, "text runs into the footer"
    if layout.body is not None:
        assert _union(layout.body.lines).top > _union(layout.headline.lines).bottom, (
            "body overlaps the headline"
        )
    ordered = sorted(layout.bands, key=lambda band: band.top)
    for upper, lower in zip(ordered, ordered[1:], strict=False):
        assert upper.bottom <= lower.top, "bands overlap"


def draw_story_card(layout: CardLayout) -> Image.Image:
    colours = CARD_THEMES[layout.theme]
    s = _scale(layout.width, layout.height)
    image = Image.new("RGB", (layout.width, layout.height), _rgb(colours.ground))
    draw = ImageDraw.Draw(image)
    step = _px(24, s, 12)
    for x in range(0, layout.width, step):
        draw.line([(x, 0), (x, layout.height - 1)], fill=_rgb(colours.grid), width=1)
    for y in range(0, layout.height, step):
        draw.line([(0, y), (layout.width - 1, y)], fill=_rgb(colours.grid), width=1)

    shadow = _px(12, s)
    p = _panel_box(layout.width, layout.height)
    draw.rectangle(
        [p.left + shadow, p.top + shadow, p.right - 1 + shadow, p.bottom - 1 + shadow],
        fill=_rgb(colours.text),
    )
    draw.rectangle(
        [p.left, p.top, p.right - 1, p.bottom - 1],
        fill=_rgb(colours.panel),
        outline=_rgb(colours.text),
        width=_px(6, s),
    )
    for b in layout.bands:
        draw.rectangle([b.left, b.top, b.right - 1, b.bottom - 1], fill=_rgb(colours.marker))

    def put(block: TextBlock, path: Path, fill: str) -> None:
        font = load_font(path, block.font_size)
        for line in block.lines:
            draw.text((line.x, line.y), line.text, font=font, fill=_rgb(fill), anchor="la")

    put(layout.headline, HEADLINE_FONT_PATH, colours.text)
    if layout.body is not None:
        if layout.body_is_fact:
            put(layout.body, BODY_FONT_PATH, colours.on_marker)
        else:
            put(layout.body, BODY_FONT_PATH, colours.muted)

    content = layout.content_box
    rule = _px(3, s)
    draw.rectangle(
        [content.left, layout.footer_top, content.right - 1, layout.footer_top + rule - 1],
        fill=_rgb(colours.text),
    )
    foot_size = _px(22, s)
    if layout.source is not None:
        draw.text(
            (layout.source.x, layout.source.y),
            layout.source.text,
            font=load_font(BODY_FONT_PATH, foot_size),
            fill=_rgb(colours.muted),
            anchor="la",
        )
    draw.text(
        (layout.wordmark.x, layout.wordmark.y),
        layout.wordmark.text,
        font=load_font(HEADLINE_FONT_PATH, foot_size),
        fill=_rgb(colours.accent),
        anchor="la",
    )
    return image


def encode_png(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, "PNG", optimize=True)
    return buf.getvalue()


class StoryCardRenderer:
    name = "story-card"

    def __init__(
        self,
        theme: CardTheme = CardTheme.LIGHT,
        width: int = CARD_LANDSCAPE[0],
        height: int = CARD_LANDSCAPE[1],
    ) -> None:
        if width < CARD_MIN[0] or height < CARD_MIN[1]:
            raise ValueError(f"card size must be at least {CARD_MIN[0]}x{CARD_MIN[1]}")
        if width > MAX_CARD_SIDE or height > MAX_CARD_SIDE:
            raise ValueError(f"card sides must be at most {MAX_CARD_SIDE}")
        self._theme = theme
        self._width = width
        self._height = height
        self.version = f"{CARD_TEMPLATE_VERSION}.{GIF_TEMPLATE_VERSION}-{theme}-{width}x{height}"

    def render_static(self, brief: MediaBrief) -> RenderedMedia:
        layout = layout_story_card(brief, self._theme, self._width, self._height)
        data = encode_png(draw_story_card(layout))
        return RenderedMedia(data, MediaType.PNG, self._width, self._height, None)

    def render_animated(self, brief: MediaBrief) -> RenderedMedia:
        # Imported here because `animation` imports this module for the card drawing code.
        from ai_trend_pipeline.media.animation import (
            GIF_DURATION_MS,
            MAX_GIF_SIDE,
            build_story_frames,
            encode_gif,
        )

        if self._width > MAX_GIF_SIDE or self._height > MAX_GIF_SIDE:
            raise MediaContentUnsupported(f"animated cards are limited to {MAX_GIF_SIDE}px")
        layout = layout_story_card(brief, self._theme, self._width, self._height)
        frames = build_story_frames(layout, brief.animation_beats)
        data = encode_gif(frames)
        return RenderedMedia(data, MediaType.GIF, self._width, self._height, GIF_DURATION_MS)
