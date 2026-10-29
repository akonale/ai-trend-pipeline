"""Tests for the HN source connector and its Temporal activity wrapper."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx
import pytest
from temporalio.testing import ActivityEnvironment

from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.hn import HN_BASE_URL, make_hn_client
from ai_trend_pipeline.sources import SourceName, SourcePollInput, SourcePollOutput
from ai_trend_pipeline.sources.hn import (
    HNSourceActivities,
    HNSourceConnector,
    make_source_hn_client,
)

FIXTURE = Path(__file__).parent / "fixtures" / "hn_source_poll.json"
EXPECTED_IDS = ["2001", "2004", "2005", "2006", "2007"]


def _fixture() -> tuple[list[int], dict[str, object]]:
    data = cast(dict[str, object], json.loads(FIXTURE.read_text()))
    return cast(list[int], data["topstories"]), cast(dict[str, object], data["items"])


def _make_client(
    requests: list[str] | None = None, topstories_status: int = 200
) -> httpx.AsyncClient:
    topstories, items = _fixture()

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


async def _poll(client: httpx.AsyncClient, limit: int = 30) -> SourcePollOutput:
    connector = HNSourceConnector(client)
    return await connector.poll(SourcePollInput(source=SourceName.HN, limit=limit))


@pytest.mark.asyncio
async def test_hn_poll_matches_fixture_without_network() -> None:
    result = await _poll(_make_client())
    assert [event.external_id for event in result.events] == EXPECTED_IDS
    first = result.events[0]
    assert first.source is SourceName.HN
    assert first.title == "Show HN: Normal story"
    assert str(first.url) == "https://example.com/normal"
    assert first.published_at == datetime.fromtimestamp(1760000000, tz=UTC)
    assert first.metrics == {"score": 100, "comments": 10}
    ask = next(event for event in result.events if event.external_id == "2004")
    assert ask.url is None
    assert ask.published_at == datetime.fromtimestamp(1760000100, tz=UTC)
    no_time = next(event for event in result.events if event.external_id == "2007")
    assert no_time.published_at is None


@pytest.mark.asyncio
async def test_hn_poll_limit_caps_item_requests() -> None:
    requests: list[str] = []
    await _poll(_make_client(requests), limit=3)
    assert requests.count("/v0/topstories.json") == 1
    assert len([path for path in requests if "/item/" in path]) <= 3


@pytest.mark.asyncio
async def test_hn_poll_http_error_raises() -> None:
    with pytest.raises(httpx.HTTPStatusError):
        await _poll(_make_client(topstories_status=500))


@pytest.mark.asyncio
async def test_hn_poll_rejects_wrong_source_before_http() -> None:
    requests: list[str] = []
    connector = HNSourceConnector(_make_client(requests))
    with pytest.raises(ValueError):
        await connector.poll(SourcePollInput(source=SourceName.FAKE))
    assert requests == []


@pytest.mark.asyncio
async def test_hn_activity_returns_source_poll_output() -> None:
    activities = HNSourceActivities(_make_client())
    result = await ActivityEnvironment().run(
        activities.poll_hn_source, SourcePollInput(source=SourceName.HN)
    )
    assert isinstance(result, SourcePollOutput)
    assert result.source is SourceName.HN
    assert [event.external_id for event in result.events] == EXPECTED_IDS


def test_make_source_hn_client_uses_setting_timeout() -> None:
    settings = Settings(source_poll_timeout_s=7.5)
    client = make_source_hn_client(settings)
    assert str(client.base_url).rstrip("/") == HN_BASE_URL.rstrip("/")
    assert client.timeout.read == 7.5
    default_client = make_hn_client()
    assert default_client.timeout.read == 10.0
