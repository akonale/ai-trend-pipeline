"""Deterministic story GIF: a fixed timeline of reveal strips cut from the static card.

Every frame is composited from `draw_story_card` output, so no colour outside the card palette
is introduced. The template owns the timeline; the brief only says which beats may run.
"""

import dataclasses
import io
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from PIL import Image, ImageChops

from ai_trend_pipeline.media.card import (
    Box,
    CardLayout,
    TextBlock,
    TextLine,
    _px,  # pyright: ignore[reportPrivateUsage]  # shared pixel scaling with the static card
    _scale,  # pyright: ignore[reportPrivateUsage]  # same reason
    draw_story_card,
)
from ai_trend_pipeline.media.models import AnimationBeat
from ai_trend_pipeline.media.providers import MediaContentUnsupported, MediaRenderError

GIF_DURATION_MS = 4500
OPEN_MS = 400
STEP_MS = 80
HEADLINE_STEPS = 12
HEADLINE_HOLD_MS = 400
FACT_STEPS = 10
FACT_HOLD_MS = 300
SOURCE_STEPS = 4
MAX_GIF_FRAMES = 40
GIF_MAX_COLOURS = 64
MAX_GIF_SIDE = 1200
GIF_BYTE_BUDGET = 1_500_000
GIF_RENDER_BUDGET_S = 20.0


class Keyframe(StrEnum):
    OPEN = "open"
    HEADLINE_DONE = "headline_done"
    FACT_DONE = "fact_done"
    FINAL = "final"


@dataclass(frozen=True)
class GifFrame:
    image: Image.Image
    duration_ms: int
    keyframe: Keyframe | None


@dataclass(frozen=True)
class _Strip:
    top: int
    bottom: int
    width: int  # revealed length this strip owns, measured from the content left edge


def _text_strips(
    content: Box, block: TextBlock, bands: tuple[Box, ...], *, fact: bool
) -> tuple[_Strip, ...]:
    lines = block.lines
    tops = [line.ink.top for line in lines] + [line.y for line in lines] + [b.top for b in bands]
    bottoms = [line.ink.bottom for line in lines] + [b.bottom for b in bands]
    bottoms += [line.y + block.line_height for line in lines]
    strips: list[_Strip] = []
    for i, line in enumerate(lines):
        top = min(tops) if i == 0 else line.y
        bottom = max(bottoms) if i == len(lines) - 1 else lines[i + 1].y
        right = bands[i].right if fact else line.ink.right
        strips.append(_Strip(top, bottom, right - content.left))
    return tuple(strips)


def _source_strip(layout: CardLayout, source: TextLine) -> _Strip:
    rule = _px(3, _scale(layout.width, layout.height))
    return _Strip(
        layout.footer_top + rule,
        layout.content_box.bottom,
        source.ink.right - layout.content_box.left,
    )


def _reveal(
    canvas: Image.Image,
    full: Image.Image,
    content: Box,
    strips: tuple[_Strip, ...],
    progress: float,
    *,
    to_edge: bool,
) -> None:
    """Paste the revealed part of each strip from `full`, line after line like a pen."""
    total = sum(strip.width for strip in strips)
    revealed = round(progress * total)
    used = 0
    for strip in strips:
        shown = min(max(revealed - used, 0), strip.width)
        used += strip.width
        if shown <= 0:
            continue
        right = content.right if shown >= strip.width and to_edge else content.left + shown
        box = (content.left, strip.top, right, strip.bottom)
        canvas.paste(full.crop(box), (content.left, strip.top))


def _same(a: Image.Image, b: Image.Image) -> bool:
    return ImageChops.difference(a, b).getbbox() is None


@dataclass
class _Step:
    image: Image.Image
    duration_ms: int
    keyframe: Keyframe | None


