"""End-to-end ingestion test: HN fixture -> selector -> pending candidate (start_local)."""

import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import anthropic
import httpx
import httpx2
import pytest
from temporalio.client import Client, WorkflowHandle
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.hn import HN_BASE_URL
from ai_trend_pipeline.services.candidates import CandidateService, ListCandidatesRequest
from ai_trend_pipeline.services.ingestion import source_ingestion_input
from ai_trend_pipeline.services.temporal_gateway import (
    TemporalCandidateGateway,
    TemporalChartRunGateway,
    pending_candidates_query,
)
from ai_trend_pipeline.source_ingestion import (
    SOURCE_INGESTION_TASK_QUEUE,
    SourceIngestionResult,
    SourceIngestionWorkflow,
    source_ingestion_workflow_id,
)
from ai_trend_pipeline.sources import SourceName
from ai_trend_pipeline.sources.hn import HNSourceActivities
from ai_trend_pipeline.story_candidate import (
    CANDIDATE_SOURCE_ATTRIBUTE,
    CANDIDATE_STATUS_ATTRIBUTE,
    CANDIDATE_TASK_QUEUE,
    CandidateStatus,
    StoryCandidateWorkflow,
    candidate_id_for,
)
from ai_trend_pipeline.story_selection import StorySelectionActivities
from ai_trend_pipeline.story_status import STORY_UI_RETENTION
from ai_trend_pipeline.temporal_client import ensure_candidate_search_attributes

FIXTURES = Path(__file__).parent / "fixtures"
HN_FIXTURE = "hn_source_poll.json"
SELECTION_FIXTURE = "llm_select_story_candidates_ingestion.json"
EXPECTED_TITLE = "Show HN: Normal story"
EXPECTED_URL = "https://example.com/normal"
EXPECTED_ANGLE = "What the project does and why builders are trying it."


def _hn_fixture() -> tuple[list[int], dict[str, object]]:
    data = cast(dict[str, object], json.loads((FIXTURES / HN_FIXTURE).read_text()))
    return cast(list[int], data["topstories"]), cast(dict[str, object], data["items"])


def _hn_client() -> httpx.AsyncClient:
    topstories, items = _hn_fixture()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v0/topstories.json":
            return httpx.Response(200, json=topstories)
        prefix, suffix = "/v0/item/", ".json"
        if path.startswith(prefix) and path.endswith(suffix):
            key = path[len(prefix) : -len(suffix)]
            if key in items:
                return httpx.Response(200, content=json.dumps(items[key]))
        raise AssertionError(f"unexpected URL: {request.url}")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=HN_BASE_URL)


def _anthropic_client() -> anthropic.AsyncAnthropic:
    body = cast(dict[str, object], json.loads((FIXTURES / SELECTION_FIXTURE).read_text()))

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.method != "POST" or not request.url.path.endswith("/v1/messages"):
            raise AssertionError(f"unexpected request: {request.method} {request.url}")
        return httpx2.Response(200, json=body)

    return anthropic.AsyncAnthropic(
        api_key="test-key",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


@asynccontextmanager
async def ingestion_run() -> AsyncGenerator[
    tuple[
        WorkflowEnvironment,
        WorkflowHandle[SourceIngestionWorkflow, SourceIngestionResult],
        CandidateService,
    ],
    None,
]:
    # The SDK default for search_attributes contains an unresolved generic; typed keys are safe.
    env = await WorkflowEnvironment.start_local(  # pyright: ignore[reportUnknownMemberType]
        data_converter=pydantic_data_converter,
        search_attributes=[CANDIDATE_STATUS_ATTRIBUTE, CANDIDATE_SOURCE_ATTRIBUTE],
    )
    await ensure_candidate_search_attributes(env.client)
    settings = Settings()
    hn_client = _hn_client()
    selection_client = _anthropic_client()
    source_activities = HNSourceActivities(hn_client)
    selection_activities = StorySelectionActivities(selection_client)
    service = CandidateService(TemporalCandidateGateway(env.client), settings)
    workflow_id = source_ingestion_workflow_id(SourceName.HN)
    async with env:
        async with hn_client:
            async with (
                Worker(
                    env.client,
                    task_queue=SOURCE_INGESTION_TASK_QUEUE,
                    workflows=[SourceIngestionWorkflow],
                    activities=[
                        source_activities.poll_hn_source,
                        selection_activities.select_story_candidates,
                    ],
                ),
                Worker(
                    env.client,
                    task_queue=CANDIDATE_TASK_QUEUE,
                    workflows=[StoryCandidateWorkflow],
                ),
            ):
                client: Client = env.client
                await client.start_workflow(
                    SourceIngestionWorkflow.run,
                    source_ingestion_input(settings, SourceName.HN),
                    id=workflow_id,
                    task_queue=SOURCE_INGESTION_TASK_QUEUE,
                )
                handle: WorkflowHandle[SourceIngestionWorkflow, SourceIngestionResult] = (
                    client.get_workflow_handle(workflow_id, result_type=SourceIngestionResult)
                )
                yield env, handle, service


async def wait_for_pending(service: CandidateService, count: int) -> None:
    for _ in range(500):
        page = await service.list_pending(ListCandidatesRequest(), now=datetime.now(UTC))
        if len(page.candidates) >= count:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"expected {count} pending candidates")


