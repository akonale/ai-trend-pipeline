"""Tests for the pure style model and helpers."""

import re

import pytest
from pydantic import JsonValue, ValidationError

from ai_trend_pipeline.style import (
    FONTS,
    PALETTES,
    TONES,
    ChartStyle,
    apply_style_to_spec,
    caption_suffix,
    parse_hashtags,
    style_prompt,
)


def test_defaults_validate() -> None:
    style = ChartStyle()
    assert (style.palette, style.font, style.tone) == ("classic", "sans", "neutral")
    assert style.hashtags == []
    assert style.signature == ""


def test_catalogue_entries_are_valid() -> None:
    for colors in PALETTES.values():
        assert 6 <= len(colors) <= 10
        assert all(re.fullmatch(r"#[0-9a-f]{6}", c) for c in colors)
    for key in PALETTES:
        ChartStyle(palette=key)
    for key in FONTS:
        ChartStyle(font=key)
    for key, text in TONES.items():
        ChartStyle(tone=key)
        assert "factual" in text
    assert FONTS["sans"] == "Liberation Sans, Arial, Helvetica, sans-serif"


def test_every_font_stack_has_the_bundled_font() -> None:
    # vl-convert only bundles Liberation Sans; without it a stack renders no text at all.
    for stack in FONTS.values():
        assert "Liberation Sans" in stack.split(", ")


@pytest.mark.parametrize(
    "bad",
    [
        {"palette": "nope"},
        {"font": "comic"},
        {"tone": "angry"},
        {"hashtags": ["a", "b", "c", "d"]},
        {"hashtags": ["a-b"]},
        {"hashtags": ["x" * 25]},
        {"signature": "x" * 41},
        {"signature": "a\nb"},
        {"signature": "a\x00b"},
        {"signature": "a\u202eb"},
    ],
)
def test_invalid_styles_raise(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ChartStyle.model_validate(bad)


def test_signature_is_stripped_and_limit_inclusive() -> None:
    assert ChartStyle(signature="  hi  ").signature == "hi"
    assert ChartStyle(signature="x" * 40).signature == "x" * 40
    assert ChartStyle(hashtags=["x" * 24]).hashtags == ["x" * 24]


def test_parse_hashtags() -> None:
    assert parse_hashtags("#ai, data  #ai") == ["ai", "data"]
    assert parse_hashtags("") == []
    assert parse_hashtags(" , # ,a,,b ") == ["a", "b"]


def test_caption_suffix() -> None:
    assert caption_suffix(ChartStyle()) == ""
    assert caption_suffix(ChartStyle(hashtags=["a", "b"])) == "#a #b"
    assert caption_suffix(ChartStyle(signature="By me")) == "By me"
    assert caption_suffix(ChartStyle(hashtags=["a"], signature="By me")) == "#a\n\nBy me"


def test_style_prompt() -> None:
    text = style_prompt(ChartStyle(tone="punchy"), 200)
    assert text.startswith("Style:")
    assert TONES["punchy"] in text
    assert "at most 200 characters" in text
    assert "Do not set colours, fonts or a config block" in text


def test_apply_style_to_spec() -> None:
    spec: dict[str, JsonValue] = {
        "mark": "line",
        "config": {"axis": {"grid": False}, "range": {"ramp": ["#000"]}, "mark": {"opacity": 1}},
    }
    out = apply_style_to_spec(spec, ChartStyle(palette="ocean", font="mono"))
    config = out["config"]
    assert isinstance(config, dict)
    assert config["axis"] == {"grid": False}
    assert config["range"] == {"ramp": ["#000"], "category": list(PALETTES["ocean"])}
    assert config["mark"] == {"opacity": 1, "color": PALETTES["ocean"][0]}
    assert config["font"] == FONTS["mono"]
    assert spec["config"] == {
        "axis": {"grid": False},
        "range": {"ramp": ["#000"]},
        "mark": {"opacity": 1},
    }
    assert out["mark"] == "line"
