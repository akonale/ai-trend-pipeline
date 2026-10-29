"""Structured ingestion log events, their guards, and the records real runs emit."""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import anthropic
import httpx
import httpx2
import pytest
from pydantic import SecretStr
from temporalio import activity
from temporalio.client import Client, WorkflowHandle
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.service import RPCError, RPCStatusCode
from temporalio.testing import ActivityEnvironment, WorkflowEnvironment
from temporalio.worker import Worker

from ai_trend_pipeline.ingestion_logging import (
    FORBIDDEN_LOG_FIELDS,
    MAX_LOG_VALUE_LENGTH,
    IngestionLogEvent,
    StructuredLogger,
    log_event,
)
from ai_trend_pipeline.source_ingestion import (
    SOURCE_INGESTION_TASK_QUEUE,
    SourceIngestionInput,
    SourceIngestionResult,
    SourceIngestionState,
    SourceIngestionWorkflow,
    source_ingestion_workflow_id,
)
from ai_trend_pipeline.sources import (
    SourceEvent,
    SourceName,
    SourcePollInput,
    SourcePollOutput,
)
from ai_trend_pipeline.sources.fake import DEFAULT_FAKE_ITEMS, FakeSourceActivities
from ai_trend_pipeline.sources.hn import HNSourceActivities
from ai_trend_pipeline.story_candidate import (
    CANDIDATE_TASK_QUEUE,
    CandidateStatus,
    StoryCandidate,
    StoryCandidateInput,
    StoryCandidateWorkflow,
    candidate_id_for,
)
from ai_trend_pipeline.story_selection import (
    SelectedStory,
    SelectStoryCandidatesInput,
    SelectStoryCandidatesOutput,
    StorySelectionActivities,
)
from ai_trend_pipeline.temporal_client import ensure_candidate_search_attributes

FIXTURES = Path(__file__).parent / "fixtures"
HN_FIXTURE = "hn_source_poll.json"
SENTINEL_KEY = "sk-ant-sentinel-do-not-log-me"
OBSERVED = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
PollActivity = Callable[[SourcePollInput], Awaitable[SourcePollOutput]]
SelectActivity = Callable[[SelectStoryCandidatesInput], Awaitable[SelectStoryCandidatesOutput]]
StateHandle = WorkflowHandle[SourceIngestionWorkflow, SourceIngestionResult]


def record_field(record: logging.LogRecord, name: str) -> object:
    return record.__dict__.get(name)


def fake_title(index: int) -> str:
    return DEFAULT_FAKE_ITEMS[index].title or ""


def records_for(
    caplog: pytest.LogCaptureFixture, event: IngestionLogEvent
) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record_field(record, "event") == event.value]


def log_text(record: logging.LogRecord) -> str:
    parts = [record.getMessage()]
    parts.extend(str(value) for value in record.__dict__.values())
    return " ".join(parts)


def _logger() -> StructuredLogger:
    return cast(StructuredLogger, logging.getLogger("tests.ingestion_logging"))


def test_event_names_are_namespaced_and_unique() -> None:
    values = [event.value for event in IngestionLogEvent]
    assert all(value.startswith("candidate_ingestion.") for value in values)
    assert len(values) == len(set(values))
    assert len(values) == 12


def test_log_event_sets_message_event_source_and_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="tests.ingestion_logging")
    log_event(
        _logger(),
        IngestionLogEvent.POLL,
        SourceName.HN,
        poll=2,
        cursor="2001",
        batch_size=3,
    )
    record = caplog.records[-1]
    assert record.getMessage() == "candidate_ingestion.poll"
    assert record.levelno == logging.INFO
    assert record_field(record, "event") == "candidate_ingestion.poll"
    assert record_field(record, "source") == "hn"
    assert record_field(record, "poll") == 2
    assert record_field(record, "cursor") == "2001"
    assert record_field(record, "batch_size") == 3


