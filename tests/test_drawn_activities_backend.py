"""draw_story_images with an injected image backend."""

import json
from pathlib import Path

import anthropic
import httpx2
import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from ai_trend_pipeline.media.drawn.activities import DrawnImageActivities
from ai_trend_pipeline.media.drawn.store import read_pointer
from ai_trend_pipeline.media.images.fake import FakeImageBackend
from ai_trend_pipeline.media.models import DrawImagesInput, DrawnImagesResult, DrawnStatus

STORY = "a" * 64


def _input() -> DrawImagesInput:
    return DrawImagesInput(
        story_id="s1", story_content_hash=STORY, headline="Rivers rise", summary="Floods."
    )


def _acts(
    root: Path,
    backend: FakeImageBackend | None,
    requests: list[httpx2.Request] | None = None,
    *,
    enabled: bool = True,
    cap: int = 10,
) -> DrawnImageActivities:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if requests is not None:
            requests.append(request)
        return httpx2.Response(500, json={"type": "error", "error": {"type": "api_error"}})

    client = anthropic.AsyncAnthropic(
        api_key="k", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    )
    return DrawnImageActivities(
        client, root, enabled=enabled, model="m", count=3, daily_cap=cap, backend=backend
    )


async def _run(acts: DrawnImageActivities) -> DrawnImagesResult:
    return await ActivityEnvironment().run(acts.draw_story_images, _input())


@pytest.mark.asyncio
async def test_fake_backend_draws_sorted_and_records_provider(tmp_path: Path) -> None:
    requests: list[httpx2.Request] = []
    result = await _run(_acts(tmp_path, FakeImageBackend(), requests))
    assert requests == []
    assert result.status is DrawnStatus.DRAWN
    assert [i.role.value for i in result.images] == ["hero", "concept", "detail"]
    pointer = read_pointer(tmp_path, STORY)
    assert pointer is not None and pointer.provider == "fake"


@pytest.mark.asyncio
async def test_default_backend_records_anthropic_svg(tmp_path: Path) -> None:
    fixtures = Path(__file__).parent / "fixtures"
    body = json.loads((fixtures / "llm_draw_story_images_clean.json").read_text())
    client = anthropic.AsyncAnthropic(
        api_key="k",
        http_client=httpx2.AsyncClient(
            transport=httpx2.MockTransport(lambda r: httpx2.Response(200, json=body))
        ),
    )
    acts = DrawnImageActivities(client, tmp_path, enabled=True, model="m", count=3, daily_cap=10)
    assert (await _run(acts)).status is DrawnStatus.DRAWN
    pointer = read_pointer(tmp_path, STORY)
    assert pointer is not None and pointer.provider == "anthropic-svg"


@pytest.mark.asyncio
async def test_empty_batch_writes_failed_pointer(tmp_path: Path) -> None:
    result = await _run(_acts(tmp_path, FakeImageBackend(empty=True)))
    assert result.status is DrawnStatus.FAILED
    pointer = read_pointer(tmp_path, STORY)
    assert pointer is not None and pointer.status == "failed"


@pytest.mark.asyncio
async def test_transient_maps_to_retryable_error(tmp_path: Path) -> None:
    with pytest.raises(ApplicationError) as info:
        await _run(_acts(tmp_path, FakeImageBackend(transient=True)))
    assert info.value.type == "DrawingTransient"
    assert not info.value.non_retryable


@pytest.mark.asyncio
async def test_refused_maps_to_non_retryable_error(tmp_path: Path) -> None:
    with pytest.raises(ApplicationError) as info:
        await _run(_acts(tmp_path, FakeImageBackend(refused=True)))
    assert info.value.type == "DrawingFailed"
    assert info.value.non_retryable


@pytest.mark.asyncio
async def test_existing_pointer_short_circuits_backend(tmp_path: Path) -> None:
    first = FakeImageBackend()
    await _run(_acts(tmp_path, first))
    second = FakeImageBackend()
    result = await _run(_acts(tmp_path, second))
    assert result.status is DrawnStatus.DRAWN
    assert second.briefs == []


@pytest.mark.asyncio
async def test_disabled_and_capped_skip_backend(tmp_path: Path) -> None:
    fake = FakeImageBackend()
    disabled = await _run(_acts(tmp_path, fake, enabled=False))
    assert disabled.status is DrawnStatus.DISABLED
    # cap is validated >= 1 in settings, but the activity only checks the counter
    capped = await _run(_acts(tmp_path, fake, cap=0))
    assert capped.status is DrawnStatus.CAPPED
    assert fake.briefs == []
