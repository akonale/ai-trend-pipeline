"""Illustrated card: goldens, determinism, byte budget, layout invariants, palette and contrast."""

import io
import socket
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from ai_trend_pipeline.media.card import CARD_THEMES, CardTheme
from ai_trend_pipeline.media.illustrations.catalogue import load_catalogue, read_asset_svg
from ai_trend_pipeline.media.illustrations.rasterise import rasterise_svg
from ai_trend_pipeline.media.illustrations.renderer import IllustratedCardProvider
from ai_trend_pipeline.media.illustrations.scene import (
    ILLUSTRATION_BOX,
    PANEL,
    SCENE_PALETTE,
    SCENE_SIZE,
    SCENE_TEMPLATE_VERSION,
    SeedStream,
    layout_scene,
    scene_spec,
)
from ai_trend_pipeline.media.models import MAX_STATIC_BYTES, IllustrationStatus, MediaType
from ai_trend_pipeline.media.providers import MediaContentUnsupported
from tests.illustrated_cases import CASE_BY_NAME, CASES, GOLDEN_NAMES, Case, content_hash
from tests.regen_illustration_goldens import GOLDEN_DIR
from tests.test_design_tokens import contrast, theme

PROVIDER = IllustratedCardProvider()
MAX_GOLDEN_BYTES = 600_000


def render(case: Case) -> bytes:
    return PROVIDER.illustrate(case.data()).rendered.data


@pytest.fixture(scope="module")
def rendered() -> dict[str, bytes]:
    return {case.name: render(case) for case in CASES}


def test_outputs_are_valid_rgb_pngs_within_the_byte_budget(rendered: dict[str, bytes]) -> None:
    for name, data in rendered.items():
        assert len(data) <= MAX_GOLDEN_BYTES < MAX_STATIC_BYTES, name
        image = Image.open(io.BytesIO(data))
        image.load()
        assert image.format == "PNG" and image.mode == "RGB" and image.size == SCENE_SIZE


def test_provider_output_metadata() -> None:
    output = PROVIDER.illustrate(CASE_BY_NAME["health"].data())
    assert output.status is IllustrationStatus.MATCHED
    assert output.description == "a person meditating cross-legged"
    assert output.rendered.media_type is MediaType.PNG
    assert (output.rendered.width, output.rendered.height) == (1200, 675)
    assert output.rendered.duration_ms is None
    assert PROVIDER.name == "illustrated-card"
    assert SCENE_TEMPLATE_VERSION in PROVIDER.version


def test_rendering_twice_gives_identical_bytes(rendered: dict[str, bytes]) -> None:
    for case in (CASE_BY_NAME["education-a"], CASE_BY_NAME["generic-ai"]):
        assert render(case) == rendered[case.name]
        assert (
            IllustratedCardProvider().illustrate(case.data()).rendered.data == rendered[case.name]
        )


def test_statuses_follow_the_match_rules() -> None:
    expected = {
        "education-a": IllustrationStatus.MATCHED,
        "education-b": IllustrationStatus.MATCHED,
        "health": IllustrationStatus.MATCHED,
        "long-ellipsised": IllustrationStatus.MATCHED,
        "generic-no-tags": IllustrationStatus.GENERIC,
        "generic-ai": IllustrationStatus.GENERIC,
        "generic-energy": IllustrationStatus.GENERIC,
        "generic-summary-only": IllustrationStatus.GENERIC,
        "unicode": IllustrationStatus.GENERIC,
    }
    for name, status in expected.items():
        assert PROVIDER.illustrate(CASE_BY_NAME[name].data()).status is status, name


def test_same_topic_with_different_seeds_differs(rendered: dict[str, bytes]) -> None:
    pngs = {rendered[f"same-topic-seed-{n}"] for n in (1, 2, 3)}
    assert len(pngs) == 3


def _differing_fraction(actual: Image.Image, golden: Image.Image) -> float:
    diff = ImageChops.difference(actual, golden)
    red, green, blue = diff.split()
    worst = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    return sum(worst.histogram()[9:]) / (diff.width * diff.height)


@pytest.mark.parametrize("name", GOLDEN_NAMES)
def test_matches_golden_images(name: str, rendered: dict[str, bytes]) -> None:
    actual = Image.open(io.BytesIO(rendered[name])).convert("RGB")
    golden = Image.open(GOLDEN_DIR / f"{name}.png")
    assert golden.mode == "RGB" and golden.size == actual.size == SCENE_SIZE
    assert _differing_fraction(actual, golden) <= 0.001


def test_the_scene_uses_every_support_colour_across_seeds() -> None:
    supports = {scene_spec(content_hash(str(n))).backdrop_colour for n in range(60)}
    assert supports == set(SCENE_PALETTE.supports)
    for n in range(60):
        spec = scene_spec(content_hash(str(n)))
        assert spec.backdrop_colour != spec.drawing_colour
        assert 2 <= len(spec.accents) <= 3