def test_log_event_rejects_forbidden_source_text_and_secret_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="tests.ingestion_logging")
    logger = _logger()
    for name in FORBIDDEN_LOG_FIELDS:
        with pytest.raises(ValueError):
            log_event(logger, IngestionLogEvent.POLL, SourceName.HN, **{name: "value"})
    for name in ("Title", "API_KEY", "Selection_Reason"):
        with pytest.raises(ValueError):
            log_event(logger, IngestionLogEvent.POLL, SourceName.HN, **{name: "value"})


def test_log_event_truncates_long_values_and_rejects_non_primitives(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="tests.ingestion_logging")
    log_event(_logger(), IngestionLogEvent.POLL, SourceName.HN, cursor="x" * 300)
    record = caplog.records[-1]
    assert record_field(record, "cursor") == "x" * MAX_LOG_VALUE_LENGTH
    assert MAX_LOG_VALUE_LENGTH == 256

    logger = _logger()
    for value in ({"a": 1}, [1], SecretStr("secret")):
        with pytest.raises(ValueError):
            log_event(
                logger,
                IngestionLogEvent.POLL,
                SourceName.HN,
                cursor=cast(str | int | float | bool | None, value),
            )


def event(external_id: str) -> SourceEvent:
    return SourceEvent(
        source=SourceName.HN,
        external_id=external_id,
        title=f"Story {external_id}",
        observed_at=OBSERVED,
    )


def poll_output(*events: SourceEvent) -> SourcePollOutput:
    return SourcePollOutput(source=SourceName.HN, events=list(events))


def selected(external_id: str) -> SelectedStory:
    return SelectedStory(
        external_id=external_id,
        summary=f"Summary for {external_id}",
        proposed_angle=f"Angle for {external_id}",
        selection_reason=f"Reason for {external_id}",
    )


def make_input(**overrides: object) -> SourceIngestionInput:
    payload: dict[str, object] = {
        "source": SourceName.HN,
        "poll_interval_seconds": 30,
        "max_polls_per_run": 50,
    }
    payload.update(overrides)
    return SourceIngestionInput.model_validate(payload)


class GatedPoller:
    def __init__(self, results: Sequence[SourcePollOutput | BaseException]) -> None:
        self._results = list(results)
        self._gates: asyncio.Queue[asyncio.Event] = asyncio.Queue()
        self.calls = 0

    @activity.defn(name="poll_hn_source")
    async def poll_hn_source(self, data: SourcePollInput) -> SourcePollOutput:
        index = self.calls
        self.calls += 1
        gate = asyncio.Event()
        await self._gates.put(gate)
        await gate.wait()
        if not self._results:
            return poll_output()
        result = self._results[index] if index < len(self._results) else self._results[-1]
        if isinstance(result, BaseException):
            raise result
        return result

    async def wait_for_call(self, count: int) -> None:
        for _ in range(4000):
            if self.calls >= count:
                return
            await asyncio.sleep(0.005)
        raise AssertionError(f"poll activity was not called {count} times (saw {self.calls})")

    async def release(self) -> None:
        gate = await asyncio.wait_for(self._gates.get(), timeout=10)
        gate.set()


class ScriptedSelector:
    def __init__(self, selections: Sequence[SelectedStory] = ()) -> None:
        self._selections = list(selections)

    @activity.defn(name="select_story_candidates")
    async def select_story_candidates(
        self, data: SelectStoryCandidatesInput
    ) -> SelectStoryCandidatesOutput:
        return SelectStoryCandidatesOutput(selections=list(self._selections))


