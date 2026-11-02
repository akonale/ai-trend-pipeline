"""The pure SVG-to-PNG pipeline: valid fixtures render, hostile input is rejected by reason."""

from pathlib import Path

import pytest

from ai_trend_pipeline.media.drawn.pipeline import (
    DRAWN_SIZE,
    DrawingRejected,
    DrawingRejectReason,
    render_drawn_svg,
)
from ai_trend_pipeline.media.models import MAX_STATIC_BYTES

FIXTURES = Path(__file__).parent / "fixtures" / "drawn"
HEAD = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 600">'
RECT = '<rect x="10" y="10" width="300" height="200" fill="#15172b"/>'


def wrap(inner: str) -> str:
    return f"{HEAD}{RECT}{inner}</svg>"


@pytest.mark.parametrize("name", ["hero_ok", "concept_ok", "detail_ok"])
def test_valid_fixtures_render_deterministically(name: str) -> None:
    svg = (FIXTURES / f"{name}.svg").read_text()
    assert len(svg.encode()) <= 4096
    first = render_drawn_svg(svg)
    second = render_drawn_svg(svg)
    assert first.png == second.png
    assert first.png.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(first.png) <= MAX_STATIC_BYTES
    assert (first.width, first.height) == DRAWN_SIZE == (1200, 900)


HOSTILE = {
    "script": wrap("<script>alert(1)</script>"),
    "foreignObject": wrap("<foreignObject/>"),
    "image": wrap('<image href="http://x.invalid/a.png"/>'),
    "use": wrap('<use href="#a"/>'),
    "text": wrap("<text>hi</text>"),
    "style": wrap('<rect width="5" height="5" style="fill:red"/>'),
    "onload": HEAD.replace("viewBox", 'onload="x()" viewBox') + RECT + "</svg>",
    "url_paint": wrap('<rect width="5" height="5" fill="url(#g)"/>'),
    "bad_colour": wrap('<rect width="5" height="5" fill="#ff0000"/>'),
    "doctype": "<!DOCTYPE svg>" + wrap(""),
    "entity": '<!DOCTYPE svg [<!ENTITY a "b">]>' + wrap("&a;"),
}


@pytest.mark.parametrize("case", sorted(HOSTILE))
def test_hostile_svg_is_unsafe(case: str) -> None:
    with pytest.raises(DrawingRejected) as info:
        render_drawn_svg(HOSTILE[case])
    assert info.value.reason is DrawingRejectReason.UNSAFE
    assert str(info.value) == "unsafe"


def test_too_large() -> None:
    svg = wrap("<!--" + "x" * 4097 + "-->")
    with pytest.raises(DrawingRejected) as info:
        render_drawn_svg(svg)
    assert info.value.reason is DrawingRejectReason.TOO_LARGE


def test_wrong_viewbox() -> None:
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">{RECT}</svg>'
    with pytest.raises(DrawingRejected) as info:
        render_drawn_svg(svg)
    assert info.value.reason is DrawingRejectReason.WRONG_VIEWBOX


def test_viewbox_with_commas_is_accepted() -> None:
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0, 0,800  600">{RECT}</svg>'
    assert render_drawn_svg(svg).width == 1200


def test_too_many_elements() -> None:
    svg = f"{HEAD}{'<rect/>' * 301}</svg>"
    assert len(svg.encode()) <= 4096
    with pytest.raises(DrawingRejected) as info:
        render_drawn_svg(svg)
    assert info.value.reason is DrawingRejectReason.TOO_MANY_ELEMENTS


def test_blank_paper_only() -> None:
    svg = f'{HEAD}<rect x="0" y="0" width="800" height="600" fill="#ffffff"/></svg>'
    with pytest.raises(DrawingRejected) as info:
        render_drawn_svg(svg)
    assert info.value.reason is DrawingRejectReason.BLANK


def test_error_text_never_carries_input() -> None:
    sentinel = "SENTINELXYZ"
    svg = f'{HEAD}{RECT}<rect {sentinel}="1" width="2" height="2"/></svg>'
    with pytest.raises(DrawingRejected) as info:
        render_drawn_svg(svg)
    exc = info.value
    assert sentinel not in str(exc)
    assert exc.__suppress_context__ is True
    assert exc.__cause__ is None
