"""SVG allowlist sanitiser. It runs over every vendored asset in tests and again at render time."""

import re
import xml.etree.ElementTree as ET
from collections.abc import Collection

SVG_NS = "http://www.w3.org/2000/svg"
MAX_SVG_BYTES = 64 * 1024

ALLOWED_ELEMENTS = frozenset(
    {"svg", "g", "defs", "clipPath", "path", "circle", "ellipse", "rect", "line"}
    | {"polyline", "polygon"}
)
GEOMETRY_ATTRIBUTES = frozenset(
    {"id", "xmlns", "version", "viewBox", "width", "height", "d", "points", "transform"}
    | {"cx", "cy", "r", "rx", "ry", "x", "y", "x1", "y1", "x2", "y2"}
)
PAINT_ATTRIBUTES = frozenset(
    {"fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin"}
    | {"stroke-miterlimit", "fill-rule", "clip-rule", "opacity", "fill-opacity", "stroke-opacity"}
)
ALLOWED_ATTRIBUTES = GEOMETRY_ATTRIBUTES | PAINT_ATTRIBUTES | {"clip-path"}

_FORBIDDEN_MARKERS = ("<!doctype", "<!entity", "<?xml-stylesheet")
_CLIP_PATH = re.compile(r"^url\(#[A-Za-z0-9_-]+\)$")
_HEX = re.compile(r"^#[0-9a-f]{6}$")
_PATH_DATA = re.compile(r"^[MmLlHhVvCcSsQqTtAaZz0-9\s.,+\-eE]*$")
_TRANSFORM = re.compile(r"^[a-zA-Z0-9\s.,()+\-]*$")
_PLAIN_VALUE = re.compile(r"^[A-Za-z0-9\s.,+\-#%]*$")
_PAINT_KEYS = frozenset({"fill", "stroke"})


class UnsafeSvg(ValueError):
    """The SVG breaks an allowlist rule. Never retried: the asset or its record is wrong."""


def _fail(reason: str) -> UnsafeSvg:
    return UnsafeSvg(reason)


def _check_attribute(name: str, value: str, colours: frozenset[str]) -> None:
    if name not in ALLOWED_ATTRIBUTES:
        raise _fail(f"attribute {name!r} is not allowed")
    if name in _PAINT_KEYS:
        if value != "none" and value.lower() not in colours:
            raise _fail(f"{name} {value!r} is not none or a mapped recolour colour")
    elif name == "clip-path":
        if not _CLIP_PATH.match(value):
            raise _fail("clip-path must reference a local #id")
    elif name in {"d", "points"}:
        if not _PATH_DATA.match(value):
            raise _fail(f"{name} has unexpected characters")
    elif name == "transform":
        if not _TRANSFORM.match(value) or "url" in value.lower():
            raise _fail("transform has unexpected characters")
    elif not _PLAIN_VALUE.match(value) or "url" in value.lower():
        raise _fail(f"{name} has unexpected characters")


def sanitize_svg(svg: str, recolour_colours: Collection[str]) -> str:
    """Return `svg` unchanged when it passes every rule, otherwise raise `UnsafeSvg`."""
    if len(svg.encode("utf-8")) > MAX_SVG_BYTES:
        raise _fail("svg is larger than 64 KB")
    lowered = svg.lower()
    for marker in _FORBIDDEN_MARKERS:
        if marker in lowered:
            raise _fail(f"{marker} is not allowed")
    colours = frozenset(colour.lower() for colour in recolour_colours)
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as exc:
        raise _fail("svg is not well-formed xml") from exc
    if root.tag != f"{{{SVG_NS}}}svg" and root.tag != "svg":
        raise _fail("the root element must be svg")
    if "viewBox" not in root.attrib:
        raise _fail("the root svg needs a viewBox")
    for element in root.iter():
        namespace, _, local = element.tag.rpartition("}")
        if namespace not in {"", f"{{{SVG_NS}"} or local not in ALLOWED_ELEMENTS:
            raise _fail(f"element {element.tag!r} is not allowed")
        for name, value in element.attrib.items():
            _check_attribute(name, value, colours)
    return svg
