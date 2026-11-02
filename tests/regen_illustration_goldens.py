"""Regenerate the illustrated-card goldens and the contact sheet by hand.

`uv run python -m tests.regen_illustration_goldens` rewrites the goldens.
`uv run python -m tests.regen_illustration_goldens --contact-sheet` also writes
`docs/media/illustrated-card-samples.png`. No network calls.
"""

import io
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from ai_trend_pipeline.media.fonts import BODY_FONT_PATH, load_font
from ai_trend_pipeline.media.illustrations.renderer import IllustratedCardProvider
from tests.illustrated_cases import CASES, Case

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "media" / "illustrated"
SHEET_PATH = Path(__file__).parent.parent / "docs" / "media" / "illustrated-card-samples.png"
SHEET_COLUMNS = 3
SHEET_SCALE = 0.5  # `Image.reduce(2)` below is exactly half size
LABEL_HEIGHT = 40
SHEET_PADDING = 16
MAX_SHEET_BYTES = 1_500_000


def write_goldens(provider: IllustratedCardProvider) -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for case in CASES:
        data = provider.illustrate(case.data()).rendered.data
        (GOLDEN_DIR / f"{case.name}.png").write_bytes(data)
        print(f"wrote {case.name}.png ({len(data)} bytes)")


def _label(provider: IllustratedCardProvider, case: Case) -> str:
    status = provider.illustrate(case.data()).status.value
    return f"{case.topic_label} / {status}: {case.name}"


def write_contact_sheet(provider: IllustratedCardProvider) -> None:
    font = load_font(BODY_FONT_PATH, 18)
    tile_w, tile_h = round(1200 * SHEET_SCALE), round(675 * SHEET_SCALE)
    rows = (len(CASES) + SHEET_COLUMNS - 1) // SHEET_COLUMNS
    cell_w, cell_h = tile_w + SHEET_PADDING, tile_h + LABEL_HEIGHT + SHEET_PADDING
    sheet = Image.new(
        "RGB", (SHEET_COLUMNS * cell_w + SHEET_PADDING, rows * cell_h + SHEET_PADDING), "#ffffff"
    )
    draw = ImageDraw.Draw(sheet)
    for index, case in enumerate(CASES):
        data = provider.illustrate(case.data()).rendered.data
        tile = Image.open(io.BytesIO(data)).convert("RGB").reduce(2)
        x = SHEET_PADDING + (index % SHEET_COLUMNS) * cell_w
        y = SHEET_PADDING + (index // SHEET_COLUMNS) * cell_h
        sheet.paste(tile, (x, y))
        draw.text((x, y + tile_h + 8), _label(provider, case), font=font, fill="#15172b")
    SHEET_PATH.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    sheet.save(buf, "PNG", optimize=True)
    if buf.tell() > MAX_SHEET_BYTES:
        sheet = sheet.quantize(colors=128).convert("RGB")
        buf = io.BytesIO()
        sheet.save(buf, "PNG", optimize=True)
    SHEET_PATH.write_bytes(buf.getvalue())
    print(f"wrote {SHEET_PATH} ({buf.tell()} bytes)")


def main(argv: list[str]) -> None:
    provider = IllustratedCardProvider()
    write_goldens(provider)
    if "--contact-sheet" in argv:
        write_contact_sheet(provider)


if __name__ == "__main__":
    main(sys.argv[1:])
