"""Vendored Archivo fonts: paths, cached loading and a pure-`struct` cmap glyph-coverage parser."""

import struct
from functools import cache
from pathlib import Path

from PIL import ImageFont

FONT_DIR = Path(__file__).parent / "fonts"
HEADLINE_FONT_PATH = FONT_DIR / "Archivo-ExtraBold.ttf"
BODY_FONT_PATH = FONT_DIR / "Archivo-Medium.ttf"
LICENCE_PATH = FONT_DIR / "OFL.txt"


@cache
def load_font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    """BASIC layout avoids output differences between machines with and without libraqm."""
    return ImageFont.truetype(str(path), size, layout_engine=ImageFont.Layout.BASIC)


def _format4(data: bytes, offset: int) -> set[int]:
    seg_x2 = struct.unpack_from(">H", data, offset + 6)[0]
    count = seg_x2 // 2
    ends = offset + 14
    starts = ends + seg_x2 + 2
    deltas = starts + seg_x2
    range_offsets = deltas + seg_x2
    found: set[int] = set()
    for i in range(count):
        end = struct.unpack_from(">H", data, ends + 2 * i)[0]
        start = struct.unpack_from(">H", data, starts + 2 * i)[0]
        delta = struct.unpack_from(">h", data, deltas + 2 * i)[0]
        range_offset = struct.unpack_from(">H", data, range_offsets + 2 * i)[0]
        if start > end or start == 0xFFFF:
            continue
        for code in range(start, end + 1):
            if range_offset == 0:
                glyph = (code + delta) & 0xFFFF
            else:
                at = range_offsets + 2 * i + range_offset + 2 * (code - start)
                raw = struct.unpack_from(">H", data, at)[0]
                glyph = (raw + delta) & 0xFFFF if raw else 0
            if glyph:
                found.add(code)
    return found


def _format12(data: bytes, offset: int) -> set[int]:
    groups = struct.unpack_from(">I", data, offset + 12)[0]
    found: set[int] = set()
    for i in range(groups):
        start, end, glyph = struct.unpack_from(">III", data, offset + 16 + 12 * i)
        if glyph:
            found.update(range(start, end + 1))
    return found


@cache
def supported_codepoints(path: Path) -> frozenset[int]:
    """Code points the font maps to a glyph (cmap formats 4 and 12; Unicode platforms only)."""
    data = path.read_bytes()
    table_count = struct.unpack_from(">H", data, 4)[0]
    cmap_offset = -1
    for i in range(table_count):
        tag, _, offset, _ = struct.unpack_from(">4sIII", data, 12 + 16 * i)
        if tag == b"cmap":
            cmap_offset = offset
    if cmap_offset < 0:
        return frozenset()
    records = struct.unpack_from(">H", data, cmap_offset + 2)[0]
    found: set[int] = set()
    for i in range(records):
        platform, encoding, sub = struct.unpack_from(">HHI", data, cmap_offset + 4 + 8 * i)
        if not (platform == 0 or (platform == 3 and encoding in (1, 10))):
            continue
        fmt = struct.unpack_from(">H", data, cmap_offset + sub)[0]
        if fmt == 4:
            found |= _format4(data, cmap_offset + sub)
        elif fmt == 12:
            found |= _format12(data, cmap_offset + sub)
    return frozenset(found)
