"""Tests for the LLM chart-spec activity, using recorded Messages API fixtures."""

import json
from pathlib import Path
from typing import cast

import anthropic
import httpx2
import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from ai_trend_pipeline.chart_spec import (
    INVALID_DRAFT_ERROR_TYPE,
    MAX_TOKENS,
    TOOL_NAME,
    ChartSpec,
    ChartSpecActivities,
    WriteChartSpecInput,
)
from ai_trend_pipeline.owid import OwidRow, OwidTable
from ai_trend_pipeline.topic import DEFAULT_MODEL

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = "llm_chart_spec_response.json"
TOPIC = "Life expectancy trends"
ENTITIES = ["Africa", "Afghanistan", "France", "Japan"]


def _fixture(name: str) -> dict[str, object]:
    return cast(dict[str, object], json.loads((FIXTURES / name).read_text()))


def _draft_input(fixture: dict[str, object]) -> dict[str, object]:
    content = cast(list[dict[str, object]], fixture["content"])
    return cast(dict[str, object], content[1]["input"])


def make_table(rows: bool = True) -> OwidTable:
    data: list[OwidRow] = []
    if rows:
        for i, entity in enumerate(ENTITIES):
            for year in (2000, 2001, 2002):
                data.append(
                    OwidRow(entity=entity, code=None, year=year, value=50.0 + i * 10 + year - 2000)
                )
    return OwidTable(
        slug="life-expectancy",
        title="Life expectancy at birth",
        value_column="life_expectancy_0",
        rows=data,
    )


def make_client(
    fixture: str = FIXTURE,
    requests: list[dict[str, object]] | None = None,
    status: int = 200,
    body: dict[str, object] | None = None,
) -> anthropic.AsyncAnthropic:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.method != "POST" or request.url.path != "/v1/messages":
            raise AssertionError(f"unexpected request: {request.method} {request.url}")
        if requests is not None:
            requests.append(cast(dict[str, object], json.loads(request.content)))
        if status != 200:
            return httpx2.Response(
                status,
                json={"type": "error", "error": {"type": "api_error", "message": "boom"}},
            )
        return httpx2.Response(200, json=body if body is not None else _fixture(fixture))

    return anthropic.AsyncAnthropic(
        api_key="test-key",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


async def _run(
    client: anthropic.AsyncAnthropic, model: str | None = None, table: OwidTable | None = None
) -> ChartSpec:
    activities = (
        ChartSpecActivities(client) if model is None else ChartSpecActivities(client, model=model)
    )
    return await ActivityEnvironment().run(
        activities.write_chart_spec,
        WriteChartSpecInput(topic=TOPIC, table=table or make_table()),
    )


def _modified(**changes: object) -> dict[str, object]:
    body = _fixture(FIXTURE)
    _draft_input(body).update(changes)
    return body


@pytest.mark.asyncio
async def test_write_chart_spec_returns_spec_and_caption() -> None:
    result = await _run(make_client())
    expected = _draft_input(_fixture(FIXTURE))
    assert isinstance(result, ChartSpec)
    assert result.caption == expected["caption"]
    assert result.entities == expected["entities"]


@pytest.mark.asyncio
async def test_data_is_injected_from_table() -> None:
    result = await _run(make_client())
    spec = result.vega_lite_spec
    chosen = {"France", "Japan", "Africa"}
    expected = [
        {"entity": r.entity, "year": r.year, "value": r.value}
        for r in make_table().rows
        if r.entity in chosen
    ]
    assert spec["data"] == {"values": expected}
    assert len(expected) == 9
    assert "Afghanistan" not in json.dumps(spec)
    assert "url" not in json.dumps(spec["data"])
    draft = cast(dict[str, object], _draft_input(_fixture(FIXTURE))["vega_lite_spec"])
    assert spec["mark"] == draft["mark"]
    assert spec["encoding"] == draft["encoding"]


@pytest.mark.asyncio
async def test_request_shape() -> None:
    requests: list[dict[str, object]] = []
    await _run(make_client(requests=requests))
    assert len(requests) == 1
    body = requests[0]
    assert body["model"] == DEFAULT_MODEL
    assert body["max_tokens"] == MAX_TOKENS
    tools = cast(list[dict[str, object]], body["tools"])
    assert [t["name"] for t in tools] == [TOOL_NAME] == ["write_chart"]
    assert body["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    messages = cast(list[dict[str, object]], body["messages"])
    assert len(messages) == 1
    text = json.dumps(messages[0]["content"])
    for needle in (TOPIC, "Life expectancy at birth", "life_expectancy_0", *ENTITIES):
        assert needle in text


@pytest.mark.asyncio
async def test_custom_model_is_sent() -> None:
    requests: list[dict[str, object]] = []
    await _run(make_client(requests=requests), model="other-model")
    assert requests[0]["model"] == "other-model"


@pytest.mark.asyncio
async def test_unknown_entity_is_non_retryable() -> None:
    with pytest.raises(ApplicationError) as exc:
        await _run(make_client(body=_modified(entities=["Atlantis"])))
    assert exc.value.type == INVALID_DRAFT_ERROR_TYPE
    assert exc.value.non_retryable is True


@pytest.mark.asyncio
async def test_invalid_draft_is_non_retryable() -> None:
    with pytest.raises(ApplicationError) as exc:
        await _run(make_client(body=_modified(caption="x" * 281)))
    assert exc.value.type == INVALID_DRAFT_ERROR_TYPE
    assert exc.value.non_retryable is True


@pytest.mark.asyncio
async def test_no_tool_use_raises_non_retryable() -> None:
    with pytest.raises(ApplicationError) as exc:
        await _run(make_client("llm_pick_topic_no_tool.json"))
    assert exc.value.non_retryable is True


@pytest.mark.asyncio
async def test_empty_table_is_non_retryable_without_request() -> None:
    requests: list[dict[str, object]] = []
    with pytest.raises(ApplicationError) as exc:
        await _run(make_client(requests=requests), table=make_table(rows=False))
    assert exc.value.non_retryable is True
    assert requests == []


@pytest.mark.asyncio
async def test_api_error_propagates() -> None:
    with pytest.raises(anthropic.APIStatusError):
        await _run(make_client(status=500))


@pytest.mark.asyncio
async def test_feedback_is_appended_to_request() -> None:
    requests: list[dict[str, object]] = []
    activities = ChartSpecActivities(make_client(requests=requests))
    await ActivityEnvironment().run(
        activities.write_chart_spec,
        WriteChartSpecInput(topic=TOPIC, table=make_table(), feedback=["too many lines"]),
    )
    messages = cast(list[dict[str, object]], requests[0]["messages"])
    content = cast(str, messages[0]["content"])
    assert content.endswith("\nA previous draft was rejected for these reasons:\n- too many lines")
