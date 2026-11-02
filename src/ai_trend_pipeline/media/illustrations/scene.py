"""The illustrated card scene: seeded background, headline panel and the drawing.

Layout is pure (`layout_scene`). Drawing uses resvg for the anti-aliased background shapes and
Pillow plus the vendored Archivo fonts for the panel and text. Every choice comes from the SHA-256
of the story content hash, never from `random`.
"""

import hashlib
import io
import re
from dataclasses import dataclass
from enum import StrEnum

from PIL import Image, ImageDraw

from ai_trend_pipeline.media.card import (
    WORDMARK,
    Box,
    TextBlock,
    TextLine,
    clean_text,
    ellipsise,
    is_supported,
    place_line,
    wrap_text,
)
from ai_trend_pipeline.media.fonts import BODY_FONT_PATH, HEADLINE_FONT_PATH, load_font
from ai_trend_pipeline.media.illustrations.rasterise import rasterise_svg
from ai_trend_pipeline.media.providers import MediaContentUnsupported

SCENE_TEMPLATE_VERSION = "1"
SCENE_SIZE = (1200, 675)
MAX_HEADLINE_LINES = 5
ILLUSTRATION_BOX = 440
BACKDROP_SIZE = 460
TILTED_SIZE = 456
SHADOW = 12
BORDER = 6
SPACINGS = (24, 32, 40)
GRID = 40
SEED_PREFIX = b"illustrated-card-v1|"

# Copied from `web/static/app.css` (light theme); a test keeps them in sync.
HEX = r"#[0-9a-f]{6}"


@dataclass(frozen=True)
class ScenePalette:
    ground: str
    grid: str
    paper: str
    ink: str
    accent: str
    supports: tuple[str, str, str, str]


SCENE_PALETTE = ScenePalette(
    ground="#e3ecdf",
    grid="#d2dfcc",
    paper="#ffffff",
    ink="#15172b",
    accent="#2448d8",
    supports=("#ffc93c", "#a8c8ff", "#7ddba3", "#c7b8ff"),
)