def anthropic_client(fixture_name: str) -> anthropic.AsyncAnthropic:
    body = cast(dict[str, object], json.loads((FIXTURES / fixture_name).read_text()))

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.method != "POST" or not request.url.path.endswith("/v1/messages"):
            raise AssertionError(f"unexpected request: {request.method} {request.url}")
        return httpx2.Response(200, json=body)

    return anthropic.AsyncAnthropic(
        api_key=SENTINEL_KEY,
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


def hn_client() -> httpx.AsyncClient:
    data = cast(dict[str, object], json.loads((FIXTURES / HN_FIXTURE).read_text()))
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


async def candidate_status(env: WorkflowEnvironment, external_id: str) -> CandidateStatus | None:
    handle = env.client.get_workflow_handle_for(
        StoryCandidateWorkflow.run, candidate_id_for(SourceName.HN, external_id)
    )
    try:
        state = await handle.query(StoryCandidateWorkflow.review_state)
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            return None
        raise
    return state.status


async def start_existing_candidate(env: WorkflowEnvironment, external_id: str) -> None:
    client: Client = env.client
    candidate = StoryCandidate.model_validate(
        {
            "source": "hn",
            "external_id": external_id,
            "title": "Pre-existing story",
            "url": "https://example.com/preexisting",
            "observed_at": OBSERVED,
            "summary": "A pre-existing summary",
            "proposed_angle": "A pre-existing angle",
            "selection_reason": "A pre-existing reason",
        }
    )
    await client.start_workflow(
        StoryCandidateWorkflow.run,
        StoryCandidateInput(candidate=candidate, review_timeout_seconds=3600),
        id=candidate_id_for(SourceName.HN, external_id),
        task_queue=CANDIDATE_TASK_QUEUE,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )


async def query_state(handle: StateHandle) -> SourceIngestionState:
    return await handle.query(SourceIngestionWorkflow.ingestion_state)


async def wait_total_polls(handle: StateHandle, count: int) -> SourceIngestionState:
    for _ in range(3000):
        state = await query_state(handle)
        if state.total_polls >= count:
            return state
        await asyncio.sleep(0.01)
    raise AssertionError(f"total_polls never reached {count}")


async def stop(handle: StateHandle) -> SourceIngestionResult:
    await handle.signal(SourceIngestionWorkflow.stop)
    return await handle.result()


@asynccontextmanager
async def multi_run_env(
    inputs: Sequence[SourceIngestionInput],
    poll_activities: Sequence[PollActivity],
    selection: SelectActivity,
) -> AsyncGenerator[tuple[WorkflowEnvironment, list[StateHandle]], None]:
    env = await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)
    await ensure_candidate_search_attributes(env.client)
    async with env:
        async with (
            Worker(
                env.client,
                task_queue=SOURCE_INGESTION_TASK_QUEUE,
                workflows=[SourceIngestionWorkflow],
                activities=[*poll_activities, selection],
            ),
            Worker(
                env.client,
                task_queue=CANDIDATE_TASK_QUEUE,
                workflows=[StoryCandidateWorkflow],
            ),
        ):
            handles: list[StateHandle] = []
            for data in inputs:
                workflow_id = source_ingestion_workflow_id(data.source)
                await env.client.start_workflow(
                    SourceIngestionWorkflow.run,
                    data,
                    id=workflow_id,
                    task_queue=SOURCE_INGESTION_TASK_QUEUE,
                )
                handles.append(
                    env.client.get_workflow_handle(workflow_id, result_type=SourceIngestionResult)
                )
            yield env, handles


