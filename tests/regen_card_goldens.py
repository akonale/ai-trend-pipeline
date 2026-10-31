"""Regenerate golden card PNGs by hand: `uv run python -m tests.regen_card_goldens`."""

from pathlib import Path

from ai_trend_pipeline.media.animation import build_story_frames
from ai_trend_pipeline.media.card import StoryCardRenderer, layout_story_card
from tests.card_cases import BRIEFS, GIF_GOLDENS, GOLDENS

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "media" / "cards"
GIF_GOLDEN_DIR = Path(__file__).parent / "fixtures" / "media" / "gifs"


def main() -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for name, (brief_key, theme, (width, height)) in GOLDENS.items():
        data = StoryCardRenderer(theme, width, height).render_static(BRIEFS[brief_key]).data
        (GOLDEN_DIR / f"{name}.png").write_bytes(data)
        print(f"wrote {name}.png ({len(data)} bytes)")
    GIF_GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for name, (brief_key, theme, (width, height), keyframe) in GIF_GOLDENS.items():
        brief = BRIEFS[brief_key]
        layout = layout_story_card(brief, theme, width, height)
        frames = build_story_frames(layout, brief.animation_beats)
        frame = next(f for f in frames if f.keyframe is keyframe)
        frame.image.save(GIF_GOLDEN_DIR / f"{name}.png", "PNG", optimize=True)
        print(f"wrote {name}.png")


if __name__ == "__main__":
    main()
