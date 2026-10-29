"""Two registered sources feeding one candidate inbox end to end (start_local)."""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import anthropic
import httpx
import httpx2
import pytest
from temporalio import activity
from temporalio.client import Client, WorkflowExecutionStatus, WorkflowHandle
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.ingestion_logging import IngestionLogEvent
from ai_trend_pipeline.services.candidates import (
    CandidateService,
    DecideCandidateRequest,
    ListCandidatesRequest,
)
from ai_trend_pipeline.services.ingestion import source_ingestion_input
from ai_trend_pipeline.services.temporal_gateway import TemporalCandidateGateway
from ai_trend_pipeline.source_ingestion import (
    SOURCE_INGESTION_TASK_QUEUE,
    SourceIngestionResult,
    SourceIngestionState,
    SourceIngestionWorkflow,
    source_ingestion_workflow_id,
)
from ai_trend_pipeline.sources import SourceName, SourcePollInput, SourcePollOutput
from ai_trend_pipeline.sources.fake import DEFAULT_FAKE_ITEMS, FakeSourceActivities
from ai_trend_pipeline.sources.hn import HNSourceActivities
from ai_trend_pipeline.sources.registry import REGISTRY
from ai_trend_pipeline.story_candidate import (
    CANDIDATE_SOURCE_ATTRIBUTE,
    CANDIDATE_STATUS_ATTRIBUTE,
    CANDIDATE_TASK_QUEUE,
    CandidateStatus,
    StoryCandidate,
    StoryCandidateWorkflow,
    candidate_id_for,
)
from ai_trend_pipeline.story_selection import StorySelectionActivities
from ai_trend_pipeline.temporal_client import ensure_candidate_search_attributes

FIXTURES = Path(__file__).parent / "fixtures"
HN_SELECTION = "llm_select_story_candidates_ingestion.json"
FAKE_SELECTION = "llm_select_story_candidates_fake_source.json"
HN_TITLE = "Show HN: Normal story"
FAKE_TITLE = DEFAULT_FAKE_ITEMS[0].title or ""
FAKE_ANGLE = "What the pilot changes for daily commuters."
OBSERVED = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
IngestionHandle = WorkflowHandle[SourceIngestionWorkflow, SourceIngestionResult]
PollActivity = Callable[[SourcePollInput], Awaitable[SourcePollOutput]]


def hn_client() -> httpx.AsyncClient:
    data = cast(dict[str, object], json.loads((FIXTURES / "hn_source_poll.json").read_text()))
    topstories = cast(list[int], data["topstories"])
    items = cast(dict[str, object], data["items"])

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

    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://hacker-news.firebaseio.com/v0/",
    )


def anthropic_client() -> anthropic.AsyncAnthropic:
    hn_body = cast(dict[str, object], json.loads((FIXTURES / HN_SELECTION).read_text()))
    fake_body = cast(dict[str, object], json.loads((FIXTURES / FAKE_SELECTION).read_text()))

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.method != "POST" or not request.url.path.endswith("/v1/messages"):
            raise AssertionError(f"unexpected request: {request.method} {request.url}")
        text = request.content.decode("utf-8", "replace")
        body = fake_body if "fake-1001" in text else hn_body
        return httpx2.Response(200, json=body)

    return anthropic.AsyncAnthropic(
        api_key="test-key",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


class FailingFakePoll:
    @activity.defn(name="poll_fake_source")
    async def poll_fake_source(self, data: SourcePollInput) -> SourcePollOutput:
        raise ApplicationError("fake source down", type="FakeSourceDown", non_retryable=True)


@asynccontextmanager
async def two_source_env(
    settings: Settings,
    *,
    fake_poll: PollActivity,
) -> AsyncGenerator[tuple[WorkflowEnvironment, CandidateService], None]:
    # The SDK default for search_attributes contains an unresolved generic; typed keys are safe.
    env = await WorkflowEnvironment.start_local(  # pyright: ignore[reportUnknownMemberType]
        data_converter=pydantic_data_converter,
        search_attributes=[CANDIDATE_STATUS_ATTRIBUTE, CANDIDATE_SOURCE_ATTRIBUTE],
    )
    await ensure_candidate_search_attributes(env.client)
    hn = hn_client()
    hn_activities = HNSourceActivities(hn)
    selection = StorySelectionActivities(anthropic_client())
    service = CandidateService(TemporalCandidateGateway(env.client), settings)
    async with env:
        async with hn:
            async with (
                Worker(
                    env.client,
                    task_queue=SOURCE_INGESTION_TASK_QUEUE,
                    workflows=[SourceIngestionWorkflow],
                    activities=[
                        hn_activities.poll_hn_source,
                        fake_poll,
                        selection.select_story_candidates,
                    ],
                ),
                Worker(
                    env.client,
                    task_queue=CANDIDATE_TASK_QUEUE,
                    workflows=[StoryCandidateWorkflow],
                ),
            ):
                yield env, service


async def start_runs(
    env: WorkflowEnvironment, settings: Settings
) -> dict[SourceName, IngestionHandle]:
    handles: dict[SourceName, IngestionHandle] = {}
    client: Client = env.client
    for source in (SourceName.HN, SourceName.FAKE):
        workflow_id = source_ingestion_workflow_id(source)
        await client.start_workflow(
            SourceIngestionWorkflow.run,
            source_ingestion_input(settings, source),
            id=workflow_id,
            task_queue=SOURCE_INGESTION_TASK_QUEUE,
        )
        handles[source] = client.get_workflow_handle(workflow_id, result_type=SourceIngestionResult)
    return handles


async def stop_runs(handles: dict[SourceName, IngestionHandle]) -> None:
    for handle in handles.values():
        await handle.signal(SourceIngestionWorkflow.stop)
    for handle in handles.values():
        result = await handle.result()
        assert result.reason == "stopped"


async def wait_for_pending(service: CandidateService, count: int) -> None:
    for _ in range(600):
        page = await service.list_pending(ListCandidatesRequest())
        if len(page.candidates) >= count:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"expected {count} pending candidates")


