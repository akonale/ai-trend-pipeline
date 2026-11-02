"""Dev-only helper that normalises a downloaded Open Doodles SVG. No network calls.

Usage: `uv run python -m tests.vendor_illustrations RAW.svg OUT.svg [DECIMALS]`

Steps (see the 065 spec): inline `style` into attributes, drop metadata, comments, `<title>`,
`<desc>` and editor namespaces, flatten colours to `#rrggbb`, round coordinates to one decimal
and drop attributes the sanitiser would reject. It prints the SHA-256 and the colours found, which
go into `catalogue.json`.
"""

import hashlib
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SVG_NS = "http://www.w3.org/2000/svg"
KEEP_TAGS = {"svg", "g", "defs", "clipPath", "path", "circle", "ellipse", "rect", "line"} | {
    "polyline",
    "polygon",
}
KEEP_ATTRS = {
    "viewBox",
    "d",
    "points",
    "transform",
    "cx",
    "cy",
    "r",
    "rx",
    "ry",
    "x",
    "y",
    "x1",
    "y1",
    "x2",
    "y2",
    "fill",
    "stroke",
    "stroke-width",
    "stroke-linecap",
    "stroke-linejoin",
    "stroke-miterlimit",
    "fill-rule",
    "clip-rule",
    "opacity",
    "fill-opacity",
    "stroke-opacity",
}
_NUMBER = re.compile(r"-?\d+\.\d+")
_PAINT = {"fill", "stroke"}
_SHORT_HEX = re.compile(r"^#([0-9a-fA-F])([0-9a-fA-F])([0-9a-fA-F])$")


def _round(match: re.Match[str], decimals: int = 1) -> str:
    text = f"{float(match.group(0)):.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


def _colour(value: str) -> str:
    short = _SHORT_HEX.match(value)
    if short:
        value = "#" + "".join(ch * 2 for ch in short.groups())
    return value.lower() if value.startswith("#") else value


def _inline_style(attrs: dict[str, str]) -> None:
    style = attrs.pop("style", "")
    for part in style.split(";"):
        if ":" in part:
            key, value = part.split(":", 1)
            attrs[key.strip()] = value.strip()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _walk(element: ET.Element, decimals: int) -> None:
    attrs = dict(element.attrib)
    _inline_style(attrs)
    element.attrib.clear()
    for key, value in attrs.items():
        if key not in KEEP_ATTRS:
            continue
        if key in _PAINT:
            value = _colour(value)
        elif key in {"d", "points", "transform"}:
            value = _NUMBER.sub(lambda m: _round(m, decimals), value)
        element.set(key, value)
    for child in list(element):
        if _local(child.tag) not in KEEP_TAGS:
            element.remove(child)
            continue
        child.tag = f"{{{SVG_NS}}}{_local(child.tag)}"
        _walk(child, decimals)


def _compact(element: ET.Element) -> None:
    element.text = None
    element.tail = None
    for child in element:
        _compact(child)


def normalise(raw: str, decimals: int = 1) -> str:
    root = ET.fromstring(raw)
    root.tag = f"{{{SVG_NS}}}svg"
    _walk(root, decimals)
    _compact(root)
    ET.register_namespace("", SVG_NS)
    return ET.tostring(root, encoding="unicode") + "\n"


def colours(svg: str) -> list[str]:
    return sorted(set(re.findall(r'(?:fill|stroke)="(#[0-9a-f]{6})"', svg)))


def main(argv: list[str]) -> None:
    raw_path, out_path = Path(argv[1]), Path(argv[2])
    decimals = int(argv[3]) if len(argv) > 3 else 1
    svg = normalise(raw_path.read_text(encoding="utf-8"), decimals)
    out_path.write_text(svg, encoding="utf-8")
    digest = hashlib.sha256(svg.encode("utf-8")).hexdigest()
    print(
        f"{out_path.name}: {len(svg.encode('utf-8'))} bytes sha256={digest} colours={colours(svg)}"
    )


if __name__ == "__main__":
    main(sys.argv)
