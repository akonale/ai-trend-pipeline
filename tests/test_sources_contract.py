"""Shared contract suite: the same logical cases against the HN and fake connectors."""

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import httpx
import pytest
from pydantic import ValidationError

from ai_trend_pipeline.hn import HN_BASE_URL
from ai_trend_pipeline.sources import (
    SourceConnector,
    SourceName,
    SourcePollInput,
    SourcePollOutput,
)
from ai_trend_pipeline.sources.fake import FakeSourceConnector
from ai_trend_pipeline.sources.hn import HNSourceConnector
from ai_trend_pipeline.sources.normalize import RawSourceItem

FIXTURE = Path(__file__).parent / "fixtures" / "hn_source_poll.json"
KINDS = ["hn", "fake"]
EXPECTED_IDS = ["2001", "2004", "2005", "2006", "2007"]
OBSERVED = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _fixture() -> tuple[list[int], dict[str, object]]:
    data = cast(dict[str, object], json.loads(FIXTURE.read_text()))
    return cast(list[int], data["topstories"]), cast(dict[str, object], data["items"])


def _hn_connector(topstories: Sequence[int] | None = None) -> HNSourceConnector:
    fixture_topstories, items = _fixture()
    ids = list(fixture_topstories) if topstories is None else list(topstories)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v0/topstories.json":
            return httpx.Response(200, json=ids)
        prefix, suffix = "/v0/item/", ".json"
        if path.startswith(prefix) and path.endswith(suffix):
            key = path[len(prefix) : -len(suffix)]
            return httpx.Response(200, content=json.dumps(items[key]))
        raise AssertionError(f"unexpected URL: {request.url}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=HN_BASE_URL)
    return HNSourceConnector(client)


def _fake_items() -> list[RawSourceItem]:
    def item(
        external_id: str,
        title: str | None,
        url: str | None,
        timestamp: int | None,
        *,
        score: int = 0,
        comments: int = 0,
        discarded: bool = False,
    ) -> RawSourceItem:
        return RawSourceItem(
            external_id=external_id,
            title=title,
            url=url,
            published_at=(
                datetime.fromtimestamp(timestamp, tz=UTC) if timestamp is not None else None
            ),
            metrics={"score": score, "comments": comments},
            discarded=discarded,
        )

    return [
        item(
            "2001",
            "Show HN: Normal story",
            "https://example.com/normal",
            1760000000,
            score=100,
            comments=10,
        ),
        item("2002", "   ", "https://example.com/blank-title", 1760000050),
        item("2003", "x" * 201, "https://example.com/long-title", 1760000060),
        item("2004", "Ask HN: No URL", None, 1760000100, score=5),
        item("2005", "Broken scheme", "javascript:alert(1)", 1760000200, score=9),
        item("2006", "FTP item", "ftp://example.com/file", 1760000300, score=11, comments=2),
        item("2007", "No time", "https://example.com/no-time", None, score=1),
        item("2008", "Dead story", "https://example.com/dead", 1760000400, discarded=True),
        item("2009", "Deleted story", "https://example.com/deleted", 1760000500, discarded=True),
        item("2010", "ExampleCo is hiring", "https://example.com/jobs", 1760000600, discarded=True),
        item("2011", "Null item", None, None, discarded=True),
        item("2001", "Duplicate occurrence", "https://example.com/again", 1760000700),
    ]


def _connector(kind: str) -> SourceConnector:
    if kind == "hn":
        return _hn_connector()
    return FakeSourceConnector(_fake_items(), observed_at=OBSERVED)


def _source(kind: str) -> SourceName:
    return SourceName.HN if kind == "hn" else SourceName.FAKE


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_contract_valid_items_are_normalized(kind: str) -> None:
    source = _source(kind)
    result = await _connector(kind).poll(SourcePollInput(source=source, limit=30))
    assert isinstance(result, SourcePollOutput)
    assert result.source is source
    assert [event.external_id for event in result.events] == EXPECTED_IDS
    for event in result.events:
        assert event.source is source
        assert isinstance(event.external_id, str)
        assert event.title.strip() == event.title
        assert event.title
        assert event.url is None or event.url.scheme in {"http", "https"}
        assert event.observed_at.utcoffset() == timedelta(0)
        if event.published_at is not None:
            assert event.published_at.utcoffset() == timedelta(0)
    assert len({event.observed_at for event in result.events}) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_contract_excludes_discarded_blank_and_oversized(kind: str) -> None:
    result = await _connector(kind).poll(SourcePollInput(source=_source(kind), limit=30))
    ids = [event.external_id for event in result.events]
    assert set(ids).isdisjoint({"2002", "2003", "2008", "2009", "2010", "2011"})
    assert ids == EXPECTED_IDS


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_contract_collapses_duplicate_ids(kind: str) -> None:
    result = await _connector(kind).poll(SourcePollInput(source=_source(kind), limit=30))
    duplicates = [event for event in result.events if event.external_id == "2001"]
    assert len(duplicates) == 1
    assert duplicates[0].title == "Show HN: Normal story"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_contract_drops_invalid_url_scheme_but_keeps_event(kind: str) -> None:
    result = await _connector(kind).poll(SourcePollInput(source=_source(kind), limit=30))
    by_id = {event.external_id: event for event in result.events}
    assert by_id["2005"].url is None
    assert by_id["2006"].url is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_contract_missing_published_time_is_none(kind: str) -> None:
    result = await _connector(kind).poll(SourcePollInput(source=_source(kind), limit=30))
    missing = next(event for event in result.events if event.external_id == "2007")
    assert missing.published_at is None
    assert missing.observed_at.utcoffset() == timedelta(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_contract_respects_limit(kind: str) -> None:
    result = await _connector(kind).poll(SourcePollInput(source=_source(kind), limit=2))
    ids = [event.external_id for event in result.events]
    assert len(ids) <= 2
    assert ids == EXPECTED_IDS[: len(ids)]
    for bad in (0, 101):
        with pytest.raises(ValidationError):
            SourcePollInput(source=_source(kind), limit=bad)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_contract_empty_source_gives_empty_output(kind: str) -> None:
    if kind == "hn":
        connector: SourceConnector = _hn_connector(topstories=[])
    else:
        connector = FakeSourceConnector([], observed_at=OBSERVED)
    result = await connector.poll(SourcePollInput(source=_source(kind), limit=30))
    assert result.events == []
    assert result.source is _source(kind)
