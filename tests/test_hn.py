"""Tests for the Hacker News top stories activity, using a recorded fixture."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx
import pytest
from pydantic import ValidationError
from temporalio.testing import ActivityEnvironment

from ai_trend_pipeline.hn import (
    HN_BASE_URL,
    HNActivities,
    HNStory,
    HNTopStoriesInput,
    HNTopStoriesOutput,
    make_hn_client,
)

FIXTURE = Path(__file__).parent / "fixtures" / "hn_top_stories.json"
EXPECTED_IDS = [1001, 1002, 1006]


def _load() -> dict[str, object]:
    return cast(dict[str, object], json.loads(FIXTURE.read_text()))


def _make_client(
    requests: list[str] | None = None, topstories_status: int = 200
) -> httpx.AsyncClient:
    data = _load()
    topstories = cast(list[int], data["topstories"])
    items = cast(dict[str, object], data["items"])

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if requests is not None:
            requests.append(path)
        if path == "/v0/topstories.json":
            if topstories_status != 200:
                return httpx.Response(topstories_status)
            return httpx.Response(200, json=topstories)
        prefix, suffix = "/v0/item/", ".json"
        if path.startswith(prefix) and path.endswith(suffix):
            key = path[len(prefix) : -len(suffix)]
            if key in items:
                return httpx.Response(200, content=json.dumps(items[key]))
        raise AssertionError(f"unexpected URL: {request.url}")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=HN_BASE_URL)


async def _run(client: httpx.AsyncClient, limit: int = 30) -> HNTopStoriesOutput:
    activities = HNActivities(client)
    return await ActivityEnvironment().run(
        activities.fetch_hn_top_stories, HNTopStoriesInput(limit=limit)
    )


@pytest.mark.asyncio
async def test_fetch_returns_typed_stories() -> None:
    result = await _run(_make_client())
    assert isinstance(result, HNTopStoriesOutput)
    assert all(isinstance(s, HNStory) for s in result.stories)
    assert [s.id for s in result.stories] == EXPECTED_IDS


@pytest.mark.asyncio
async def test_fetch_filters_non_stories() -> None:
    result = await _run(_make_client())
    ids = {s.id for s in result.stories}
    assert ids.isdisjoint({1003, 1004, 1005, 1007})


@pytest.mark.asyncio
async def test_story_without_url_and_descendants() -> None:
    result = await _run(_make_client())
    ask = next(s for s in result.stories if s.id == 1002)
    assert ask.url is None
    assert ask.comments == 0


@pytest.mark.asyncio
async def test_time_is_utc_datetime() -> None:
    result = await _run(_make_client())
    first = result.stories[0]
    assert first.time.tzinfo is not None
    assert first.time.utcoffset() == UTC.utcoffset(None)
    assert first.time == datetime.fromtimestamp(1760000000, tz=UTC)
    assert first.comments == 42


@pytest.mark.asyncio
async def test_limit_caps_item_requests() -> None:
    requests: list[str] = []
    await _run(_make_client(requests), limit=2)
    assert requests.count("/v0/topstories.json") == 1
    assert sorted(r for r in requests if "/item/" in r) == [
        "/v0/item/1001.json",
        "/v0/item/1002.json",
    ]
    assert len(requests) == 3


@pytest.mark.asyncio
async def test_http_error_raises() -> None:
    with pytest.raises(httpx.HTTPStatusError):
        await _run(_make_client(topstories_status=500))


def test_input_limit_bounds() -> None:
    with pytest.raises(ValidationError):
        HNTopStoriesInput(limit=0)
    with pytest.raises(ValidationError):
        HNTopStoriesInput(limit=101)


def test_make_hn_client_base_url() -> None:
    client = make_hn_client()
    assert str(client.base_url).rstrip("/") == HN_BASE_URL.rstrip("/")
