"""Image backend registry, fake backend and Anthropic SVG backend tests; no live calls."""

import json
from pathlib import Path
from typing import cast

import anthropic
import httpx2
import pytest

from ai_trend_pipeline.media.drawn.prompt import requested_roles
from ai_trend_pipeline.media.images.anthropic_svg import AnthropicSvgBackend
from ai_trend_pipeline.media.images.backends import (
    IMAGE_BACKENDS,
    ImageBackendDeps,
    ImageBackendName,
    ImageBackendRefused,
    ImageBackendTransient,
    UnknownImageBackendError,
    image_backend_for,
)
from ai_trend_pipeline.media.images.fake import FakeImageBackend
from ai_trend_pipeline.media.images.models import ImageBatch, ImageBrief
from ai_trend_pipeline.media.models import MAX_STATIC_BYTES, MEDIA_SIGNATURES, MediaType

FIXTURES = Path(__file__).parent / "fixtures"
SENTINEL = "LEAKSENTINEL"


def _client(
    fixture: str | None = None, status: int = 200, connect_error: bool = False
) -> anthropic.AsyncAnthropic:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if connect_error:
            raise httpx2.ConnectError("boom", request=request)
        if status != 200:
            return httpx2.Response(
                status, json={"type": "error", "error": {"type": "api_error", "message": SENTINEL}}
            )
        assert fixture is not None
        return httpx2.Response(200, json=json.loads((FIXTURES / fixture).read_text()))

    return anthropic.AsyncAnthropic(
        api_key="test-key",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


def _deps() -> ImageBackendDeps:
    return ImageBackendDeps(_client("llm_draw_story_images_clean.json"), "m")


def _brief(count: int = 3) -> ImageBrief:
    return ImageBrief(headline="Rivers rise", summary="Floods.", roles=requested_roles(count))


def test_registry_returns_matching_backends() -> None:
    svg = image_backend_for("anthropic-svg", _deps())
    fake = image_backend_for("fake", _deps())
    assert isinstance(svg, AnthropicSvgBackend)
    assert svg.name is ImageBackendName.ANTHROPIC_SVG
    assert isinstance(fake, FakeImageBackend)
    assert fake.name is ImageBackendName.FAKE


@pytest.mark.parametrize("name", ["nope", ""])
def test_unknown_name_raises(name: str) -> None:
    with pytest.raises(UnknownImageBackendError, match="Unknown image backend"):
        image_backend_for(name, _deps())


def test_every_name_has_one_registry_entry() -> None:
    assert set(IMAGE_BACKENDS) == set(ImageBackendName)


@pytest.mark.asyncio
async def test_fake_returns_valid_png_per_role_and_records_brief() -> None:
    fake = FakeImageBackend()
    brief = _brief(3)
    batch = await fake.generate(brief)
    assert [i.role for i in batch.images] == list(brief.roles)
    for image in batch.images:
        assert image.png.startswith(MEDIA_SIGNATURES[MediaType.PNG])
        assert len(image.png) <= MAX_STATIC_BYTES
        assert (image.width, image.height) == (1200, 900)
    assert fake.briefs == [brief]


@pytest.mark.asyncio
async def test_fake_modes() -> None:
    assert (await FakeImageBackend(empty=True).generate(_brief())) == ImageBatch()
    with pytest.raises(ImageBackendTransient):
        await FakeImageBackend(transient=True).generate(_brief())
    with pytest.raises(ImageBackendRefused):
        await FakeImageBackend(refused=True).generate(_brief())


@pytest.mark.asyncio
async def test_svg_clean_returns_three_images() -> None:
    batch = await AnthropicSvgBackend(_client("llm_draw_story_images_clean.json"), "m").generate(
        _brief()
    )
    assert [i.role.value for i in batch.images] == ["hero", "concept", "detail"]
    assert batch.rejects == {}
    assert batch.input_tokens > 0


@pytest.mark.asyncio
async def test_svg_partial_has_fewer_images_and_rejects() -> None:
    batch = await AnthropicSvgBackend(_client("llm_draw_story_images_partial.json"), "m").generate(
        _brief()
    )
    assert 0 < len(batch.images) < 3
    assert sum(batch.rejects.values()) >= 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fixture",
    [
        "llm_draw_story_images_all_rejected.json",
        "llm_draw_story_images_malformed.json",
        "llm_draw_story_images_no_tool.json",
        "llm_draw_story_images_max_tokens.json",
    ],
)
async def test_svg_unusable_replies_give_empty_batch(fixture: str) -> None:
    batch = await AnthropicSvgBackend(_client(fixture), "m").generate(_brief())
    assert batch.images == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500])
async def test_svg_transient_statuses(status: int) -> None:
    with pytest.raises(ImageBackendTransient) as info:
        await AnthropicSvgBackend(_client(status=status), "m").generate(_brief())
    _assert_clean(info.value)


@pytest.mark.asyncio
async def test_svg_connect_error_is_transient() -> None:
    with pytest.raises(ImageBackendTransient) as info:
        await AnthropicSvgBackend(_client(connect_error=True), "m").generate(_brief())
    _assert_clean(info.value)


@pytest.mark.asyncio
async def test_svg_400_is_refused() -> None:
    with pytest.raises(ImageBackendRefused) as info:
        await AnthropicSvgBackend(_client(status=400), "m").generate(_brief())
    _assert_clean(info.value)


def _assert_clean(exc: BaseException) -> None:
    assert SENTINEL not in str(exc)
    assert cast(object, exc.__cause__) is None
    assert cast(object, exc.__context__) is None