PANEL = Box(40, 40, 640, SCENE_SIZE[1] - 40 - SHADOW)
ART_AREA = Box(680, 40, 1160, PANEL.bottom)
BACKDROP_CENTRE = ((ART_AREA.left + ART_AREA.right) // 2, (ART_AREA.top + ART_AREA.bottom) // 2)


class PatternFamily(StrEnum):
    DOTS = "dots"
    STRIPES = "stripes"
    ARCS = "arcs"
    CHECKER = "checker"


class BackdropShape(StrEnum):
    CIRCLE = "circle"
    ROUNDED_SQUARE = "rounded-square"
    TILTED_SQUARE = "tilted-square"


class AccentKind(StrEnum):
    DOT = "dot"
    RING = "ring"
    BAR = "bar"


@dataclass(frozen=True)
class Accent:
    kind: AccentKind
    x: int
    y: int


@dataclass(frozen=True)
class SceneSpec:
    pattern: PatternFamily
    spacing: int
    backdrop_colour: str  # first support colour: the shape under the drawing
    drawing_colour: str  # second support colour: the drawing's `support` slot and the burst
    backdrop_shape: BackdropShape
    tilt: int
    accents: tuple[Accent, ...]
    asset_pick: int


class SeedStream:
    """Deterministic bytes: `sha256(seed + counter)` blocks, read one byte at a time."""

    def __init__(self, content_hash: str) -> None:
        self._seed = hashlib.sha256(SEED_PREFIX + content_hash.encode("ascii")).digest()
        self._index = 0

    def next_byte(self) -> int:
        block, offset = divmod(self._index, 32)
        self._index += 1
        return hashlib.sha256(self._seed + block.to_bytes(4, "big")).digest()[offset]


def scene_spec(content_hash: str) -> SceneSpec:
    stream = SeedStream(content_hash)
    patterns = list(PatternFamily)
    shapes = list(BackdropShape)
    supports = SCENE_PALETTE.supports
    pattern = patterns[stream.next_byte() % len(patterns)]
    spacing = SPACINGS[stream.next_byte() % len(SPACINGS)]
    first = stream.next_byte() % len(supports)
    second = (first + 1 + stream.next_byte() % (len(supports) - 1)) % len(supports)
    shape = shapes[stream.next_byte() % len(shapes)]
    tilt = 6 if stream.next_byte() % 2 == 0 else -6
    accents: list[Accent] = []
    kinds = list(AccentKind)
    for _ in range(2 + stream.next_byte() % 2):
        kind = kinds[stream.next_byte() % len(kinds)]
        column = stream.next_byte() % ((ART_AREA.right - ART_AREA.left) // GRID)
        row = stream.next_byte() % ((ART_AREA.bottom - ART_AREA.top) // GRID)
        accents.append(Accent(kind, ART_AREA.left + GRID * column, ART_AREA.top + GRID * row))
    return SceneSpec(
        pattern=pattern,
        spacing=spacing,
        backdrop_colour=supports[first],
        drawing_colour=supports[second],
        backdrop_shape=shape,
        tilt=tilt,
        accents=tuple(accents),
        asset_pick=stream.next_byte(),
    )


@dataclass(frozen=True)
class SceneLayout:
    spec: SceneSpec
    content: Box
    footer_top: int
    headline: TextBlock
    wordmark: TextLine
    art_area: Box
    backdrop_centre: tuple[int, int]
    illustration: Box | None
    truncated: bool


def fit_box(aspect: float, limit: int = ILLUSTRATION_BOX) -> tuple[int, int]:
    """Largest width, height inside `limit` x `limit` with the given aspect (width / height)."""
    if aspect >= 1:
        return limit, max(1, round(limit / aspect))
    return max(1, round(limit * aspect)), limit


def _head_sizes() -> list[int]:
    return list(range(64, 35, -2))


def layout_scene(headline: str, spec: SceneSpec, aspect: float | None) -> SceneLayout:
    """Place the headline, footer and art. Pure. Raises `MediaContentUnsupported`."""
    text = clean_text(headline)
    if not text:
        raise MediaContentUnsupported("headline has nothing the card fonts can draw")
    if not is_supported(text):
        raise MediaContentUnsupported("headline has characters the card fonts cannot draw")
    inset = BORDER + 40
    content = Box(PANEL.left + inset, PANEL.top + inset, PANEL.right - inset, PANEL.bottom - inset)
    width = content.right - content.left

    foot_size = 22
    foot_font = load_font(BODY_FONT_PATH, foot_size)
    mark_font = load_font(HEADLINE_FONT_PATH, foot_size)
    rule, rule_gap, gap = 3, 16, 28
    footer_top = content.bottom - (rule + rule_gap + sum(foot_font.getmetrics()))
    mark_width = round(mark_font.getlength(WORDMARK))
    wordmark = place_line(
        WORDMARK,
        content.right - mark_width,
        footer_top + rule + rule_gap,
        mark_font,
        content.right,
    )
    available = footer_top - gap - content.top

    def line_height(size: int) -> int:
        return round(size * 1.08)

    chosen: tuple[int, list[str]] | None = None
    for size in _head_sizes():
        lines = wrap_text(text, load_font(HEADLINE_FONT_PATH, size), width)
        if len(lines) <= MAX_HEADLINE_LINES and len(lines) * line_height(size) <= available:
            chosen = (size, lines)
            break
    truncated = chosen is None
    if chosen is None:
        size = _head_sizes()[-1]
        font = load_font(HEADLINE_FONT_PATH, size)
        lines = wrap_text(text, font, width)
        if len(lines) > MAX_HEADLINE_LINES:
            lines = lines[:MAX_HEADLINE_LINES]
            lines[-1] = ellipsise(lines[-1], font, width)
        chosen = (size, lines)
    size, lines = chosen
    font = load_font(HEADLINE_FONT_PATH, size)
    lh = line_height(size)
    top = content.top + max(0, (available - len(lines) * lh) // 2)
    block = TextBlock(
        size,
        lh,
        tuple(
            place_line(line, content.left, top + i * lh, font, content.right)
            for i, line in enumerate(lines)
        ),
    )

    illustration: Box | None = None
    if aspect is not None:
        w, h = fit_box(aspect)
        cx, cy = BACKDROP_CENTRE
        illustration = Box(cx - w // 2, cy - h // 2, cx - w // 2 + w, cy - h // 2 + h)
    layout = SceneLayout(
        spec=spec,
        content=content,
        footer_top=footer_top,
        headline=block,
        wordmark=wordmark,
        art_area=ART_AREA,
        backdrop_centre=BACKDROP_CENTRE,
        illustration=illustration,
        truncated=truncated,
    )
    _check(layout)
    return layout


def _inside(inner: Box, outer: Box) -> bool:
    return (
        inner.left >= outer.left
        and inner.top >= outer.top
        and inner.right <= outer.right
        and inner.bottom <= outer.bottom
    )


def _check(layout: SceneLayout) -> None:
    """Invariants. A failure here is a layout bug, not bad input."""
    for line in (*layout.headline.lines, layout.wordmark):
        assert _inside(line.ink, layout.content), f"text ink {line.ink} escapes the content box"
    bottom = max(line.ink.bottom for line in layout.headline.lines)
    assert bottom <= layout.footer_top, "text runs into the footer rule"
    assert layout.art_area.left > PANEL.right + SHADOW, "art area overlaps the panel shadow"
    box = layout.illustration
    if box is not None:
        assert _inside(box, layout.art_area), "illustration escapes the art area"
        assert box.right - box.left <= ILLUSTRATION_BOX and box.bottom - box.top <= ILLUSTRATION_BOX


# --- drawing ------------------------------------------------------------------------------


def _pattern_svg(spec: SceneSpec) -> str:
    width, height = SCENE_SIZE
    s, grid = spec.spacing, SCENE_PALETTE.grid
    parts: list[str] = []
    if spec.pattern is PatternFamily.DOTS:
        for y in range(s // 2, height, s):
            for x in range(s // 2, width, s):
                parts.append(f'<circle cx="{x}" cy="{y}" r="3" fill="{grid}"/>')
    elif spec.pattern is PatternFamily.STRIPES:
        for k in range(-height, width, s * 2):
            parts.append(
                f'<path d="M{k},0 L{k + height},{height}" stroke="{grid}" stroke-width="8"/>'
            )
    elif spec.pattern is PatternFamily.ARCS:
        for j, y in enumerate(range(0, height, s)):
            for i, x in enumerate(range(0, width, s)):
                if (i + j) % 2 == 0:
                    parts.append(
                        f'<path d="M{x},{y} L{x + s},{y} A{s},{s} 0 0 1 {x},{y + s} Z" '
                        f'fill="{grid}"/>'
                    )
    else:
        for j, y in enumerate(range(0, height, s)):
            for i, x in enumerate(range(0, width, s)):
                if (i + j) % 2 == 0:
                    parts.append(f'<rect x="{x}" y="{y}" width="{s}" height="{s}" fill="{grid}"/>')
    return "".join(parts)


def _accents_svg(spec: SceneSpec) -> str:
    palette = SCENE_PALETTE
    box = ART_AREA
    parts = [
        f'<clipPath id="art"><rect x="{box.left}" y="{box.top}" '
        f'width="{box.right - box.left}" height="{box.bottom - box.top}"/></clipPath>',
        '<g clip-path="url(#art)">',
    ]
    for accent in spec.accents:
        cx, cy = accent.x + GRID // 2, accent.y + GRID // 2
        if accent.kind is AccentKind.DOT:
            parts.append(f'<circle cx="{cx}" cy="{cy}" r="12" fill="{palette.ink}"/>')
        elif accent.kind is AccentKind.RING:
            parts.append(
                f'<circle cx="{cx}" cy="{cy}" r="18" fill="none" '
                f'stroke="{palette.accent}" stroke-width="5"/>'
            )
        else:
            parts.append(
                f'<rect x="{cx - 28}" y="{cy - 6}" width="56" height="12" fill="{palette.accent}"/>'
            )
    parts.append("</g>")
    return "".join(parts)


def _backdrop_shape(spec: SceneSpec, fill: str, dx: int, dy: int) -> str:
    cx, cy = BACKDROP_CENTRE[0] + dx, BACKDROP_CENTRE[1] + dy
    ink = SCENE_PALETTE.ink
    stroke = f'fill="{fill}" stroke="{ink}" stroke-width="6"'
    if spec.backdrop_shape is BackdropShape.CIRCLE:
        r = BACKDROP_SIZE // 2
        return f'<circle cx="{cx}" cy="{cy}" r="{r}" {stroke}/>'
    if spec.backdrop_shape is BackdropShape.ROUNDED_SQUARE:
        half = BACKDROP_SIZE // 2
        return (
            f'<rect x="{cx - half}" y="{cy - half}" width="{BACKDROP_SIZE}" '
            f'height="{BACKDROP_SIZE}" rx="24" {stroke}/>'
        )
    half = TILTED_SIZE // 2
    return (
        f'<rect x="{cx - half}" y="{cy - half}" width="{TILTED_SIZE}" height="{TILTED_SIZE}" '
        f'transform="rotate({spec.tilt} {cx} {cy})" {stroke}/>'
    )


def _burst_svg(spec: SceneSpec) -> str:
    """An ink-outlined 8-point burst: abstract, never figurative."""
    cx, cy = BACKDROP_CENTRE
    points: list[str] = []
    # Integer vertices of a 16-point star (outer 170, inner 95), listed from the top, clockwise.
    outer, inner = 170, 95
    ring = [
        (0, -1000),
        (383, -924),
        (707, -707),
        (924, -383),
        (1000, 0),
        (924, 383),
        (707, 707),
        (383, 924),
        (0, 1000),
        (-383, 924),
        (-707, 707),
        (-924, 383),
        (-1000, 0),
        (-924, -383),
        (-707, -707),
        (-383, -924),
    ]
    for index, (ux, uy) in enumerate(ring):
        radius = outer if index % 2 == 0 else inner
        points.append(f"{cx + round(ux * radius / 1000)},{cy + round(uy * radius / 1000)}")
    return (
        f'<polygon points="{" ".join(points)}" fill="{spec.drawing_colour}" '
        f'stroke="{SCENE_PALETTE.ink}" stroke-width="6" stroke-linejoin="round"/>'
    )


def background_svg(spec: SceneSpec, with_burst: bool) -> str:
    width, height = SCENE_SIZE
    palette = SCENE_PALETTE
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" fill="{palette.ground}"/>',
        _pattern_svg(spec),
        _accents_svg(spec),
        _backdrop_shape(spec, palette.ink, SHADOW, SHADOW),
        _backdrop_shape(spec, spec.backdrop_colour, 0, 0),
    ]
    if with_burst:
        parts.append(_burst_svg(spec))
    parts.append("</svg>")
    return "".join(parts)


_VIEWBOX = re.compile(r'viewBox="\s*([-\d.]+)[ ,]+([-\d.]+)[ ,]+([-\d.]+)[ ,]+([-\d.]+)\s*"')
_ATTR_HEX = re.compile(r'="(' + HEX + r')"')


def svg_aspect(svg: str) -> float:
    match = _VIEWBOX.search(svg)
    if match is None:
        raise MediaContentUnsupported("asset has no viewBox")
    width, height = float(match.group(3)), float(match.group(4))
    if width <= 0 or height <= 0:
        raise MediaContentUnsupported("asset viewBox is empty")
    return width / height


def slot_colours(spec: SceneSpec) -> dict[str, str]:
    palette = SCENE_PALETTE
    return {
        "ink": palette.ink,
        "paper": palette.paper,
        "accent": palette.accent,
        "support": spec.drawing_colour,
    }


def recolour_svg(svg: str, recolour: dict[str, str], spec: SceneSpec) -> str:
    """Swap each mapped source colour for its slot colour in one pass."""
    slots = slot_colours(spec)
    mapping = {colour.lower(): slots[slot] for colour, slot in recolour.items()}
    return _ATTR_HEX.sub(lambda m: f'="{mapping.get(m.group(1).lower(), m.group(1))}"', svg)


def draw_scene(layout: SceneLayout, illustration_svg: str | None) -> Image.Image:
    """Draw the scene. `illustration_svg` must already be sanitised and recoloured."""
    spec = layout.spec
    background = rasterise_svg(
        background_svg(spec, with_burst=layout.illustration is None), *SCENE_SIZE
    )
    image = Image.new("RGB", SCENE_SIZE, SCENE_PALETTE.ground)
    image.paste(background.convert("RGB"))
    draw = ImageDraw.Draw(image)
    ink, paper = SCENE_PALETTE.ink, SCENE_PALETTE.paper
    p = PANEL
    draw.rectangle(
        [p.left + SHADOW, p.top + SHADOW, p.right - 1 + SHADOW, p.bottom - 1 + SHADOW], fill=ink
    )
    draw.rectangle(
        [p.left, p.top, p.right - 1, p.bottom - 1], fill=paper, outline=ink, width=BORDER
    )
    font = load_font(HEADLINE_FONT_PATH, layout.headline.font_size)
    for line in layout.headline.lines:
        draw.text((line.x, line.y), line.text, font=font, fill=ink, anchor="la")
    content = layout.content
    draw.rectangle(
        [content.left, layout.footer_top, content.right - 1, layout.footer_top + 2], fill=ink
    )
    draw.text(
        (layout.wordmark.x, layout.wordmark.y),
        layout.wordmark.text,
        font=load_font(HEADLINE_FONT_PATH, 22),
        fill=SCENE_PALETTE.accent,
        anchor="la",
    )
    box = layout.illustration
    if box is not None and illustration_svg is not None:
        art = rasterise_svg(illustration_svg, box.right - box.left, box.bottom - box.top)
        rgba = image.convert("RGBA")
        rgba.alpha_composite(art, (box.left, box.top))
        image = rgba.convert("RGB")
    return image


def encode_png(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, "PNG", optimize=True)
    return buf.getvalue()
