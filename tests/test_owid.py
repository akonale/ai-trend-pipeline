"""Tests for the OWID dataset activity, using a hand-written fixture."""

from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from ai_trend_pipeline.owid import (
    CATALOG,
    NO_MATCH_ERROR_TYPE,
    OWID_BASE_URL,
    FetchOwidInput,
    OwidActivities,
    OwidDataset,
    OwidRow,
    OwidTable,
    make_owid_client,
    match_dataset,
)

FIXTURE = Path(__file__).parent / "fixtures" / "owid_life_expectancy.csv"
TOPIC = "Rising life expectancy worldwide"
PATH = "/grapher/life-expectancy.csv"


def _make_client(
    requests: list[httpx.Request] | None = None,
    status: int = 200,
    body: str | None = None,
) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        if request.url.path != PATH:
            raise AssertionError(f"unexpected URL: {request.url}")
        if status != 200:
            return httpx.Response(status)
        return httpx.Response(200, text=FIXTURE.read_text() if body is None else body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=OWID_BASE_URL)


async def _run(client: httpx.AsyncClient, topic: str = TOPIC, since_year: int = 2000) -> OwidTable:
    activities = OwidActivities(client)
    return await ActivityEnvironment().run(
        activities.fetch_owid_dataset, FetchOwidInput(topic=topic, since_year=since_year)
    )


@pytest.mark.asyncio
async def test_fetch_returns_typed_table() -> None:
    table = await _run(_make_client())
    assert isinstance(table, OwidTable)
    assert table.slug == "life-expectancy"
    assert table.title == "Life expectancy"
    assert table.value_column == "life_expectancy_0"
    assert all(isinstance(r, OwidRow) for r in table.rows)
    assert table.rows[0] == OwidRow(entity="Africa", code=None, year=2000, value=52.2)


@pytest.mark.asyncio
async def test_request_shape() -> None:
    requests: list[httpx.Request] = []
    await _run(_make_client(requests))
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert request.url.path == PATH
    assert request.url.params["csvType"] == "full"
    assert request.url.params["useColumnShortNames"] == "true"


@pytest.mark.asyncio
async def test_region_code_is_none_and_blank_values_skipped() -> None:
    table = await _run(_make_client(), since_year=1950)
    africa = [r for r in table.rows if r.entity == "Africa"]
    assert africa
    assert all(r.code is None for r in africa)
    assert not any(r.entity == "Afghanistan" and r.year == 1990 for r in table.rows)
    assert any(r.entity == "Afghanistan" and r.code == "AFG" for r in table.rows)


@pytest.mark.asyncio
async def test_since_year_filters_rows() -> None:
    default = await _run(_make_client())
    assert default.rows
    assert all(r.year >= 2000 for r in default.rows)
    earlier = await _run(_make_client(), since_year=1950)
    assert any(r.year < 2000 for r in earlier.rows)
    assert len(earlier.rows) > len(default.rows)


@pytest.mark.asyncio
async def test_no_match_raises_non_retryable_without_request() -> None:
    requests: list[httpx.Request] = []
    with pytest.raises(ApplicationError) as exc_info:
        await _run(_make_client(requests), topic="Rust compiler release")
    assert exc_info.value.type == NO_MATCH_ERROR_TYPE
    assert exc_info.value.non_retryable is True
    assert requests == []


@pytest.mark.asyncio
async def test_not_found_is_non_retryable() -> None:
    with pytest.raises(ApplicationError) as exc_info:
        await _run(_make_client(status=404))
    assert exc_info.value.non_retryable is True


@pytest.mark.asyncio
async def test_server_error_raises_http_status_error() -> None:
    with pytest.raises(httpx.HTTPStatusError):
        await _run(_make_client(status=500))


@pytest.mark.asyncio
async def test_bad_header_is_non_retryable() -> None:
    with pytest.raises(ApplicationError) as exc_info:
        await _run(_make_client(body="Entity,Day,value\nFrance,1,2\n"))
    assert exc_info.value.non_retryable is True


@pytest.mark.asyncio
async def test_lowercase_real_header_is_accepted() -> None:
    body = "entity,code,year,life_expectancy_0\nFrance,FRA,2001,79.5\n"
    table = await _run(_make_client(body=body))
    assert table.rows == [OwidRow(entity="France", code="FRA", year=2001, value=79.5)]


@pytest.mark.parametrize(
    ("topic", "slug"),
    [
        ("CO2 emissions in Europe", "co-emissions-per-capita"),
        ("EV sales boom", "electric-car-sales-share"),
        ("every developer", None),
        ("Internet access in Africa", "share-of-individuals-using-the-internet"),
        ("Solar and climate", "co-emissions-per-capita"),
    ],
)
def test_match_dataset(topic: str, slug: str | None) -> None:
    match = match_dataset(topic)
    assert (match.slug if match else None) == slug


@pytest.mark.parametrize("dataset", CATALOG, ids=lambda d: d.slug)
def test_match_dataset_every_title(dataset: OwidDataset) -> None:
    assert match_dataset(dataset.title) == dataset


def test_input_validation() -> None:
    with pytest.raises(ValidationError):
        FetchOwidInput(topic="")
    with pytest.raises(ValidationError):
        FetchOwidInput(topic="x", since_year=1700)


def test_make_owid_client() -> None:
    client = make_owid_client()
    assert str(client.base_url) == OWID_BASE_URL
    assert client.follow_redirects is True
