"""One typed wrapper around `resvg-py`. SVGs here have no text, so system fonts are skipped."""

import io

import resvg_py
from PIL import Image


def rasterise_svg(svg: str, width: int, height: int) -> Image.Image:
    """Render `svg` to an RGBA image of exactly `width` x `height` pixels."""
    png = resvg_py.svg_to_bytes(svg_string=svg, width=width, height=height, skip_system_fonts=True)
    image = Image.open(io.BytesIO(png))
    image.load()
    return image.convert("RGBA")
