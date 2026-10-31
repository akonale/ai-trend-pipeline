"""Vendored fonts: location, licence and glyph coverage parsing."""

from pathlib import Path

import pytest

from ai_trend_pipeline.media.fonts import (
    BODY_FONT_PATH,
    FONT_DIR,
    HEADLINE_FONT_PATH,
    LICENCE_PATH,
    supported_codepoints,
)

FONTS = [HEADLINE_FONT_PATH, BODY_FONT_PATH]


@pytest.mark.parametrize("path", FONTS)
def test_supported_codepoints_covers_latin_and_punctuation(path: Path) -> None:
    codes = supported_codepoints(path)
    for char in "Aéłạ—“…€":
        assert ord(char) in codes, char


@pytest.mark.parametrize("path", FONTS)
def test_supported_codepoints_excludes_cjk_cyrillic_and_emoji(path: Path) -> None:
    codes = supported_codepoints(path)
    assert ord("中") not in codes
    assert ord("Ж") not in codes
    assert ord("😀") not in codes


def test_fonts_load_from_package_directory() -> None:
    assert FONT_DIR.parts[-3:] == ("ai_trend_pipeline", "media", "fonts")
    for path in (*FONTS, LICENCE_PATH):
        assert path.parent == FONT_DIR
        assert path.is_file()
    assert "SIL OPEN FONT LICENSE" in LICENCE_PATH.read_text().upper()