@pytest.mark.asyncio
async def test_workflow_emits_poll_selection_duplicate_candidate_and_budget_events(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="temporalio.workflow")
    poller = GatedPoller([poll_output(event("2001"), event("2002")), poll_output(event("2003"))])
    selector = ScriptedSelector([selected("2001"), selected("2002")])
    data = make_input(candidate_budget=1)
    async with multi_run_env([data], [poller.poll_hn_source], selector.select_story_candidates) as (
        env,
        handles,
    ):
        handle = handles[0]
        await poller.wait_for_call(1)
        await start_existing_candidate(env, "2001")
        await poller.release()
        await wait_total_polls(handle, 1)
        for _ in range(3000):
            if await candidate_status(env, "2002") is CandidateStatus.AWAITING_REVIEW:
                break
            await asyncio.sleep(0.01)
        await env.sleep(30)
        await poller.wait_for_call(2)
        await poller.release()
        await wait_total_polls(handle, 2)
        result = await stop(handle)
    assert result.reason == "stopped"

    started = records_for(caplog, IngestionLogEvent.CANDIDATE_STARTED)
    duplicates = records_for(caplog, IngestionLogEvent.CANDIDATE_DUPLICATE)
    exhausted = records_for(caplog, IngestionLogEvent.BUDGET_EXHAUSTED)
    poll_records = records_for(caplog, IngestionLogEvent.POLL)
    selection_records = records_for(caplog, IngestionLogEvent.SELECTION)

    assert len(started) == 1
    assert record_field(started[0], "external_id") == "2002"
    assert record_field(started[0], "candidate_id") == candidate_id_for(SourceName.HN, "2002")
    assert record_field(started[0], "budget_remaining") == 0
    assert record_field(started[0], "source") == "hn"

    assert len(duplicates) == 1
    assert record_field(duplicates[0], "external_id") == "2001"
    assert record_field(duplicates[0], "candidate_id") == candidate_id_for(SourceName.HN, "2001")

    assert len(exhausted) == 1
    assert record_field(exhausted[0], "budget") == 1
    assert record_field(exhausted[0], "candidates_started") == 1
    assert record_field(exhausted[0], "day") == str(datetime.now(UTC).date())

    assert len(poll_records) == 2
    first_poll = poll_records[0]
    assert record_field(first_poll, "poll") == 1
    assert record_field(first_poll, "event_count") == 2
    assert record_field(first_poll, "new_event_count") == 2
    assert record_field(first_poll, "batch_size") == 2
    assert record_field(first_poll, "cursor") == "2002"
    assert record_field(first_poll, "budget_remaining") == 1

    assert len(selection_records) == 1
    assert record_field(selection_records[0], "batch_size") == 2
    assert record_field(selection_records[0], "selected_count") == 2
    assert record_field(selection_records[0], "max_candidates") == 1
    assert record_field(selection_records[0], "budget_remaining") == 1


@pytest.mark.asyncio
async def test_workflow_and_activity_logs_contain_no_titles_or_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    caplog.set_level(logging.INFO, logger="temporalio.workflow")
    caplog.set_level(logging.INFO, logger="temporalio.activity")
    hn = hn_client()
    selector = StorySelectionActivities(
        anthropic_client("llm_select_story_candidates_ingestion.json")
    )
    hn_activities = HNSourceActivities(hn)
    fake_activities = FakeSourceActivities()
    inputs = [make_input(), make_input(source=SourceName.FAKE)]
    async with hn:
        async with multi_run_env(
            inputs,
            [hn_activities.poll_hn_source, fake_activities.poll_fake_source],
            selector.select_story_candidates,
        ) as (_env, handles):
            for handle in handles:
                await wait_total_polls(handle, 1)
            results = [await stop(handle) for handle in handles]
    assert [result.reason for result in results] == ["stopped", "stopped"]

    sentinels = [
        "Show HN: Normal story",
        fake_title(0),
        fake_title(1),
        "https://example.com/fake-source/1001",
        SENTINEL_KEY,
    ]
    assert len(caplog.records) > 0
    for record in caplog.records:
        text = log_text(record)
        for sentinel in sentinels:
            assert sentinel not in text, sentinel
        assert "title" not in record.__dict__
        assert "summary" not in record.__dict__
        assert "api_key" not in record.__dict__


@pytest.mark.asyncio
async def test_poll_activities_log_normalized_event_counts_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="temporalio.activity")
    hn = hn_client()
    async with hn:
        hn_activities = HNSourceActivities(hn)
        hn_result = await ActivityEnvironment().run(
            hn_activities.poll_hn_source, SourcePollInput(source=SourceName.HN, limit=5)
        )
    fake_result = await ActivityEnvironment().run(
        FakeSourceActivities().poll_fake_source,
        SourcePollInput(source=SourceName.FAKE, limit=2),
    )
    assert isinstance(hn_result, SourcePollOutput)
    assert isinstance(fake_result, SourcePollOutput)

    records = records_for(caplog, IngestionLogEvent.EVENTS_NORMALIZED)
    assert len(records) == 2
    by_source = {record_field(record, "source"): record for record in records}
    assert record_field(by_source["hn"], "requested_limit") == 5
    assert record_field(by_source["hn"], "event_count") == len(hn_result.events)
    assert record_field(by_source["fake"], "requested_limit") == 2
    assert record_field(by_source["fake"], "event_count") == 2
    for record in records:
        text = log_text(record)
        assert "Show HN" not in text
        assert fake_title(0) not in text
        assert "title" not in record.__dict__
