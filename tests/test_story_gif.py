"""Story GIF: bounds, keyframe order, determinism, goldens and failure mapping."""

import io
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageSequence

from ai_trend_pipeline.media import animation
from ai_trend_pipeline.media.animation import (
    GIF_BYTE_BUDGET,
    GIF_DURATION_MS,
    MAX_GIF_FRAMES,
    GifFrame,
    Keyframe,
    build_story_frames,
)
from ai_trend_pipeline.media.card import (
    CARD_LANDSCAPE,
    CARD_MIN,
    CARD_SQUARE,
    CARD_THEMES,
    Box,
    CardLayout,
    CardTheme,
    StoryCardRenderer,
    _rgb,  # pyright: ignore[reportPrivateUsage]
    layout_story_card,
)
from ai_trend_pipeline.media.models import AnimationBeat, MediaType
from ai_trend_pipeline.media.providers import (
    MediaContentUnsupported,
    MediaRenderError,
    renderer_for,
)
from tests.card_cases import BRIEFS, FACT_IDS, GIF_GOLDENS
from tests.media_fixtures import make_brief
from tests.regen_card_goldens import GIF_GOLDEN_DIR

SIZES = [CARD_LANDSCAPE, CARD_SQUARE, CARD_MIN]
Rgb = tuple[int, int, int]


def _frames(
    key: str, theme: CardTheme, size: tuple[int, int] = CARD_LANDSCAPE
) -> Iterator[Image.Image]:
    data = StoryCardRenderer(theme, *size).render_animated(BRIEFS[key]).data
    yield from (f.convert("RGB") for f in ImageSequence.Iterator(Image.open(io.BytesIO(data))))


def _build(
    key: str, theme: CardTheme, size: tuple[int, int] = CARD_LANDSCAPE
) -> tuple[CardLayout, tuple[GifFrame, ...]]:
    brief = BRIEFS[key]
    layout = layout_story_card(brief, theme, *size)
    return layout, build_story_frames(layout, brief.animation_beats)


def _by_key(frames: tuple[GifFrame, ...], key: Keyframe) -> Image.Image:
    return next(f.image for f in frames if f.keyframe is key)


def _is_plain(image: Image.Image, box: Box, colour: Rgb) -> bool:
    crop = image.crop((box.left, box.top, box.right, box.bottom))
    return crop.getcolors(10**6) == [(crop.width * crop.height, colour)]


def _same(a: Image.Image, b: Image.Image, box: Box) -> bool:
    area = (box.left, box.top, box.right, box.bottom)
    return ImageChops.difference(a.crop(area), b.crop(area)).getbbox() is None


def _regions(layout: CardLayout) -> tuple[Box, Box, Box]:
    content = layout.content_box
    head = layout.headline.lines
    headline = Box(
        content.left,
        min(line.ink.top for line in head),
        content.right,
        max(line.ink.bottom for line in head),
    )
    assert layout.body is not None and layout.source is not None
    body = Box(
        content.left,
        min([b.top for b in layout.bands] + [line.ink.top for line in layout.body.lines]),
        content.right,
        max([b.bottom for b in layout.bands] + [line.ink.bottom for line in layout.body.lines]),
    )
    source = layout.source.ink
    return headline, body, Box(content.left, source.top, source.right, source.bottom)


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("theme", list(CardTheme))
@pytest.mark.parametrize("name", list(BRIEFS))
def test_gif_is_valid_bounded_and_plays_once(
    name: str, theme: CardTheme, size: tuple[int, int]
) -> None:
    rendered = StoryCardRenderer(theme, *size).render_animated(BRIEFS[name])
    assert renderer_for("story-card").version == StoryCardRenderer().version
    assert rendered.media_type is MediaType.GIF and rendered.duration_ms == GIF_DURATION_MS
    assert rendered.data.startswith(b"GIF89a")
    assert len(rendered.data) <= GIF_BYTE_BUDGET
    image = Image.open(io.BytesIO(rendered.data))
    assert image.size == size and "loop" not in image.info
    count, total = 0, 0
    for frame in ImageSequence.Iterator(image):
        count += 1
        total += int(frame.info["duration"])
        colours = frame.convert("RGB").getcolors(10**6)
        assert colours is not None and len(colours) <= animation.GIF_MAX_COLOURS
    assert count <= MAX_GIF_FRAMES and total == GIF_DURATION_MS


@pytest.mark.parametrize("theme", list(CardTheme))
def test_keyframes_follow_headline_fact_footer_order(theme: CardTheme) -> None:
    layout, frames = _build("fact", theme)
    panel = _rgb(CARD_THEMES[theme].panel)
    headline, body, source = _regions(layout)
    opened = _by_key(frames, Keyframe.OPEN)
    head_done = _by_key(frames, Keyframe.HEADLINE_DONE)
    fact_done = _by_key(frames, Keyframe.FACT_DONE)
    final = _by_key(frames, Keyframe.FINAL)
    for box in (headline, body, source):
        assert _is_plain(opened, box, panel)
    assert _same(head_done, final, headline)
    assert _is_plain(head_done, body, panel) and _is_plain(head_done, source, panel)
    assert _same(fact_done, final, headline) and _same(fact_done, final, body)
    assert not _is_plain(fact_done, body, panel)
    assert _is_plain(fact_done, source, panel)
    assert frames[-1].keyframe is Keyframe.FINAL and frames[0].keyframe is Keyframe.OPEN


