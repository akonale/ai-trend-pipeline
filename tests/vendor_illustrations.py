"""Dev-only helper that normalises a local SVG (an Open Doodles download or a project glyph).

No network calls.

Usage: `uv run python -m tests.vendor_illustrations RAW.svg OUT.svg [DECIMALS]`

Steps (see the 065 spec): inline `style` into attributes, drop metadata, comments, `<title>`,
`<desc>` and editor namespaces, flatten colours to `#rrggbb`, round coordinates to one decimal,
drop attributes the sanitiser would reject, then crop the `viewBox` to the content bounds
(render at 1024 px, take the alpha bounding box, pad by 2 % of its long side, round to 2
decimals). It prints the SHA-256 and the colours found, which go into `catalogue.json`.
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
    "width",
    "height",
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


def _walk(element: ET.Element, decimals: int, root: bool = False) -> None:
    attrs = dict(element.attrib)
    if root:
        attrs.pop("width", None)  # the root size comes from the viewBox
        attrs.pop("height", None)
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
    _walk(root, decimals, root=True)
    _compact(root)
    ET.register_namespace("", SVG_NS)
    return ET.tostring(root, encoding="unicode") + "\n"


CROP_RENDER_PX = 1024
CROP_PADDING = 0.02
_ROOT_VIEWBOX = re.compile(r'viewBox="([^"]+)"')


def crop_viewbox(svg: str) -> str:
    """Rewrite the root viewBox to the content bounds. Geometry is left untouched."""
    from ai_trend_pipeline.media.illustrations.rasterise import rasterise_svg
    from ai_trend_pipeline.media.illustrations.scene import svg_aspect

    match = _ROOT_VIEWBOX.search(svg)
    assert match is not None, "svg needs a viewBox"
    x0, y0, vw, vh = (float(part) for part in match.group(1).replace(",", " ").split())
    aspect = svg_aspect(svg)
    width = CROP_RENDER_PX if aspect >= 1 else round(CROP_RENDER_PX * aspect)
    height = round(CROP_RENDER_PX / aspect) if aspect >= 1 else CROP_RENDER_PX
    bbox = rasterise_svg(svg, width, height).getchannel("A").getbbox()
    assert bbox is not None, "svg draws nothing"
    left, top = x0 + bbox[0] / width * vw, y0 + bbox[1] / height * vh
    right, bottom = x0 + bbox[2] / width * vw, y0 + bbox[3] / height * vh
    pad = CROP_PADDING * max(right - left, bottom - top)
    left, top, right, bottom = left - pad, top - pad, right + pad, bottom + pad
    box = f"{left:.2f} {top:.2f} {right - left:.2f} {bottom - top:.2f}"
    return svg[: match.start(1)] + box + svg[match.end(1) :]


def colours(svg: str) -> list[str]:
    return sorted(set(re.findall(r'(?:fill|stroke)="(#[0-9a-f]{6})"', svg)))


def main(argv: list[str]) -> None:
    raw_path, out_path = Path(argv[1]), Path(argv[2])
    decimals = int(argv[3]) if len(argv) > 3 else 1
    svg = crop_viewbox(normalise(raw_path.read_text(encoding="utf-8"), decimals))
    out_path.write_text(svg, encoding="utf-8")
    digest = hashlib.sha256(svg.encode("utf-8")).hexdigest()
    print(
        f"{out_path.name}: {len(svg.encode('utf-8'))} bytes sha256={digest} colours={colours(svg)}"
    )


if __name__ == "__main__":
    main(sys.argv)
