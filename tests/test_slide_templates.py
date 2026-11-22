"""Slide templates: registry, goldens, determinism, fit, text, size, bad input, models, alt text."""

import io

import pytest
from PIL import Image, ImageChops
from pydantic import ValidationError

from ai_trend_pipeline.media.carousel.templates import (
    SLIDE_TEMPLATE_VERSION,
    SLIDE_TEMPLATES,
    ComposedSlide,
    SlideOverlay,
    SlideTemplateName,
    UnknownSlideTemplateError,
    slide_template_for,
)
from ai_trend_pipeline.media.carousel.templates.frame import encode_within, slide_alt_text
from ai_trend_pipeline.media.models import MAX_ALT_TEXT_CHARS, MAX_STATIC_BYTES
from ai_trend_pipeline.media.providers import MediaContentUnsupported
from tests.regen_slide_goldens import GOLDEN_DIR
from tests.slide_cases import GOLDENS, noise_art, noise_image, overlay, sample_art

ALL = list(SlideTemplateName)


def _compose(name: SlideTemplateName, art: bytes, ov: SlideOverlay) -> ComposedSlide:
    return slide_template_for(name).compose(art, ov)


def _ov(name: SlideTemplateName, text: str = "Hello there") -> SlideOverlay:
    return overlay(text, "a short caption" if name is SlideTemplateName.NUMBER else None)


def test_registry_has_exactly_four_templates() -> None:
    assert set(SLIDE_TEMPLATES) == set(SlideTemplateName)
    for name in SlideTemplateName:
        template = slide_template_for(name)
        assert template.name is name
        assert template.version == f"{SLIDE_TEMPLATE_VERSION}-{name}-1080x1080"
        assert slide_template_for(str(name)) is template


@pytest.mark.parametrize("name", ["nope", ""])
def test_unknown_template_raises(name: str) -> None:
    with pytest.raises(UnknownSlideTemplateError, match="Unknown slide template"):
        slide_template_for(name)


def _differing_fraction(actual: Image.Image, golden: Image.Image) -> float:
    diff = ImageChops.difference(actual, golden)
    red, green, blue = diff.split()
    worst = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    return sum(worst.histogram()[9:]) / (diff.width * diff.height)


@pytest.mark.parametrize("name", list(GOLDENS))
def test_matches_golden_images(name: str) -> None:
    template, ov, seed = GOLDENS[name]
    slide = _compose(template, sample_art(seed=seed), ov)
    actual = Image.open(io.BytesIO(slide.png))
    assert actual.format == "PNG" and actual.mode == "RGB" and actual.size == (1080, 1080)
    assert (slide.width, slide.height) == (1080, 1080)
    golden = Image.open(GOLDEN_DIR / f"{name}.png")
    assert golden.mode == "RGB" and golden.size == (1080, 1080)
    assert _differing_fraction(actual, golden) <= 0.001


def test_long_text_is_truncated_and_plain_text_is_not() -> None:
    template, ov, seed = GOLDENS["point-long-truncated"]
    assert _compose(template, sample_art(seed=seed), ov).truncated
    template, ov, seed = GOLDENS["point"]
    assert not _compose(template, sample_art(seed=seed), ov).truncated


@pytest.mark.parametrize("name", ALL)
def test_compose_is_byte_identical(name: SlideTemplateName) -> None:
    art = sample_art()
    assert _compose(name, art, _ov(name)).png == _compose(name, art, _ov(name)).png


@pytest.mark.parametrize("name", ALL)
@pytest.mark.parametrize("size", [(1200, 900), (900, 1200)])
def test_input_is_cover_cropped(name: SlideTemplateName, size: tuple[int, int]) -> None:
    flat = Image.new("RGB", size, (255, 0, 0))
    buf = io.BytesIO()
    flat.save(buf, "PNG")
    slide = _compose(name, buf.getvalue(), _ov(name))
    image = Image.open(io.BytesIO(slide.png)).convert("RGB")
    assert image.size == (1080, 1080)
    bottom = 560 if name is SlideTemplateName.NUMBER else 640
    for x, y in [(47, 47), (1020, 47), (47, bottom - 8), (1020, bottom - 8)]:
        assert image.getpixel((x, y)) == (255, 0, 0)


def test_rgba_art_is_flattened_on_white() -> None:
    buf = io.BytesIO()
    Image.new("RGBA", (400, 400), (0, 0, 0, 0)).save(buf, "PNG")
    image = Image.open(
        io.BytesIO(
            _compose(SlideTemplateName.HOOK, buf.getvalue(), _ov(SlideTemplateName.HOOK)).png
        )
    )
    assert image.convert("RGB").getpixel((300, 300)) == (255, 255, 255)


