"""Tests for the one-regenerate chart loop, run inside Temporal's time-skipping environment."""

import json
import uuid
from collections.abc import Callable, Sequence
from typing import cast

import anthropic
import httpx2
import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from ai_trend_pipeline.chart_loop import CritiquedChart, DraftChartWorkflow
from ai_trend_pipeline.chart_spec import (
    INVALID_DRAFT_ERROR_TYPE,
    ChartSpec,
    ChartSpecActivities,
    WriteChartSpecInput,
)
from ai_trend_pipeline.critique import ChartCritique, CritiqueActivities, CritiqueChartInput
from tests.test_chart_spec import TOPIC, _fixture, make_client, make_table  # pyright: ignore[reportPrivateUsage]

PASS = ChartCritique(passed=True, reasons=[])
FAIL = ChartCritique(passed=False, reasons=["caption contradicts data"])


def _chart(caption: str) -> ChartSpec:
    return ChartSpec(vega_lite_spec={"mark": "line"}, caption=caption, entities=["France"])


class FakeActivities:
    def __init__(self, writes: list[ChartSpec | ApplicationError], critiques: list[ChartCritique]):
        self._writes = list(writes)
        self._critiques = list(critiques)
        self.write_inputs: list[WriteChartSpecInput] = []
        self.critique_inputs: list[CritiqueChartInput] = []

    @activity.defn(name="write_chart_spec")
    async def write_chart_spec(self, data: WriteChartSpecInput) -> ChartSpec:
        self.write_inputs.append(data)
        result = self._writes[len(self.write_inputs) - 1]
        if isinstance(result, ApplicationError):
            raise result
        return result

    @activity.defn(name="critique_chart")
    async def critique_chart(self, data: CritiqueChartInput) -> ChartCritique:
        self.critique_inputs.append(data)
        return self._critiques[len(self.critique_inputs) - 1]


def _invalid(message: str = "bad draft") -> ApplicationError:
    return ApplicationError(message, type=INVALID_DRAFT_ERROR_TYPE, non_retryable=True)


async def _execute(activities: Sequence[Callable[..., object]]) -> CritiquedChart:
    task_queue = f"test-{uuid.uuid4()}"
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[DraftChartWorkflow],
            activities=activities,
        ):
            return await env.client.execute_workflow(
                DraftChartWorkflow.run,
                WriteChartSpecInput(topic=TOPIC, table=make_table()),
                id=f"test-{uuid.uuid4()}",
                task_queue=task_queue,
            )


async def _run_fake(fake: FakeActivities) -> CritiquedChart:
    return await _execute([fake.write_chart_spec, fake.critique_chart])


@pytest.mark.asyncio
async def test_pass_first_time_no_regenerate() -> None:
    fake = FakeActivities([_chart("one")], [PASS])
    result = await _run_fake(fake)
    assert len(fake.write_inputs) == 1
    assert len(fake.critique_inputs) == 1
    assert result.attempts == 1
    assert result.chart.caption == "one"
    assert fake.write_inputs[0].feedback == []


@pytest.mark.asyncio
async def test_bad_draft_triggers_exactly_one_regenerate() -> None:
    fake = FakeActivities([_chart("one"), _chart("two")], [FAIL, PASS])
    result = await _run_fake(fake)
    assert len(fake.write_inputs) == 2
    assert len(fake.critique_inputs) == 2
    assert fake.write_inputs[1].feedback == FAIL.reasons
    assert fake.critique_inputs[1].chart.caption == "two"
    assert result.attempts == 2
    assert result.chart.caption == "two"
    assert result.critique.passed is True


@pytest.mark.asyncio
async def test_two_failures_return_failed_critique_without_third_draft() -> None:
    fake = FakeActivities([_chart("one"), _chart("two")], [FAIL, FAIL])
    result = await _run_fake(fake)
    assert len(fake.write_inputs) == 2
    assert len(fake.critique_inputs) == 2
    assert result.attempts == 2
    assert result.critique.passed is False
    assert result.chart.caption == "two"


@pytest.mark.asyncio
async def test_invalid_draft_triggers_one_regenerate() -> None:
    fake = FakeActivities([_invalid("bad draft"), _chart("two")], [PASS])
    result = await _run_fake(fake)
    assert len(fake.write_inputs) == 2
    assert len(fake.critique_inputs) == 1
    assert any("bad draft" in f for f in fake.write_inputs[1].feedback)
    assert result.attempts == 2


@pytest.mark.asyncio
async def test_invalid_draft_twice_fails_workflow() -> None:
    fake = FakeActivities([_invalid(), _invalid()], [])
    with pytest.raises(WorkflowFailureError):
        await _run_fake(fake)
    assert len(fake.write_inputs) == 2
    assert fake.critique_inputs == []


@pytest.mark.asyncio
async def test_fixture_bad_critique_triggers_one_retry_end_to_end() -> None:
    chart_requests: list[dict[str, object]] = []
    critique_requests: list[dict[str, object]] = []
    served = ["llm_critique_fail.json", "llm_critique_pass.json"]

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.method != "POST" or request.url.path != "/v1/messages":
            raise AssertionError(f"unexpected request: {request.method} {request.url}")
        critique_requests.append(cast(dict[str, object], json.loads(request.content)))
        return httpx2.Response(200, json=_fixture(served[len(critique_requests) - 1]))

    critique_client = anthropic.AsyncAnthropic(
        api_key="test-key",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    chart_acts = ChartSpecActivities(make_client(requests=chart_requests))
    critique_acts = CritiqueActivities(critique_client)
    result = await _execute([chart_acts.write_chart_spec, critique_acts.critique_chart])
    assert len(chart_requests) == 2
    assert len(critique_requests) == 2
    reason = "Caption says Japan's life expectancy fell, but the data shows it rose."
    messages = cast(list[dict[str, object]], chart_requests[1]["messages"])
    assert reason in cast(str, messages[0]["content"])
    assert result.attempts == 2
    assert result.critique.passed is True
