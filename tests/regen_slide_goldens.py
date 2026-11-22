"""Regenerate golden slide PNGs by hand: `uv run python -m tests.regen_slide_goldens`."""

from pathlib import Path

from ai_trend_pipeline.media.carousel.templates import slide_template_for
from tests.slide_cases import GOLDENS, sample_art

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "media" / "slides"


def main() -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for name, (template, slide_overlay, seed) in GOLDENS.items():
        slide = slide_template_for(template).compose(sample_art(seed=seed), slide_overlay)
        (GOLDEN_DIR / f"{name}.png").write_bytes(slide.png)
        print(f"wrote {name}.png ({len(slide.png)} bytes, truncated={slide.truncated})")


if __name__ == "__main__":
    main()