@pytest.mark.parametrize("name", ALL)
def test_cjk_and_emoji_only_main_text_raise(name: SlideTemplateName) -> None:
    for text in ("中文标题测试", "😀😀🎉"):
        with pytest.raises(MediaContentUnsupported):
            _compose(name, sample_art(), overlay(text))


def test_unsupported_caption_is_dropped() -> None:
    art = sample_art()
    for name in (SlideTemplateName.NUMBER, SlideTemplateName.SOURCE):
        dropped = _compose(name, art, overlay("40%", "中文说明"))
        plain = _compose(name, art, overlay("40%", None))
        assert dropped.png == plain.png


@pytest.mark.parametrize(
    "name", [SlideTemplateName.HOOK, SlideTemplateName.POINT, SlideTemplateName.SOURCE]
)
def test_unbroken_200_char_word_stays_inside(name: SlideTemplateName) -> None:
    slide = _compose(name, sample_art(), overlay("W" * 200))
    assert slide.truncated
    assert len(slide.png) <= MAX_STATIC_BYTES


def test_number_fits_one_line_or_raises() -> None:
    art = sample_art()
    slide = _compose(SlideTemplateName.NUMBER, art, overlay("$2.1bn", "revenue"))
    assert not slide.truncated
    with pytest.raises(MediaContentUnsupported, match="one line"):
        _compose(SlideTemplateName.NUMBER, art, overlay("1234567890123456789", "x"))


@pytest.mark.parametrize("name", ALL)
def test_noise_art_is_within_byte_limit(name: SlideTemplateName) -> None:
    slide = _compose(name, noise_art(), _ov(name))
    assert len(slide.png) <= MAX_STATIC_BYTES
    assert slide.png.startswith(b"\x89PNG\r\n\x1a\n")


def test_encode_within_raises_when_nothing_fits() -> None:
    with pytest.raises(MediaContentUnsupported, match="too large"):
        encode_within(noise_image(), limit=10_000)


def _truncate(data: bytes) -> bytes:
    return data[: len(data) // 2]


def _png(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(buf, "PNG")
    return buf.getvalue()


@pytest.mark.parametrize(
    "data",
    [b"not a png at all", _truncate(sample_art()), _png(100, 100), _png(5000, 10)],
    ids=["not-png", "truncated", "small", "thin"],
)
def test_bad_image_input_raises_without_overlay_text(data: bytes) -> None:
    with pytest.raises(MediaContentUnsupported) as info:
        _compose(SlideTemplateName.HOOK, data, overlay("Secret overlay words", "caption words"))
    assert "Secret" not in str(info.value) and "overlay" not in str(info.value)


def test_models_reject_bad_values() -> None:
    good = {"text": "Hi", "position": 1, "total": 2}
    SlideOverlay.model_validate(good)
    for patch in (
        {"position": 3},
        {"total": 1},
        {"total": 5},
        {"text": "see https://example.com"},
        {"text": ""},
        {"text": "   "},
        {"text": "x" * 201},
    ):
        with pytest.raises(ValidationError):
            SlideOverlay.model_validate({**good, **patch})


def test_alt_text_formats() -> None:
    def alt(
        name: SlideTemplateName,
        text: str = "Hi",
        caption: str | None = None,
        image_description: str | None = None,
    ) -> str:
        ov = SlideOverlay(
            text=text, caption=caption, image_description=image_description, position=2, total=4
        )
        return slide_alt_text(name, ov)

    hook, point = SlideTemplateName.HOOK, SlideTemplateName.POINT
    number, source = SlideTemplateName.NUMBER, SlideTemplateName.SOURCE
    assert alt(hook, image_description="a boat") == "Slide 2 of 4. Hi. Illustration: a boat."
    assert alt(point) == "Slide 2 of 4. Hi."
    assert alt(point, text="Done!") == "Slide 2 of 4. Done!"
    assert alt(number, text="40%", caption="of firms", image_description="a boat") == (
        "Slide 2 of 4. 40%: of firms. Illustration: a boat."
    )
    assert alt(number, text="40%") == "Slide 2 of 4. 40%."
    assert alt(source, text="Reuters", caption="Minutes.", image_description="a boat") == (
        "Slide 2 of 4. Source: Reuters. Minutes. Illustration: a boat."
    )
    assert alt(source, text="Reuters") == "Slide 2 of 4. Source: Reuters."
    assert ".." not in alt(source, text="Reuters.", caption="Minutes.")


def test_composed_alt_text_uses_overlay_words_only() -> None:
    template, ov, seed = GOLDENS["number"]
    slide = _compose(template, sample_art(seed=seed), ov)
    assert slide.alt_text == (
        "Slide 3 of 4. 40%: of firms plan to hire again by the end of next quarter. "
        "Illustration: a paper boat on a calm lake."
    )
    assert len(slide.alt_text) <= MAX_ALT_TEXT_CHARS
