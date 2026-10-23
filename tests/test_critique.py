"""Tests for the LLM chart critique activity, using recorded Messages API fixtures."""

from typing import cast

import anthropic
import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from ai_trend_pipeline.chart_spec import ChartSpec, ChartSpecActivities, WriteChartSpecInput
from ai_trend_pipeline.critique import (
    INVALID_CRITIQUE_ERROR_TYPE,
    MAX_TOKENS,
    SYSTEM_PROMPT,
    TOOL_NAME,
    ChartCritique,
    CritiqueActivities,
    CritiqueChartInput,
)
from ai_trend_pipeline.topic import DEFAULT_MODEL

# Private test helper shared across test modules; move to tests/helpers.py later.
from tests.test_chart_spec import (
    TOPIC,
    _fixture,  # pyright: ignore[reportPrivateUsage]
    make_client,
    make_table,
)

PASS = "llm_critique_pass.json"
FAIL = "llm_critique_fail.json"


async def make_chart() -> ChartSpec:
    activities = ChartSpecActivities(make_client())
    return await ActivityEnvironment().run(
        activities.write_chart_spec, WriteChartSpecInput(topic=TOPIC, table=make_table())
    )


async def _run(client: anthropic.AsyncAnthropic, model: str | None = None) -> ChartCritique:
    activities = CritiqueActivities(client) if model is None else CritiqueActivities(client, model)
    data = CritiqueChartInput(topic=TOPIC, table=make_table(), chart=await make_chart())
    return await ActivityEnvironment().run(activities.critique_chart, data)


def _body(passed: bool, reasons: list[str]) -> dict[str, object]:
    body = _fixture(PASS)
    content = cast(list[dict[str, object]], body["content"])
    content[1]["input"] = {"passed": passed, "reasons": reasons}
    return body


def _user_text(request: dict[str, object]) -> str:
    messages = cast(list[dict[str, object]], request["messages"])
    assert len(messages) == 1
    return cast(str, messages[0]["content"])


@pytest.mark.asyncio
async def test_critique_pass_fixture() -> None:
    assert await _run(make_client(PASS)) == ChartCritique(passed=True, reasons=[])


@pytest.mark.asyncio
async def test_critique_fail_fixture() -> None:
    result = await _run(make_client(FAIL))
    assert result.passed is False
    assert result.reasons == [
        "Caption says Japan's life expectancy fell, but the data shows it rose."
    ]


@pytest.mark.asyncio
async def test_request_shape() -> None:
    requests: list[dict[str, object]] = []
    await _run(make_client(PASS, requests=requests))
    assert len(requests) == 1
    body = requests[0]
    assert body["model"] == DEFAULT_MODEL
    assert body["max_tokens"] == MAX_TOKENS
    tools = cast(list[dict[str, object]], body["tools"])
    assert [t["name"] for t in tools] == [TOOL_NAME] == ["critique_chart"]
    assert body["tool_choice"] == {"type": "auto"}
    text = _user_text(body)
    chart = await make_chart()
    for needle in (TOPIC, chart.caption, "life_expectancy_0", *chart.entities):
        assert needle in text
    assert '"values"' not in text
    assert "Afghanistan" not in text


@pytest.mark.asyncio
async def test_summary_lines() -> None:
    requests: list[dict[str, object]] = []
    await _run(make_client(PASS, requests=requests))
    text = _user_text(requests[0])
    # France is index 2: values 70.0, 71.0, 72.0 for 2000..2002
    line = next(ln for ln in text.splitlines() if ln.startswith("France"))
    for needle in ("2000", "2002", "70.0", "72.0", "3"):
        assert needle in line
    assert "min 70.0" in line
    assert "max 72.0" in line


@pytest.mark.asyncio
async def test_custom_model_is_sent() -> None:
    requests: list[dict[str, object]] = []
    await _run(make_client(PASS, requests=requests), model="other-model")
    assert requests[0]["model"] == "other-model"


@pytest.mark.asyncio
async def test_fail_without_reasons_is_non_retryable() -> None:
    with pytest.raises(ApplicationError) as exc:
        await _run(make_client(body=_body(False, [])))
    assert exc.value.type == INVALID_CRITIQUE_ERROR_TYPE
    assert exc.value.non_retryable is True


@pytest.mark.asyncio
async def test_no_tool_use_raises_non_retryable() -> None:
    with pytest.raises(ApplicationError) as exc:
        await _run(make_client("llm_pick_topic_no_tool.json"))
    assert exc.value.non_retryable is True


@pytest.mark.asyncio
async def test_api_error_propagates() -> None:
    with pytest.raises(anthropic.APIStatusError):
        await _run(make_client(status=500))


@pytest.mark.asyncio
async def test_system_prompt_mentions_tool() -> None:
    requests: list[dict[str, object]] = []
    await _run(make_client(PASS, requests=requests))
    system = str(requests[0]["system"])
    for text in (system, SYSTEM_PROMPT):
        assert TOOL_NAME in text
        assert "calling" in text
