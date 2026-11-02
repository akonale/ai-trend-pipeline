"""Project-original glyphs follow the glyph vocabulary: tokens only, one stroke, small and flat."""

import xml.etree.ElementTree as ET

import pytest

from ai_trend_pipeline.media.illustrations.catalogue import (
    ASSET_DIR,
    IllustrationAsset,
    load_catalogue,
    read_asset_svg,
)
from ai_trend_pipeline.media.illustrations.matching import TOPIC_ORDER

GLYPH_STROKE_WIDTH = "5"
AUTHORING_COLOURS = {
    "#15172b": "ink",
    "#ffffff": "paper",
    "#2448d8": "accent",
    "#ffc93c": "support",
}
INK = "#15172b"
# Topics that already have an Open Doodles drawing never get a glyph.
DOODLE_TOPICS = {"education", "health"}
UNCOVERED = [topic for topic in TOPIC_ORDER if topic not in DOODLE_TOPICS]
# Glyphs dropped for quality after the draw, render and look loop (none so far).
DROPPED_GLYPHS: frozenset[str] = frozenset()
MAX_ELEMENTS = 40
MAX_BYTES = 8 * 1024


def glyphs() -> list[IllustrationAsset]:
    return [asset for asset in load_catalogue() if asset.source.value == "project"]


def test_every_uncovered_topic_has_a_glyph_or_is_listed_as_dropped() -> None:
    assert len(UNCOVERED) == 12
    covered = {asset.topic for asset in glyphs()}
    for topic in UNCOVERED:
        assert (topic in covered) != (topic in DROPPED_GLYPHS), topic
    assert not covered & DOODLE_TOPICS
    assert DROPPED_GLYPHS <= set(UNCOVERED)


@pytest.mark.parametrize("asset", glyphs(), ids=lambda a: a.id)
def test_glyph_colours_and_recolour_map(asset: IllustrationAsset) -> None:
    svg = read_asset_svg(asset)
    used = {
        value.lower()
        for element in ET.fromstring(svg).iter()
        for key, value in element.attrib.items()
        if key in {"fill", "stroke"} and value != "none"
    }
    assert used <= set(AUTHORING_COLOURS)
    assert dict(asset.recolour) == {k: v for k, v in AUTHORING_COLOURS.items()}
    assert asset.id.startswith("glyph-")


def _walk(element: ET.Element, inherited: dict[str, str], found: list[dict[str, str]]) -> None:
    here = {**inherited, **element.attrib}
    if element.tag.rpartition("}")[2] in {
        "path",
        "circle",
        "ellipse",
        "rect",
        "line",
        "polyline",
        "polygon",
    }:
        found.append(here)
    for child in element:
        _walk(child, here, found)


@pytest.mark.parametrize("asset", glyphs(), ids=lambda a: a.id)
def test_every_stroked_element_uses_ink_and_the_pinned_stroke(asset: IllustrationAsset) -> None:
    found: list[dict[str, str]] = []
    _walk(ET.fromstring(read_asset_svg(asset)), {}, found)
    stroked = [attrs for attrs in found if attrs.get("stroke", "none") != "none"]
    assert stroked
    for attrs in stroked:
        assert attrs["stroke"] == INK
        assert attrs["stroke-width"] == GLYPH_STROKE_WIDTH
        assert attrs["stroke-linejoin"] == "round"
        assert attrs["stroke-linecap"] == "round"
    assert len(stroked) == len(found)  # nothing is left unoutlined


@pytest.mark.parametrize("asset", glyphs(), ids=lambda a: a.id)
def test_two_or_three_non_ink_fills_and_simple_files(asset: IllustrationAsset) -> None:
    svg = read_asset_svg(asset)
    found: list[dict[str, str]] = []
    _walk(ET.fromstring(svg), {}, found)
    fills = {attrs.get("fill", "none") for attrs in found} - {"none", INK}
    assert 2 <= len(fills) <= 3, fills
    assert len(found) <= MAX_ELEMENTS
    assert len(svg.encode("utf-8")) <= MAX_BYTES
    assert "<text" not in svg and "<tspan" not in svg


@pytest.mark.parametrize("asset", glyphs(), ids=lambda a: a.id)
def test_every_glyph_has_the_hard_offset_shadow(asset: IllustrationAsset) -> None:
    root = ET.fromstring(read_asset_svg(asset))
    shadows = [
        g
        for g in root.iter("{http://www.w3.org/2000/svg}g")
        if g.attrib.get("transform") == "translate(6 6)" and g.attrib.get("fill") == INK
    ]
    assert len(shadows) == 1 and len(shadows[0]) >= 1


def test_glyph_files_have_no_stray_files() -> None:
    ids = {asset.file for asset in load_catalogue()}
    assert {path.name for path in ASSET_DIR.glob("*.svg")} == ids
