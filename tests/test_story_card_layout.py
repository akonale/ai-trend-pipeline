"""Pure layout of the story card: fitting, truncation, text policy, bands and invariants."""

import pytest

from ai_trend_pipeline.media.card import (
    CARD_LANDSCAPE,
    CARD_MIN,
    CARD_SQUARE,
    Box,
    CardLayout,
    CardTheme,
    StoryCardRenderer,
    TextBlock,
    layout_story_card,
)
from ai_trend_pipeline.media.providers import MediaContentUnsupported
from tests.card_cases import BRIEFS, FACT_IDS, LONG_HEADLINE, LONG_HIGHLIGHT
from tests.media_fixtures import make_brief

SIZES = [CARD_LANDSCAPE, CARD_SQUARE, CARD_MIN]
THEMES = [CardTheme.LIGHT, CardTheme.DARK]


def _blocks(layout: CardLayout) -> list[TextBlock]:
    return [b for b in (layout.headline, layout.body) if b is not None]


def _union(block: TextBlock) -> Box:
    return Box(
        min(line.ink.left for line in block.lines),
        min(line.ink.top for line in block.lines),
        max(line.ink.right for line in block.lines),
        max(line.ink.bottom for line in block.lines),
    )


def _inside(inner: Box, outer: Box) -> bool:
    return (
        outer.left <= inner.left
        and outer.top <= inner.top
        and inner.right <= outer.right
        and inner.bottom <= outer.bottom
    )


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("name", list(BRIEFS))
def test_ink_and_bands_stay_inside_content_box_and_above_footer(
    name: str, size: tuple[int, int], theme: CardTheme
) -> None:
    layout = layout_story_card(BRIEFS[name], theme, *size)
    box = layout.content_box
    inks = [line.ink for block in _blocks(layout) for line in block.lines]
    inks += [line.ink for line in (layout.source, layout.wordmark) if line is not None]
    for ink in inks:
        assert _inside(ink, box), (ink, box)
    for band in layout.bands:
        assert _inside(band, box), (band, box)
    for block in _blocks(layout):
        assert _union(block).bottom <= layout.footer_top
    if layout.body is not None:
        assert _union(layout.body).top > _union(layout.headline).bottom
    ordered = sorted(layout.bands, key=lambda b: b.top)
    for upper, lower in zip(ordered, ordered[1:], strict=False):
        assert upper.bottom <= lower.top


@pytest.mark.parametrize("size", SIZES)
def test_long_text_shrinks_before_truncating(size: tuple[int, int]) -> None:
    medium = make_brief(
        headline="Central banks hold interest rates steady for a third straight quarter",
        highlight="Inflation cooled faster than forecast, leaving room for cuts later this year.",
        highlight_fact_ids=FACT_IDS,
    )
    layout = layout_story_card(medium, CardTheme.LIGHT, *size)
    assert not layout.truncated
    assert layout.body is not None
    short = layout_story_card(BRIEFS["fact"], CardTheme.LIGHT, *size)
    assert short.headline.font_size >= layout.headline.font_size
    maximum = make_brief(
        headline=LONG_HEADLINE, highlight=LONG_HIGHLIGHT, highlight_fact_ids=FACT_IDS
    )
    tight = layout_story_card(maximum, CardTheme.LIGHT, *CARD_MIN)
    if tight.truncated:
        cut = [b.lines[-1].text for b in _blocks(tight) if b.lines[-1].text.endswith("…")]
        assert cut


def test_truncation_ends_with_ellipsis() -> None:
    brief = make_brief(
        headline=("WWWWWWWWW " * 15).strip(),
        highlight=("MMMMMMMMM " * 28).strip(),
        highlight_fact_ids=FACT_IDS,
    )
    layout = layout_story_card(brief, CardTheme.LIGHT, *CARD_MIN)
    assert layout.truncated
    assert layout.headline.lines[-1].text.endswith("…")
    assert len(layout.headline.lines) == 4


@pytest.mark.parametrize("size", SIZES)
def test_unbreakable_long_word_is_split_without_overflow(size: tuple[int, int]) -> None:
    brief = make_brief(headline="W" * 60, highlight="m" * 100, highlight_fact_ids=FACT_IDS)
    layout = layout_story_card(brief, CardTheme.LIGHT, *size)
    assert len(layout.headline.lines) > 1
    assert all(line.ink.right <= layout.content_box.right for line in layout.headline.lines)
    assert all(_inside(band, layout.content_box) for band in layout.bands)


@pytest.mark.parametrize("size", SIZES)
def test_fact_gets_marker_bands_and_summary_does_not(size: tuple[int, int]) -> None:
    fact = layout_story_card(BRIEFS["long"], CardTheme.LIGHT, *size)
    assert fact.body is not None and fact.body_is_fact
    assert len(fact.bands) == len(fact.body.lines) > 0
    summary = layout_story_card(BRIEFS["summary"], CardTheme.LIGHT, *size)
    assert summary.body is not None and not summary.body_is_fact
    assert summary.bands == ()


@pytest.mark.parametrize("size", SIZES)
def test_missing_highlight_renders_headline_and_footer_only(size: tuple[int, int]) -> None:
    layout = layout_story_card(BRIEFS["no_highlight"], CardTheme.LIGHT, *size)
    assert layout.body is None and layout.bands == ()
    assert layout.headline.lines
    assert layout.source is not None and layout.wordmark.text == "Topic Studio"


@pytest.mark.parametrize("headline", ["中文标题", "Привет мир", "Mixed Latin and 日本"])
def test_unsupported_headline_letters_raise(headline: str) -> None:
    with pytest.raises(MediaContentUnsupported):
        layout_story_card(make_brief(headline=headline), CardTheme.LIGHT, *CARD_LANDSCAPE)


def test_headline_of_only_emoji_raises() -> None:
    with pytest.raises(MediaContentUnsupported):
        layout_story_card(make_brief(headline="😀 🎉"), CardTheme.LIGHT, *CARD_LANDSCAPE)


def test_emoji_dropped_and_unsupported_fact_or_source_omitted() -> None:
    brief = make_brief(headline="Smile 😀 now", highlight="Numbers 中文 here", source_label="来源")
    layout = layout_story_card(brief, CardTheme.LIGHT, *CARD_LANDSCAPE)
    assert " ".join(line.text for line in layout.headline.lines) == "Smile now"
    assert layout.body is None and layout.bands == ()
    assert layout.source is None
    emoji_fact = make_brief(highlight="Rising 📈 fast", highlight_fact_ids=FACT_IDS)
    kept = layout_story_card(emoji_fact, CardTheme.LIGHT, *CARD_LANDSCAPE)
    assert kept.body is not None
    assert " ".join(line.text for line in kept.body.lines) == "Rising fast"


def test_markup_like_text_is_kept_literally() -> None:
    brief = make_brief(headline="**bold** &amp; more", highlight="Use *stars* and &lt;ok&gt;")
    layout = layout_story_card(brief, CardTheme.LIGHT, *CARD_LANDSCAPE)
    assert " ".join(line.text for line in layout.headline.lines) == "**bold** &amp; more"
    assert layout.body is not None
    assert " ".join(line.text for line in layout.body.lines) == "Use *stars* and &lt;ok&gt;"


def test_constructor_rejects_sizes_below_minimum_or_above_max() -> None:
    for width, height in [(639, 675), (1200, 359), (2049, 675), (1200, 2049)]:
        with pytest.raises(ValueError):
            StoryCardRenderer(width=width, height=height)
    StoryCardRenderer(width=640, height=360)
    StoryCardRenderer(width=2048, height=2048)