def test_seed_stream_is_deterministic_and_not_python_random() -> None:
    a, b = SeedStream(content_hash("x")), SeedStream(content_hash("x"))
    assert [a.next_byte() for _ in range(80)] == [b.next_byte() for _ in range(80)]
    assert [SeedStream(content_hash("y")).next_byte() for _ in range(8)] != [
        SeedStream(content_hash("x")).next_byte() for _ in range(8)
    ]
    source = Path(__file__).parent.parent / "src/ai_trend_pipeline/media/illustrations/scene.py"
    assert "import random" not in source.read_text()


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_layout_invariants_hold(case: Case) -> None:
    data = case.data()
    layout = layout_scene(data.headline, scene_spec(data.story_content_hash), aspect=4 / 3)
    content = layout.content
    for line in (*layout.headline.lines, layout.wordmark):
        ink = line.ink
        assert ink.left >= content.left and ink.right <= content.right
        assert ink.top >= content.top and ink.bottom <= content.bottom
    assert max(line.ink.bottom for line in layout.headline.lines) <= layout.footer_top
    assert len(layout.headline.lines) <= 5
    assert layout.art_area.left > PANEL.right + 12
    box = layout.illustration
    assert box is not None
    assert box.left >= layout.art_area.left and box.right <= layout.art_area.right
    assert (box.right - box.left) <= ILLUSTRATION_BOX and (box.bottom - box.top) <= ILLUSTRATION_BOX
    assert abs((box.right - box.left) / (box.bottom - box.top) - 4 / 3) < 0.02
    centre = ((box.left + box.right) / 2, (box.top + box.bottom) / 2)
    assert abs(centre[0] - layout.backdrop_centre[0]) <= 1
    assert abs(centre[1] - layout.backdrop_centre[1]) <= 1


def test_long_headline_is_ellipsised_in_five_lines() -> None:
    data = CASE_BY_NAME["long-ellipsised"].data()
    layout = layout_scene(data.headline, scene_spec(data.story_content_hash), aspect=1.0)
    assert layout.truncated and len(layout.headline.lines) == 5
    assert layout.headline.lines[-1].text.endswith("…")
    assert layout.headline.font_size == 36


def test_short_headline_uses_the_largest_size() -> None:
    data = CASE_BY_NAME["generic-ai"].data()
    layout = layout_scene(data.headline, scene_spec(data.story_content_hash), aspect=1.0)
    assert layout.headline.font_size == 64 and not layout.truncated


def test_scene_palette_matches_app_css_tokens() -> None:
    tokens = theme("light")
    light = CARD_THEMES[CardTheme.LIGHT]
    assert SCENE_PALETTE.ground == tokens["bg"] == light.ground
    assert SCENE_PALETTE.grid == tokens["grid"] == light.grid
    assert SCENE_PALETTE.paper == tokens["surface"] == light.panel
    assert SCENE_PALETTE.ink == tokens["text"] == light.text
    assert SCENE_PALETTE.accent == tokens["accent"] == light.accent
    assert SCENE_PALETTE.supports == (
        tokens["highlight"],
        tokens["info"],
        tokens["success"],
        tokens["published"],
    )


def test_contrast_guard() -> None:
    palette = SCENE_PALETTE
    for backdrop in (palette.ground, *palette.supports):
        assert contrast(palette.ink, backdrop) >= 3, backdrop
    assert contrast(palette.ink, palette.paper) >= 4.5
    # Accent is never a backdrop fill: ink on cobalt fails 3:1.
    assert contrast(palette.ink, palette.accent) < 3
    assert palette.accent not in palette.supports


def test_unsupported_glyph_raises_content_unsupported() -> None:
    case = Case("greek", "Ελληνικά headline", "", "none")
    with pytest.raises(MediaContentUnsupported):
        PROVIDER.illustrate(case.data())
    emoji_only = Case("emoji", "\U0001f600\U0001f600", "", "none")
    with pytest.raises(MediaContentUnsupported):
        PROVIDER.illustrate(emoji_only.data())


def test_rendering_makes_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("network used")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    assert render(CASE_BY_NAME["health"]).startswith(b"\x89PNG")


def test_rasteriser_returns_rgba_at_the_requested_size() -> None:
    asset = load_catalogue()[0]
    image = rasterise_svg(read_asset_svg(asset), 220, 165)
    assert image.mode == "RGBA" and image.size == (220, 165)
    assert image.getchannel("A").getextrema()[0] == 0  # transparent outside the drawing
    assert rasterise_svg(read_asset_svg(asset), 220, 165).tobytes() == image.tobytes()


def test_a_broken_asset_raises_content_unsupported(tmp_path: Path) -> None:
    asset = load_catalogue()[0]
    svg_dir = tmp_path
    (svg_dir / asset.file).write_text("<svg/>", encoding="utf-8")
    broken = IllustratedCardProvider(asset_dir=svg_dir)
    data = CASE_BY_NAME["education-a"].data()
    with pytest.raises(MediaContentUnsupported):
        broken.illustrate(data)
