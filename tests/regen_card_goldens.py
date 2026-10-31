"""Regenerate golden card PNGs by hand: `uv run python -m tests.regen_card_goldens`."""

from pathlib import Path

from ai_trend_pipeline.media.card import StoryCardRenderer
from tests.card_cases import BRIEFS, GOLDENS

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "media" / "cards"


def main() -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for name, (brief_key, theme, (width, height)) in GOLDENS.items():
        data = StoryCardRenderer(theme, width, height).render_static(BRIEFS[brief_key]).data
        (GOLDEN_DIR / f"{name}.png").write_bytes(data)
        print(f"wrote {name}.png ({len(data)} bytes)")


if __name__ == "__main__":
    main()
