"""Shared card briefs and golden cases (used by tests and the golden regeneration script)."""

from ai_trend_pipeline.media.animation import Keyframe
from ai_trend_pipeline.media.card import CARD_LANDSCAPE, CARD_MIN, CARD_SQUARE, CardTheme
from ai_trend_pipeline.media.models import MediaBrief
from tests.media_fixtures import make_brief

LONG_HEADLINE = (
    "Central banks across every major economy hold interest rates steady for a third straight "
    "quarter as inflation cools faster than economists had forecast earlier this year ok"
)[:160]
LONG_HIGHLIGHT = (
    "World life expectancy was 72.5 years in 2020 and kept rising through the decade, with "
    "large differences between regions and income groups, according to the latest report. "
    "The gap between the richest and poorest regions narrowed slightly over the period. "
    "Researchers caution that the figures are estimates and may be revised."
)[:280]

FACT_IDS = ["fact-v1-0123456789abcdef"]
FACT_BEATS = ["headline_reveal", "fact_emphasis", "source_footer"]

BRIEFS: dict[str, MediaBrief] = {
    "fact": make_brief(highlight_fact_ids=FACT_IDS),
    "long": make_brief(
        headline=LONG_HEADLINE, highlight=LONG_HIGHLIGHT, highlight_fact_ids=FACT_IDS
    ),
    "summary": make_brief(),
    "no_highlight": make_brief(
        highlight=None, animation_beats=["headline_reveal", "source_footer"]
    ),
    "unicode": make_brief(
        headline="Café «Việt Nam» — “quoted” text 😀 …",
        highlight="Naïve résumé: Łódź, Tiếng Việt, “curly” quotes — and an em dash… 🎉",
        highlight_fact_ids=FACT_IDS,
    ),
}

# name -> (brief key, theme, size)
GOLDENS: dict[str, tuple[str, CardTheme, tuple[int, int]]] = {
    "light_fact_1200x675": ("fact", CardTheme.LIGHT, CARD_LANDSCAPE),
    "light_long_1200x675": ("long", CardTheme.LIGHT, CARD_LANDSCAPE),
    "light_summary_1200x675": ("summary", CardTheme.LIGHT, CARD_LANDSCAPE),
    "light_unicode_1200x675": ("unicode", CardTheme.LIGHT, CARD_LANDSCAPE),
    "dark_fact_1200x675": ("fact", CardTheme.DARK, CARD_LANDSCAPE),
    "light_long_640x360": ("long", CardTheme.LIGHT, CARD_MIN),
    "light_long_1080x1080": ("long", CardTheme.LIGHT, CARD_SQUARE),
}

# Animation keyframe goldens: name -> (brief key, theme, size, keyframe)
GIF_GOLDENS: dict[str, tuple[str, CardTheme, tuple[int, int], Keyframe]] = {
    "light_fact_1200x675_open": ("fact", CardTheme.LIGHT, CARD_LANDSCAPE, Keyframe.OPEN),
    "light_fact_1200x675_headline_done": (
        "fact",
        CardTheme.LIGHT,
        CARD_LANDSCAPE,
        Keyframe.HEADLINE_DONE,
    ),
    "light_fact_1200x675_fact_done": ("fact", CardTheme.LIGHT, CARD_LANDSCAPE, Keyframe.FACT_DONE),
    "light_fact_1200x675_final": ("fact", CardTheme.LIGHT, CARD_LANDSCAPE, Keyframe.FINAL),
    "dark_fact_1200x675_fact_done": ("fact", CardTheme.DARK, CARD_LANDSCAPE, Keyframe.FACT_DONE),
}