async def wait_for_pending_count(service: CandidateService, count: int) -> None:
    for _ in range(600):
        page = await service.list_pending(ListCandidatesRequest())
        if len(page.candidates) == count:
            return
        await asyncio.sleep(0.02)
    page = await service.list_pending(ListCandidatesRequest())
    raise AssertionError(f"expected {count} pending candidates, saw {len(page.candidates)}")


async def wait_for_polled(handle: IngestionHandle) -> SourceIngestionState:
    for _ in range(600):
        state = await handle.query(SourceIngestionWorkflow.ingestion_state)
        if state.total_polls >= 1:
            return state
        await asyncio.sleep(0.02)
    raise AssertionError("source workflow never polled")


def record_field(record: logging.LogRecord, name: str) -> object:
    return record.__dict__.get(name)


@pytest.mark.asyncio
async def test_two_sources_feed_one_inbox() -> None:
    settings = Settings(source_enabled=frozenset({SourceName.HN, SourceName.FAKE}))
    async with two_source_env(settings, fake_poll=FakeSourceActivities().poll_fake_source) as (
        env,
        service,
    ):
        handles = await start_runs(env, settings)
        await wait_for_pending(service, 2)
        page = await service.list_pending(ListCandidatesRequest())
        by_source = {summary.source: summary for summary in page.candidates}
        assert len(page.candidates) == 2
        assert set(by_source) == {SourceName.HN, SourceName.FAKE}
        assert by_source[SourceName.HN].title == HN_TITLE
        assert by_source[SourceName.FAKE].title == FAKE_TITLE
        assert by_source[SourceName.FAKE].proposed_angle == FAKE_ANGLE
        assert by_source[SourceName.HN].candidate_id == candidate_id_for(SourceName.HN, "2001")
        assert by_source[SourceName.FAKE].candidate_id == candidate_id_for(
            SourceName.FAKE, "fake-1001"
        )
        assert REGISTRY[SourceName.FAKE].label == "Test source"
        state = await wait_for_polled(handles[SourceName.FAKE])
        assert state.source is SourceName.FAKE
        await stop_runs(handles)


@pytest.mark.asyncio
async def test_reviews_are_unchanged_for_both_sources() -> None:
    settings = Settings(source_enabled=frozenset({SourceName.HN, SourceName.FAKE}))
    async with two_source_env(settings, fake_poll=FakeSourceActivities().poll_fake_source) as (
        env,
        service,
    ):
        handles = await start_runs(env, settings)
        await wait_for_pending(service, 2)
        page = await service.list_pending(ListCandidatesRequest())
        by_source = {summary.source: summary for summary in page.candidates}
        hn_id = by_source[SourceName.HN].candidate_id
        fake_id = by_source[SourceName.FAKE].candidate_id

        hn_state = await service.show(hn_id)
        fake_state = await service.show(fake_id)
        assert hn_state.status is CandidateStatus.AWAITING_REVIEW
        assert fake_state.status is CandidateStatus.AWAITING_REVIEW

        approved = await service.decide(
            DecideCandidateRequest(
                candidate_id=fake_id,
                decision="approve",
                note="good story",
                decided_by="tester",
            )
        )
        rejected = await service.decide(
            DecideCandidateRequest(
                candidate_id=hn_id,
                decision="reject",
                note="not for us",
                decided_by="tester",
            )
        )
        assert approved.status == "approved"
        assert rejected.status == "rejected"
        await wait_for_pending_count(service, 0)

        real_gateway = TemporalCandidateGateway(env.client)
        assert (
            await real_gateway.handle(fake_id).review_state()
        ).status is CandidateStatus.APPROVED
        assert (await real_gateway.handle(hn_id).review_state()).status is CandidateStatus.REJECTED
        await stop_runs(handles)