async def stop_run(
    handle: WorkflowHandle[SourceIngestionWorkflow, SourceIngestionResult],
) -> SourceIngestionResult:
    await handle.signal(SourceIngestionWorkflow.stop)
    return await handle.result()


@pytest.mark.asyncio
async def test_hn_fixture_becomes_a_pending_candidate_through_candidate_service() -> None:
    async with ingestion_run() as (_env, handle, service):
        await wait_for_pending(service, 1)
        page = await service.list_pending(ListCandidatesRequest(), now=datetime.now(UTC))
        assert len(page.candidates) == 1
        summary = page.candidates[0]
        assert summary.source is SourceName.HN
        assert summary.title == EXPECTED_TITLE
        assert summary.proposed_angle == EXPECTED_ANGLE
        assert summary.candidate_id == candidate_id_for(SourceName.HN, "2001")
        state = await service.show(summary.candidate_id)
        assert state.status is CandidateStatus.AWAITING_REVIEW
        assert state.candidate.external_id == "2001"
        assert str(state.candidate.url) == EXPECTED_URL
        result = await stop_run(handle)
    assert result.reason == "stopped"


@pytest.mark.asyncio
async def test_ingestion_workflow_is_not_a_candidate_or_chart_run() -> None:
    async with ingestion_run() as (env, handle, service):
        await wait_for_pending(service, 1)
        page = await service.list_pending(ListCandidatesRequest(), now=datetime.now(UTC))
        assert len(page.candidates) == 1
        executions = [
            execution
            async for execution in env.client.list_workflows(
                "WorkflowType = 'SourceIngestionWorkflow'"
            )
        ]
        assert [execution.id for execution in executions] == [
            source_ingestion_workflow_id(SourceName.HN)
        ]
        attributes = executions[0].typed_search_attributes
        assert attributes.get(CANDIDATE_STATUS_ATTRIBUTE) is None
        assert attributes.get(CANDIDATE_SOURCE_ATTRIBUTE) is None
        chart_runs = await TemporalChartRunGateway(env.client).count_started_since(
            "someone", datetime(2020, 1, 1, tzinfo=UTC)
        )
        assert chart_runs == 0
        query = pending_candidates_query(datetime.now(UTC) - STORY_UI_RETENTION)
        assert "StoryCandidateWorkflow" in query
        assert "SourceIngestionWorkflow" not in query
        result = await stop_run(handle)
    assert result.reason == "stopped"


@pytest.mark.asyncio
async def test_end_to_end_run_stops_cleanly() -> None:
    async with ingestion_run() as (_env, handle, service):
        await wait_for_pending(service, 1)
        before = await service.list_pending(ListCandidatesRequest(), now=datetime.now(UTC))
        result = await stop_run(handle)
        await asyncio.sleep(0.1)
        after = await service.list_pending(ListCandidatesRequest(), now=datetime.now(UTC))
        assert len(after.candidates) == len(before.candidates) == 1
    assert result.reason == "stopped"
