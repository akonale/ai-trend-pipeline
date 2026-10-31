"""Story card renderer: PNG validity, determinism, goldens, theme tokens and registry."""

import io
import socket
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from ai_trend_pipeline.media import card
from ai_trend_pipeline.media.card import (
    CARD_LANDSCAPE,
    CARD_MIN,
    CARD_SQUARE,
    CARD_THEMES,
    CardTheme,
    StoryCardRenderer,
)
from ai_trend_pipeline.media.illustration import ILLUSTRATION_PROVIDERS
from ai_trend_pipeline.media.models import MAX_STATIC_BYTES, MediaKind, MediaType, artifact_id_for
from ai_trend_pipeline.media.providers import renderer_for
from tests.card_cases import BRIEFS, GOLDENS
from tests.regen_card_goldens import GOLDEN_DIR
from tests.test_design_tokens import contrast, theme

SIZES = [CARD_LANDSCAPE, CARD_SQUARE, CARD_MIN]


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("card_theme", list(CardTheme))
@pytest.mark.parametrize("name", list(BRIEFS))
def test_render_static_returns_valid_png_of_requested_size_within_byte_limit(
    name: str, card_theme: CardTheme, size: tuple[int, int]
) -> None:
    rendered = StoryCardRenderer(card_theme, *size).render_static(BRIEFS[name])
    assert rendered.media_type is MediaType.PNG
    assert (rendered.width, rendered.height) == size
    assert rendered.duration_ms is None
    assert len(rendered.data) <= MAX_STATIC_BYTES
    image = Image.open(io.BytesIO(rendered.data))
    image.load()
    assert image.format == "PNG" and image.mode == "RGB" and image.size == size


def test_render_is_byte_identical_for_same_input() -> None:
    first = StoryCardRenderer().render_static(BRIEFS["long"]).data
    second = StoryCardRenderer().render_static(BRIEFS["long"]).data
    assert first == second


def _differing_fraction(actual: Image.Image, golden: Image.Image) -> float:
    diff = ImageChops.difference(actual, golden)
    red, green, blue = diff.split()
    worst = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    bad = sum(worst.histogram()[9:])
    return bad / (diff.width * diff.height)


@pytest.mark.parametrize("name", list(GOLDENS))
def test_matches_golden_images(name: str) -> None:
    brief_key, card_theme, size = GOLDENS[name]
    data = StoryCardRenderer(card_theme, *size).render_static(BRIEFS[brief_key]).data
    actual = Image.open(io.BytesIO(data)).convert("RGB")
    golden = Image.open(GOLDEN_DIR / f"{name}.png")
    assert golden.mode == "RGB" and golden.size == actual.size == size
    assert _differing_fraction(actual, golden) <= 0.001


def test_version_and_artifact_id_change_with_theme_and_size() -> None:
    brief = BRIEFS["fact"]
    renderers = [
        StoryCardRenderer(),
        StoryCardRenderer(CardTheme.DARK),
        StoryCardRenderer(CardTheme.LIGHT, *CARD_SQUARE),
    ]
    ids = {artifact_id_for(brief, MediaKind.STATIC, r.name, r.version) for r in renderers}
    assert len(ids) == 3
    assert renderers[0].version == "1.1-light-1200x675"
    again = StoryCardRenderer()
    assert artifact_id_for(brief, MediaKind.STATIC, again.name, again.version) in ids


def test_render_animated_returns_gif_with_duration() -> None:
    rendered = StoryCardRenderer().render_animated(BRIEFS["fact"])
    assert rendered.media_type is MediaType.GIF
    assert rendered.duration_ms == 4500
    assert (rendered.width, rendered.height) == CARD_LANDSCAPE
    assert rendered.data.startswith(b"GIF89a")


def test_render_makes_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    assert StoryCardRenderer().render_static(BRIEFS["unicode"]).data.startswith(b"\x89PNG")


@pytest.mark.parametrize("name", ["light", "dark"])
def test_card_theme_colours_match_app_css_tokens(name: str) -> None:
    tokens = theme(name)
    colours = CARD_THEMES[CardTheme(name)]
    assert colours.ground == tokens["bg"]
    assert colours.grid == tokens["grid"]
    assert colours.panel == tokens["surface"]
    assert colours.text == tokens["text"] == tokens["border"] == tokens["shadow"]
    assert colours.muted == tokens["muted"]
    assert colours.accent == tokens["accent"]
    assert colours.marker == tokens["highlight"]
    assert colours.on_marker == tokens["on-fill"]


@pytest.mark.parametrize("card_theme", list(CardTheme))
def test_card_theme_text_pairs_meet_wcag(card_theme: CardTheme) -> None:
    c = CARD_THEMES[card_theme]
    for fg, bg in [
        (c.text, c.panel),
        (c.muted, c.panel),
        (c.accent, c.panel),
        (c.on_marker, c.marker),
    ]:
        assert contrast(fg, bg) >= 4.5, (card_theme, fg, bg)
    assert contrast(c.text, c.ground) >= 3


def test_registry_returns_story_card_renderer() -> None:
    renderer = renderer_for("story-card")
    assert isinstance(renderer, StoryCardRenderer)
    assert renderer.name == "story-card"


def test_illustration_registry_is_empty_and_unused() -> None:
    assert dict(ILLUSTRATION_PROVIDERS) == {}
    source = Path(card.__file__).read_text()
    assert "illustration" not in source
