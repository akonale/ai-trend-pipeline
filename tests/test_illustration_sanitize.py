"""The SVG allowlist: vendored assets pass, every crafted attack is rejected."""

import pytest

from ai_trend_pipeline.media.illustrations.catalogue import load_catalogue, read_asset_svg
from ai_trend_pipeline.media.illustrations.sanitize import UnsafeSvg, sanitize_svg

COLOURS = {"#000000", "#ff5678"}
NS = 'xmlns="http://www.w3.org/2000/svg"'


def svg(body: str, root_attrs: str = 'viewBox="0 0 10 10"') -> str:
    return f"<svg {NS} {root_attrs}>{body}</svg>"


GOOD = svg('<g fill="none"><path d="M0,0 L5,5" fill="#000000" stroke="#ff5678"/></g>')


def test_all_vendored_assets_pass() -> None:
    for asset in load_catalogue():
        assert sanitize_svg(read_asset_svg(asset), asset.recolour.keys()) is not None


def test_a_plain_svg_passes() -> None:
    sanitize_svg(GOOD, COLOURS)
    sanitize_svg(
        svg(
            '<defs><clipPath id="c1"><rect x="0" y="0" width="5" height="5"/></clipPath></defs>'
            '<circle cx="1" cy="1" r="2" fill="#000000" clip-path="url(#c1)" opacity="0.5"/>'
            '<polygon points="0,0 1,1 2,0" fill="#ff5678"/>'
        ),
        COLOURS,
    )


@pytest.mark.parametrize(
    "bad",
    [
        svg("<script>alert(1)</script>"),
        svg('<path d="M0,0" onload="x()"/>'),
        svg('<path d="M0,0" onclick="x()"/>'),
        svg("<foreignObject><div/></foreignObject>"),
        svg('<image href="http://example.com/a.png"/>'),
        svg('<image xlink:href="data:image/png;base64,AA"/>'.replace("xlink:", "")),
        svg("<text>hi</text>"),
        svg("<tspan>hi</tspan>"),
        svg("<style>path{fill:red}</style>"),
        svg('<path d="M0,0" style="fill:red"/>'),
        svg('<a href="http://x"><path d="M0,0"/></a>'),
        svg('<use href="#a"/>'),
        svg("<filter/>"),
        svg("<mask/>"),
        svg("<pattern/>"),
        svg('<linearGradient id="g"/>'),
        svg('<path d="M0,0" fill="url(http://example.com/x.svg#a)"/>'),
        svg('<path d="M0,0" fill="url(#g)"/>'),
        svg('<path d="M0,0" clip-path="url(http://example.com/x#a)"/>'),
        svg('<path d="M0,0" clip-path="url(#a) url(#b)"/>'),
        svg('<path d="M0,0" fill="#123456"/>'),
        svg('<path d="M0,0" stroke="red"/>'),
        svg('<path d="M0,0" fill="currentColor"/>'),
        svg('<path d="M0,0" href="#x"/>'),
        svg('<path d="M0,0" data-x="1"/>'),
        svg('<path d="javascript:alert(1)"/>'),
        svg('<path d="M0,0" transform="url(#x)"/>'),
        svg('<g xmlns:xlink="http://www.w3.org/1999/xlink"><path xlink:href="#a" d="M0,0"/></g>'),
        svg('<ns:path xmlns:ns="http://example.com/"/>'),
        svg('<path d="M0,0"/>', root_attrs=""),
        "<!DOCTYPE svg><svg " + NS + ' viewBox="0 0 1 1"/>',
        '<!DOCTYPE svg [<!ENTITY a "b">]><svg ' + NS + ' viewBox="0 0 1 1">&a;</svg>',
        '<!ENTITY a "b"><svg ' + NS + ' viewBox="0 0 1 1"/>',
        '<?xml-stylesheet href="a.css"?><svg ' + NS + ' viewBox="0 0 1 1"/>',
        "<svg",
        "",
        "<html></html>",
        svg("<path d='M0,0'/>") + "<svg/>",
    ],
)
def test_crafted_svgs_are_rejected(bad: str) -> None:
    with pytest.raises(UnsafeSvg):
        sanitize_svg(bad, COLOURS)


def test_file_over_64_kb_is_rejected() -> None:
    big = svg('<path d="M0,0 ' + "L1,1 " * 20_000 + '" fill="#000000"/>')
    assert len(big.encode()) > 64 * 1024
    with pytest.raises(UnsafeSvg):
        sanitize_svg(big, COLOURS)


def test_a_fill_colour_must_be_a_key_of_the_recolour_map() -> None:
    sanitize_svg(GOOD, COLOURS)
    with pytest.raises(UnsafeSvg):
        sanitize_svg(GOOD, {"#000000"})


def test_none_is_always_an_allowed_paint() -> None:
    sanitize_svg(svg('<path d="M0,0" fill="none" stroke="none"/>'), set())