def test_fact_text_only_appears_with_its_band() -> None:
    layout, frames = _build("fact", CardTheme.DARK)
    ink = _rgb(CARD_THEMES[CardTheme.DARK].on_marker)
    marker = _rgb(CARD_THEMES[CardTheme.DARK].marker)
    content = layout.content_box
    _, body, _ = _regions(layout)
    stroke = [f for f in frames if f.keyframe is not Keyframe.OPEN]
    assert len(stroke) > 10
    for frame in frames:
        crop = frame.image.crop((content.left, body.top, content.right, body.bottom))
        pixels = crop.load()
        assert pixels is not None
        for y in range(crop.height):
            if pixels[0, y] == marker:
                continue
            for x in range(crop.width):
                assert pixels[x, y] != ink, f"ink outside a band at {(x, y)}"


def _fraction_off(a: Image.Image, b: Image.Image, limit: int) -> float:
    diff = ImageChops.difference(a, b)
    red, green, blue = diff.split()
    worst = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    return sum(worst.histogram()[limit + 1 :]) / (diff.width * diff.height)


@pytest.mark.parametrize("name", ["fact", "long", "unicode"])
def test_final_frame_matches_static_card(name: str) -> None:
    renderer = StoryCardRenderer()
    last = list(_frames(name, CardTheme.LIGHT))[-1]
    static = Image.open(io.BytesIO(renderer.render_static(BRIEFS[name]).data)).convert("RGB")
    assert last.size == static.size
    assert _fraction_off(last, static, 16) <= 0.01


def test_missing_beats_or_elements_are_drawn_from_the_start() -> None:
    layout, frames = _build("no_highlight", CardTheme.LIGHT)
    assert all(f.keyframe is not Keyframe.FACT_DONE for f in frames)
    assert sum(f.duration_ms for f in frames) == GIF_DURATION_MS
    brief = make_brief(
        highlight_fact_ids=FACT_IDS,
        animation_beats=[AnimationBeat.HEADLINE_REVEAL, AnimationBeat.FACT_EMPHASIS],
    )
    layout = layout_story_card(brief, CardTheme.LIGHT, *CARD_LANDSCAPE)
    frames = build_story_frames(layout, brief.animation_beats)
    _, _, source = _regions(layout)
    assert _same(_by_key(frames, Keyframe.OPEN), _by_key(frames, Keyframe.FINAL), source)
    assert sum(f.duration_ms for f in frames) == GIF_DURATION_MS


def test_nothing_to_animate_raises_content_unsupported() -> None:
    brief = make_brief(
        highlight=None,
        animation_beats=[AnimationBeat.SOURCE_FOOTER],
        source_label=None,
    )
    layout = layout_story_card(brief, CardTheme.LIGHT, *CARD_LANDSCAPE)
    with pytest.raises(MediaContentUnsupported, match="nothing to animate"):
        build_story_frames(layout, brief.animation_beats)


@pytest.mark.parametrize("name", list(BRIEFS))
def test_identical_frames_are_merged(name: str) -> None:
    _, frames = _build(name, CardTheme.LIGHT)
    for before, after in zip(frames, frames[1:], strict=False):
        assert ImageChops.difference(before.image, after.image).getbbox() is not None
    assert len(list(_frames(name, CardTheme.LIGHT))) == len(frames)
    assert all(f.duration_ms % 10 == 0 for f in frames)


def test_render_is_byte_identical_for_same_input() -> None:
    first = StoryCardRenderer().render_animated(BRIEFS["long"]).data
    second = StoryCardRenderer().render_animated(BRIEFS["long"]).data
    assert first == second


@pytest.mark.parametrize("name", list(GIF_GOLDENS))
def test_keyframes_match_golden_images(name: str) -> None:
    key, theme, size, keyframe = GIF_GOLDENS[name]
    _, frames = _build(key, theme, size)
    actual = _by_key(frames, keyframe)
    golden = Image.open(GIF_GOLDEN_DIR / f"{name}.png")
    assert golden.mode == actual.mode == "RGB" and golden.size == actual.size == size
    assert _fraction_off(actual, golden.convert("RGB"), 8) <= 0.001


def test_oversized_side_raises_content_unsupported() -> None:
    with pytest.raises(MediaContentUnsupported):
        StoryCardRenderer(CardTheme.LIGHT, 1201, 675).render_animated(BRIEFS["fact"])


def test_byte_budget_exceeded_raises_content_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(animation, "GIF_BYTE_BUDGET", 1000)
    with pytest.raises(MediaContentUnsupported, match="byte budget"):
        StoryCardRenderer().render_animated(BRIEFS["fact"])


def test_render_time_budget_raises_render_error() -> None:
    layout = layout_story_card(BRIEFS["fact"], CardTheme.LIGHT, *CARD_LANDSCAPE)
    ticks = iter(range(0, 10_000, 30))
    with pytest.raises(MediaRenderError, match="time budget"):
        build_story_frames(layout, BRIEFS["fact"].animation_beats, clock=lambda: float(next(ticks)))


def test_encoder_exception_becomes_render_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("disk on fire")

    monkeypatch.setattr(Image.Image, "save", boom)
    with pytest.raises(MediaRenderError, match="gif encoding failed"):
        StoryCardRenderer().render_animated(BRIEFS["fact"])


def test_no_temp_files_after_success_or_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    StoryCardRenderer().render_animated(BRIEFS["fact"])
    assert list(tmp_path.iterdir()) == []

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("x")

    monkeypatch.setattr(Image.Image, "save", boom)
    with pytest.raises(MediaRenderError):
        StoryCardRenderer().render_animated(BRIEFS["fact"])
    assert list(tmp_path.iterdir()) == []


def test_unsupported_headline_raises_for_animation() -> None:
    with pytest.raises(MediaContentUnsupported):
        StoryCardRenderer().render_animated(make_brief(headline="中文标题"))