def build_story_frames(
    layout: CardLayout,
    beats: Sequence[AnimationBeat],
    *,
    clock: Callable[[], float] = time.monotonic,
    budget_s: float = GIF_RENDER_BUDGET_S,
) -> tuple[GifFrame, ...]:
    started = clock()
    content = layout.content_box
    run_headline = AnimationBeat.HEADLINE_REVEAL in beats
    run_fact = AnimationBeat.FACT_EMPHASIS in beats and layout.body is not None
    run_source = AnimationBeat.SOURCE_FOOTER in beats and layout.source is not None
    if not (run_headline or run_fact or run_source):
        raise MediaContentUnsupported("nothing to animate")

    full = draw_story_card(layout)
    base_layout = dataclasses.replace(
        layout,
        headline=dataclasses.replace(layout.headline, lines=())
        if run_headline
        else layout.headline,
        body=None if run_fact else layout.body,
        body_is_fact=False if run_fact else layout.body_is_fact,
        bands=() if run_fact else layout.bands,
        source=None if run_source else layout.source,
    )
    canvas = draw_story_card(base_layout)

    steps: list[_Step] = [_Step(canvas.copy(), OPEN_MS, Keyframe.OPEN)]

    def check_budget() -> None:
        if clock() - started > budget_s:
            raise MediaRenderError("animation exceeded its render time budget")

    def run(
        count: int,
        strips: tuple[_Strip, ...],
        *,
        eased: bool,
        hold_ms: int,
        done: Keyframe | None,
        to_edge: bool,
    ) -> None:
        for k in range(1, count + 1):
            fraction = k / count
            progress = 1 - (1 - fraction) ** 2 if eased else fraction
            _reveal(canvas, full, content, strips, progress, to_edge=to_edge)
            last = k == count
            steps.append(
                _Step(
                    canvas.copy(),
                    STEP_MS + (hold_ms if last else 0),
                    done if last else None,
                )
            )
            check_budget()

    if run_headline:
        run(
            HEADLINE_STEPS,
            _text_strips(content, layout.headline, (), fact=False),
            eased=False,
            hold_ms=HEADLINE_HOLD_MS,
            done=Keyframe.HEADLINE_DONE,
            to_edge=True,
        )
    if run_fact and layout.body is not None:
        run(
            FACT_STEPS,
            _text_strips(content, layout.body, layout.bands, fact=layout.body_is_fact),
            eased=True,
            hold_ms=FACT_HOLD_MS,
            done=Keyframe.FACT_DONE,
            to_edge=True,
        )
    if run_source and layout.source is not None:
        run(
            SOURCE_STEPS,
            (_source_strip(layout, layout.source),),
            eased=False,
            hold_ms=0,
            done=None,
            to_edge=False,
        )

    assert _same(canvas, full), "reveal strips do not rebuild the static card"
    steps[-1].image = full
    steps[-1].keyframe = Keyframe.FINAL

    merged: list[_Step] = []
    for step in steps:
        if merged and _same(merged[-1].image, step.image):
            merged[-1].duration_ms += step.duration_ms
            merged[-1].keyframe = step.keyframe or merged[-1].keyframe
        else:
            merged.append(step)
    earlier = sum(step.duration_ms for step in merged[:-1])
    merged[-1].duration_ms = GIF_DURATION_MS - earlier
    assert len(merged) <= MAX_GIF_FRAMES, "too many animation frames"
    assert merged[-1].duration_ms > 0
    return tuple(GifFrame(s.image, s.duration_ms, s.keyframe) for s in merged)


def encode_gif(frames: Sequence[GifFrame]) -> bytes:
    """Encode in memory with one shared palette. Plays once, carries no metadata."""
    try:
        palette = frames[-1].image.quantize(
            colors=GIF_MAX_COLOURS,
            method=Image.Quantize.MEDIANCUT,
            dither=Image.Dither.NONE,
        )
        images = [f.image.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
        buf = io.BytesIO()
        images[0].save(
            buf,
            "GIF",
            save_all=True,
            append_images=images[1:],
            duration=[f.duration_ms for f in frames],
            disposal=1,
            optimize=False,
        )
    except (OSError, ValueError) as exc:
        raise MediaRenderError("gif encoding failed") from exc
    data = buf.getvalue()
    if len(data) > GIF_BYTE_BUDGET:
        raise MediaContentUnsupported("gif exceeds byte budget")
    return data
