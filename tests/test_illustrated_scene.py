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
    ACCENT_SLOTS,
    BACKDROP_SIZE,
    ILLUSTRATION_BOX,
    PANEL,
    SCENE_PALETTE,
    SCENE_SIZE,
    SCENE_TEMPLATE_VERSION,
    Accent,
    AccentKind,
    BackdropShape,
    SeedStream,
    accent_box,
    accent_problems,
    backdrop_bounds,
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
    assert SCENE_TEMPLATE_VERSION == "2" and SCENE_TEMPLATE_VERSION in PROVIDER.version


def test_rendering_twice_gives_identical_bytes(rendered: dict[str, bytes]) -> None:
    for case in (CASE_BY_NAME["education-a"], CASE_BY_NAME["generic-no-tags"]):
        assert render(case) == rendered[case.name]
        assert (
            IllustratedCardProvider().illustrate(case.data()).rendered.data == rendered[case.name]
        )


def test_statuses_follow_the_match_rules() -> None:
    generic = {"generic-no-tags", "generic-summary-only", "unicode"}
    for case in CASES:
        status = IllustrationStatus.GENERIC if case.name in generic else IllustrationStatus.MATCHED
        assert PROVIDER.illustrate(case.data()).status is status, case.name


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
    longest = max(box.right - box.left, box.bottom - box.top)
    assert 0.75 * BACKDROP_SIZE <= longest <= 0.85 * BACKDROP_SIZE
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
    data = CASE_BY_NAME["generic-no-tags"].data()
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


def test_rasteriser_returns_rgba_at_the_fitted_size() -> None:
    from ai_trend_pipeline.media.illustrations.scene import fit_box, svg_aspect

    asset = load_catalogue()[0]
    svg = read_asset_svg(asset)
    width, height = fit_box(svg_aspect(svg), 220)
    image = rasterise_svg(svg, width, height)
    assert image.mode == "RGBA"
    assert abs(image.size[0] - width) <= 1 and abs(image.size[1] - height) <= 1
    assert image.getchannel("A").getextrema()[0] == 0  # transparent outside the drawing
    assert rasterise_svg(svg, width, height).tobytes() == image.tobytes()


def test_a_broken_asset_raises_content_unsupported(tmp_path: Path) -> None:
    asset = load_catalogue()[0]
    svg_dir = tmp_path
    (svg_dir / asset.file).write_text("<svg/>", encoding="utf-8")
    broken = IllustratedCardProvider(asset_dir=svg_dir)
    data = CASE_BY_NAME["education-a"].data()
    with pytest.raises(MediaContentUnsupported):
        broken.illustrate(data)


def test_fitted_drawing_and_burst_fill_about_80_percent_of_the_tile() -> None:
    assert ILLUSTRATION_BOX == round(0.8 * BACKDROP_SIZE)
    for case in CASES:
        data = case.data()
        spec = scene_spec(data.story_content_hash)
        for aspect in (0.6, 1.0, 1.6):
            box = layout_scene(data.headline, spec, aspect=aspect).illustration
            assert box is not None
            longest = max(box.right - box.left, box.bottom - box.top)
            assert 0.75 * BACKDROP_SIZE <= longest <= 0.85 * BACKDROP_SIZE
    assert BACKDROP_SIZE == 440


@pytest.mark.parametrize("shape", list(BackdropShape))
@pytest.mark.parametrize("kind", list(AccentKind))
def test_every_accent_slot_is_inside_the_art_area_and_clear_of_the_backdrop(
    shape: BackdropShape, kind: AccentKind
) -> None:
    from ai_trend_pipeline.media.illustrations.scene import BACKDROP_CENTRE, Box, fit_box

    assert len(ACCENT_SLOTS) >= 8 and len(set(ACCENT_SLOTS)) == len(ACCENT_SLOTS)
    for aspect in (0.6, 1.0, 1.6):
        width, height = fit_box(aspect)
        cx, cy = BACKDROP_CENTRE
        drawing = Box(cx - width // 2, cy - height // 2, cx + width // 2, cy + height // 2)
        for x, y in ACCENT_SLOTS:
            accent = Accent(kind, x, y)
            assert accent_problems(accent, shape, drawing) == [], (shape, kind, x, y)
            box = accent_box(accent)
            assert box.left - 680 >= 8 and 1160 - box.right >= 8
            assert box.top - 40 >= 8 and 623 - box.bottom >= 8
    bounds = backdrop_bounds(shape)
    assert bounds.right <= 1200 - 16 and bounds.bottom <= 623 and bounds.top >= 40


def test_accent_sizes_respect_the_limits() -> None:
    dot, ring, bar = (accent_box(Accent(kind, 500, 500)) for kind in AccentKind)
    assert dot.right - dot.left <= 24 and dot.bottom - dot.top <= 24
    assert ring.right - ring.left <= 28 and ring.bottom - ring.top <= 28
    assert (bar.right - bar.left, bar.bottom - bar.top) == (56, 12)


def test_layout_checks_hold_for_200_hashes_and_accents_are_distinct_slots() -> None:
    seen: set[BackdropShape] = set()
    for n in range(200):
        spec = scene_spec(content_hash(f"many-{n}"))
        layout = layout_scene("A headline", spec, aspect=1.25)
        seen.add(spec.backdrop_shape)
        slots = [(a.x, a.y) for a in spec.accents]
        assert len(set(slots)) == len(slots) and set(slots) <= set(ACCENT_SLOTS)
        assert layout.illustration is not None
        for accent in spec.accents:
            assert accent_problems(accent, spec.backdrop_shape, layout.illustration) == []
    assert seen == set(BackdropShape)