@pytest.mark.asyncio
async def test_budget_is_per_source() -> None:
    settings = Settings(
        source_enabled=frozenset({SourceName.HN, SourceName.FAKE}),
        source_candidate_budget=1,
    )
    async with two_source_env(settings, fake_poll=FakeSourceActivities().poll_fake_source) as (
        env,
        service,
    ):
        handles = await start_runs(env, settings)
        await wait_for_pending(service, 2)
        page = await service.list_pending(ListCandidatesRequest())
        assert {summary.source for summary in page.candidates} == {
            SourceName.HN,
            SourceName.FAKE,
        }
        for handle in handles.values():
            state = await handle.query(SourceIngestionWorkflow.ingestion_state)
            assert state.candidate_budget == 1
            assert state.candidates_started == 1
            assert state.budget_remaining == 0
        await stop_runs(handles)


@pytest.mark.asyncio
async def test_one_failing_source_does_not_block_the_other(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="temporalio.workflow")
    settings = Settings(source_enabled=frozenset({SourceName.HN, SourceName.FAKE}))
    async with two_source_env(settings, fake_poll=FailingFakePoll().poll_fake_source) as (
        env,
        service,
    ):
        handles = await start_runs(env, settings)
        await wait_for_pending(service, 1)
        fake_state = await wait_for_polled(handles[SourceName.FAKE])
        assert fake_state.total_polls >= 1
        fake_description = await handles[SourceName.FAKE].describe()
        assert fake_description.status is WorkflowExecutionStatus.RUNNING

        page = await service.list_pending(ListCandidatesRequest())
        assert [summary.source for summary in page.candidates] == [SourceName.HN]

        failures = [
            record
            for record in caplog.records
            if record_field(record, "event") == IngestionLogEvent.POLL_FAILED.value
            and record_field(record, "source") == "fake"
        ]
        assert failures
        assert record_field(failures[0], "error_type") == "ActivityError"
        assert record_field(failures[0], "poll") == 1
        for record in failures:
            assert "fake source down" not in record.getMessage()
            assert all("fake source down" not in str(value) for value in record.__dict__.values())
        await stop_runs(handles)


@pytest.mark.asyncio
async def test_duplicate_candidate_start_does_not_duplicate_inbox_rows() -> None:
    settings = Settings(source_enabled=frozenset({SourceName.HN, SourceName.FAKE}))
    async with two_source_env(settings, fake_poll=FakeSourceActivities().poll_fake_source) as (
        env,
        service,
    ):
        existing = StoryCandidate.model_validate(
            {
                "source": "hn",
                "external_id": "2001",
                "title": HN_TITLE,
                "url": "https://example.com/normal",
                "observed_at": OBSERVED,
                "summary": "A pre-existing summary",
                "proposed_angle": "A pre-existing angle",
                "selection_reason": "A pre-existing reason",
            }
        )
        started = await service.start(existing, review_timeout_seconds=3600)
        assert started.candidate_id == candidate_id_for(SourceName.HN, "2001")

        handles = await start_runs(env, settings)
        await wait_for_pending(service, 2)
        page = await service.list_pending(ListCandidatesRequest())
        hn_rows = [summary for summary in page.candidates if summary.source is SourceName.HN]
        assert len(hn_rows) == 1
        assert hn_rows[0].candidate_id == started.candidate_id
        assert {summary.source for summary in page.candidates} == {
            SourceName.HN,
            SourceName.FAKE,
        }
        await stop_runs(handles)


@pytest.mark.asyncio
async def test_stop_leaves_pending_reviews() -> None:
    settings = Settings(source_enabled=frozenset({SourceName.HN, SourceName.FAKE}))
    async with two_source_env(settings, fake_poll=FakeSourceActivities().poll_fake_source) as (
        env,
        service,
    ):
        handles = await start_runs(env, settings)
        await wait_for_pending(service, 2)
        page = await service.list_pending(ListCandidatesRequest())
        by_source = {summary.source: summary for summary in page.candidates}
        await stop_runs(handles)

        for source in (SourceName.HN, SourceName.FAKE):
            shown = await service.show(by_source[source].candidate_id)
            assert shown.status is CandidateStatus.AWAITING_REVIEW

        fake_id = by_source[SourceName.FAKE].candidate_id
        approved = await service.decide(
            DecideCandidateRequest(candidate_id=fake_id, decision="approve")
        )
        assert approved.status == "approved"
        await wait_for_pending_count(service, 1)
