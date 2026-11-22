"""Shared slide overlays, deterministic art and golden cases for slide template tests."""

import io
import random

from PIL import Image, ImageDraw

from ai_trend_pipeline.media.carousel.templates import SlideOverlay, SlideTemplateName


def sample_art(width: int = 1200, height: int = 900, seed: int = 1) -> bytes:
    """Flat Pillow shapes in seeded colours. No network, no fixtures."""
    rng = random.Random(seed)

    def colour() -> tuple[int, int, int]:
        return (rng.randrange(256), rng.randrange(256), rng.randrange(256))

    image = Image.new("RGB", (width, height), colour())
    draw = ImageDraw.Draw(image)
    for _ in range(6):
        x, y = rng.randrange(width), rng.randrange(height)
        r = rng.randrange(width // 8, width // 3)
        if rng.random() < 0.5:
            draw.ellipse([x - r, y - r, x + r, y + r], fill=colour())
        else:
            draw.rectangle([x - r, y - r // 2, x + r, y + r // 2], fill=colour())
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


def noise_image(width: int = 1080, height: int = 1080, seed: int = 7) -> Image.Image:
    rng = random.Random(seed)
    return Image.frombytes("RGB", (width, height), rng.randbytes(width * height * 3))


def noise_art(width: int = 1080, height: int = 1080, seed: int = 7) -> bytes:
    buf = io.BytesIO()
    noise_image(width, height, seed).save(buf, "PNG")
    return buf.getvalue()


def overlay(
    text: str,
    caption: str | None = None,
    position: int = 2,
    total: int = 4,
    description: str | None = "a paper boat on a calm lake",
) -> SlideOverlay:
    return SlideOverlay(
        text=text,
        caption=caption,
        position=position,
        total=total,
        image_description=description,
    )


LONG_POINT = ("WIDE WORDS MAKE MIGHTY MOMENTS WHEN MOST WOMEN MEET " * 5)[:200].strip()

# name -> (template, overlay, art seed)
GOLDENS: dict[str, tuple[SlideTemplateName, SlideOverlay, int]] = {
    "hook": (
        SlideTemplateName.HOOK,
        overlay("Rates are staying put, and nobody saw it coming", position=1),
        1,
    ),
    "point": (
        SlideTemplateName.POINT,
        overlay("Inflation cooled faster than forecast, so central banks held rates steady."),
        2,
    ),
    "number": (
        SlideTemplateName.NUMBER,
        overlay("40%", "of firms plan to hire again by the end of next quarter", position=3),
        3,
    ),
    "source": (
        SlideTemplateName.SOURCE,
        overlay("Reuters, 12 March", "Central bank minutes and staff forecasts", 4, 4),
        4,
    ),
    "source-no-caption": (
        SlideTemplateName.SOURCE,
        overlay("Reuters, 12 March", None, 4, 4),
        4,
    ),
    "point-long-truncated": (
        SlideTemplateName.POINT,
        overlay(LONG_POINT),
        5,
    ),
    "hook-non-ascii": (
        SlideTemplateName.HOOK,
        overlay("Zürich café’s “naïve” Łódź plan — 😀", position=1),
        6,
    ),
}
