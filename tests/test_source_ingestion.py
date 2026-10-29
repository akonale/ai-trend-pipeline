"""Time-skipping tests for SourceIngestionWorkflow and its activity loop."""

import ast
import asyncio
import json
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import anthropic
import httpx2
import pytest
from temporalio import activity
from temporalio.client import (
    Client,
    WorkflowFailureError,
    WorkflowHandle,
)
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError, RPCStatusCode
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

import ai_trend_pipeline.source_ingestion as source_ingestion
from ai_trend_pipeline.source_ingestion import (
    SOURCE_INGESTION_TASK_QUEUE,
    SourceIngestionInput,
    SourceIngestionResult,
    SourceIngestionState,
    SourceIngestionWorkflow,
    source_ingestion_workflow_id,
)
from ai_trend_pipeline.sources import SourceEvent, SourceName, SourcePollInput, SourcePollOutput
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
OBSERVED = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
SelectActivity = Callable[[SelectStoryCandidatesInput], Awaitable[SelectStoryCandidatesOutput]]
StateHandle = WorkflowHandle[SourceIngestionWorkflow, SourceIngestionResult]


def event(external_id: str, title: str | None = None) -> SourceEvent:
    return SourceEvent(
        source=SourceName.HN,
        external_id=external_id,
        title=title or f"Story {external_id}",
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


class PollerBase:
    """Activity object whose method is registered under the registry activity name."""

    @activity.defn(name="poll_hn_source")
    async def poll_hn_source(self, data: SourcePollInput) -> SourcePollOutput:
        return await self._next(data)

    async def _next(self, data: SourcePollInput) -> SourcePollOutput:
        raise NotImplementedError


class GatedPoller(PollerBase):
    """Blocks every activity attempt until the test releases it.

    Retries become individual calls, so tests can step the loop without wall-clock waits.
    """

    def __init__(self, results: Sequence[SourcePollOutput | BaseException]) -> None:
        self._results = list(results)
        self._gates: asyncio.Queue[asyncio.Event] = asyncio.Queue()
        self.calls = 0
        self.inputs: list[SourcePollInput] = []

    async def _next(self, data: SourcePollInput) -> SourcePollOutput:
        index = self.calls
        self.calls += 1
        self.inputs.append(data)
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

    async def release_if_pending(self, timeout: float) -> None:
        try:
            gate = await asyncio.wait_for(self._gates.get(), timeout=timeout)
        except TimeoutError:
            return
        gate.set()


class ScriptedSelector:
    def __init__(
        self,
        selections: Sequence[SelectedStory] = (),
        *,
        error: BaseException | None = None,
    ) -> None:
        self._selections = list(selections)
        self._error = error
        self.calls = 0
        self.inputs: list[SelectStoryCandidatesInput] = []

    @activity.defn(name="select_story_candidates")
    async def select_story_candidates(
        self, data: SelectStoryCandidatesInput
    ) -> SelectStoryCandidatesOutput:
        self.calls += 1
        self.inputs.append(data)
        if self._error is not None:
            raise self._error
        return SelectStoryCandidatesOutput(selections=list(self._selections))


def anthropic_client(fixture_name: str) -> anthropic.AsyncAnthropic:
    body = cast(dict[str, object], json.loads((FIXTURES / fixture_name).read_text()))

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.method != "POST" or not request.url.path.endswith("/v1/messages"):
            raise AssertionError(f"unexpected request: {request.method} {request.url}")
        return httpx2.Response(200, json=body)

    return anthropic.AsyncAnthropic(
        api_key="test-key",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


async def eventually(check: Callable[[], Awaitable[bool]], message: str) -> None:
    for _ in range(3000):
        if await check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(message)


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


async def candidate_awaiting(env: WorkflowEnvironment, external_id: str) -> bool:
    return await candidate_status(env, external_id) is CandidateStatus.AWAITING_REVIEW


def make_candidate(external_id: str, title: str = "Pre-existing story") -> StoryCandidate:
    return StoryCandidate.model_validate(
        {
            "source": SourceName.HN,
            "external_id": external_id,
            "title": title,
            "url": "https://example.com/preexisting",
            "observed_at": OBSERVED,
            "summary": "A pre-existing summary",
            "proposed_angle": "A pre-existing angle",
            "selection_reason": "A pre-existing reason",
        }
    )


async def start_existing_candidate(
    env: WorkflowEnvironment, external_id: str, timeout: int = 3600
) -> None:
    client: Client = env.client
    await client.start_workflow(
        StoryCandidateWorkflow.run,
        StoryCandidateInput(candidate=make_candidate(external_id), review_timeout_seconds=timeout),
        id=candidate_id_for(SourceName.HN, external_id),
        task_queue=CANDIDATE_TASK_QUEUE,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )


async def query_state(handle: StateHandle) -> SourceIngestionState:
    return await handle.query(SourceIngestionWorkflow.ingestion_state)


async def wait_for_total_polls(handle: StateHandle, count: int) -> SourceIngestionState:
    for _ in range(3000):
        state = await query_state(handle)
        if state.total_polls >= count:
            return state
        await asyncio.sleep(0.005)
    raise AssertionError(f"total_polls never reached {count}")


async def processed_contains(handle: StateHandle, external_id: str) -> bool:
    state = await query_state(handle)
    return external_id in state.processed_ids


async def selector_reached(selector: ScriptedSelector, count: int) -> bool:
    return selector.calls >= count


async def advance_until_called(env: WorkflowEnvironment, poller: GatedPoller, count: int) -> None:
    for _ in range(120):
        if poller.calls >= count:
            return
        await env.sleep(timedelta(seconds=1))
    raise AssertionError(f"poll attempt {count} never started (saw {poller.calls})")


@asynccontextmanager
async def ingestion_env(
    data: SourceIngestionInput,
    poller: PollerBase,
    selection: SelectActivity,
) -> AsyncGenerator[tuple[WorkflowEnvironment, StateHandle], None]:
    env = await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)
    await ensure_candidate_search_attributes(env.client)
    ingestion_worker = Worker(
        env.client,
        task_queue=SOURCE_INGESTION_TASK_QUEUE,
        workflows=[SourceIngestionWorkflow],
        activities=[poller.poll_hn_source, selection],
    )
    candidate_worker = Worker(
        env.client,
        task_queue=CANDIDATE_TASK_QUEUE,
        workflows=[StoryCandidateWorkflow],
    )
    workflow_id = source_ingestion_workflow_id(data.source)
    async with env:
        async with ingestion_worker, candidate_worker:
            await env.client.start_workflow(
                SourceIngestionWorkflow.run,
                data,
                id=workflow_id,
                task_queue=SOURCE_INGESTION_TASK_QUEUE,
            )
            handle: StateHandle = env.client.get_workflow_handle(
                workflow_id, result_type=SourceIngestionResult
            )
            yield env, handle


async def stop_ingestion(handle: StateHandle, poller: GatedPoller) -> SourceIngestionResult:
    """Signal stop and release any poll attempt that starts before it is observed."""
    await handle.signal(SourceIngestionWorkflow.stop)
    result_task = asyncio.create_task(handle.result())
    while not result_task.done():
        await poller.release_if_pending(0.01)
    return await result_task


@pytest.mark.asyncio
async def test_polls_multiple_times_on_temporal_timers() -> None:
    poller = GatedPoller([poll_output(), poll_output(), poll_output()])
    selector = ScriptedSelector()
    async with ingestion_env(make_input(), poller, selector.select_story_candidates) as (
        env,
        handle,
    ):
        await poller.wait_for_call(1)
        await poller.release()
        first = await wait_for_total_polls(handle, 1)
        await env.sleep(timedelta(seconds=30))
        await poller.wait_for_call(2)
        await poller.release()
        second = await wait_for_total_polls(handle, 2)
        result = await stop_ingestion(handle, poller)
    assert first.total_polls == 1
    assert second.total_polls == 2
    assert first.last_poll_at is not None
    assert second.last_poll_at is not None
    delta = second.last_poll_at - first.last_poll_at
    assert timedelta(seconds=30) <= delta < timedelta(seconds=31)
    assert result.reason == "stopped"


@pytest.mark.asyncio
async def test_empty_selection_starts_no_candidate_and_advances_state() -> None:
    poller = GatedPoller([poll_output(event("2001"))])
    selector = ScriptedSelector()
    async with ingestion_env(make_input(), poller, selector.select_story_candidates) as (
        env,
        handle,
    ):
        await poller.wait_for_call(1)
        await poller.release()
        await wait_for_total_polls(handle, 1)
        await eventually(lambda: selector_reached(selector, 1), "selector never ran")
        state = await query_state(handle)
        assert state.processed_ids == ("2001",)
        assert state.last_selected_count == 0
        assert await candidate_status(env, "2001") is None
        result = await stop_ingestion(handle, poller)
    assert selector.calls == 1
    assert result.reason == "stopped"


@pytest.mark.asyncio
async def test_repeated_top_story_ids_are_not_reselected() -> None:
    poller = GatedPoller([poll_output(event("2001"))])
    selector = ScriptedSelector([selected("2001")])
    async with ingestion_env(make_input(), poller, selector.select_story_candidates) as (
        env,
        handle,
    ):
        await poller.wait_for_call(1)
        await poller.release()
        await wait_for_total_polls(handle, 1)
        await eventually(lambda: candidate_awaiting(env, "2001"), "candidate did not start")
        await env.sleep(timedelta(seconds=30))
        await poller.wait_for_call(2)
        await poller.release()
        state = await wait_for_total_polls(handle, 2)
        assert selector.calls == 1
        assert state.processed_ids == ("2001",)
        result = await stop_ingestion(handle, poller)
    assert result.reason == "stopped"


@pytest.mark.asyncio
async def test_processed_ids_prevent_reevaluation_within_the_bound() -> None:
    poller = GatedPoller([poll_output(event("2001"), event("2002"))])
    selector = ScriptedSelector([selected("2002")])
    data = make_input(processed_ids=("2001",))
    async with ingestion_env(data, poller, selector.select_story_candidates) as (
        env,
        handle,
    ):
        await poller.wait_for_call(1)
        await poller.release()
        await eventually(lambda: selector_reached(selector, 1), "selector never ran")
        state = await query_state(handle)
        assert [item.external_id for item in selector.inputs[0].events] == ["2002"]
        assert state.processed_ids == ("2001", "2002")
        assert await candidate_status(env, "2001") is None
        assert await candidate_awaiting(env, "2002")
        result = await stop_ingestion(handle, poller)
    assert result.reason == "stopped"


@pytest.mark.asyncio
async def test_duplicate_candidate_start_is_ignored_and_one_candidate_remains() -> None:
    poller = GatedPoller([poll_output(event("2001"))])
    selector = ScriptedSelector([selected("2001")])
    async with ingestion_env(make_input(), poller, selector.select_story_candidates) as (
        env,
        handle,
    ):
        await poller.wait_for_call(1)
        await start_existing_candidate(env, "2001")
        await poller.release()
        await wait_for_total_polls(handle, 1)
        await eventually(lambda: processed_contains(handle, "2001"), "batch never marked processed")
        assert await candidate_status(env, "2001") is CandidateStatus.AWAITING_REVIEW
        result = await stop_ingestion(handle, poller)
    assert result.reason == "stopped"


@pytest.mark.asyncio
async def test_selection_caps_candidate_starts() -> None:
    poller = GatedPoller([poll_output(event("101"), event("102"), event("103"), event("104"))])
    activities = StorySelectionActivities(
        anthropic_client("llm_select_story_candidates_over_cap.json")
    )
    data = make_input(max_candidates=2)
    async with ingestion_env(data, poller, activities.select_story_candidates) as (
        env,
        handle,
    ):
        await poller.wait_for_call(1)
        await poller.release()
        await eventually(lambda: _all_awaiting(env, ("101", "102")), "two candidates did not start")
        assert await candidate_status(env, "103") is None
        assert await candidate_status(env, "104") is None
        result = await stop_ingestion(handle, poller)
    assert result.reason == "stopped"


async def _all_awaiting(env: WorkflowEnvironment, external_ids: Sequence[str]) -> bool:
    statuses = [await candidate_status(env, value) for value in external_ids]
    return all(status is CandidateStatus.AWAITING_REVIEW for status in statuses)


@pytest.mark.asyncio
async def test_poll_activity_retry_then_success() -> None:
    poller = GatedPoller(
        [
            ApplicationError("transient"),
            ApplicationError("transient"),
            poll_output(event("2001")),
        ]
    )
    selector = ScriptedSelector([selected("2001")])
    async with ingestion_env(make_input(), poller, selector.select_story_candidates) as (
        env,
        handle,
    ):
        await poller.wait_for_call(1)
        await poller.release()
        await advance_until_called(env, poller, 2)
        await poller.release()
        await advance_until_called(env, poller, 3)
        await poller.release()
        await eventually(lambda: candidate_awaiting(env, "2001"), "candidate did not start")
        assert poller.calls >= 3
        assert selector.calls == 1
        result = await stop_ingestion(handle, poller)
    assert result.reason == "stopped"


@pytest.mark.asyncio
async def test_persistent_poll_failure_keeps_the_loop_alive() -> None:
    poller = GatedPoller([ApplicationError("down")])
    selector = ScriptedSelector([selected("2001")])
    async with ingestion_env(make_input(), poller, selector.select_story_candidates) as (
        env,
        handle,
    ):
        await poller.wait_for_call(1)
        await poller.release()
        await advance_until_called(env, poller, 2)
        await poller.release()
        await advance_until_called(env, poller, 3)
        await poller.release()
        state = await wait_for_total_polls(handle, 1)
        assert state.last_event_count == 0
        assert state.last_new_event_count == 0
        result = await stop_ingestion(handle, poller)
    assert poller.calls >= 3
    assert selector.calls == 0
    assert result.reason == "stopped"


@pytest.mark.asyncio
async def test_selector_failure_skips_the_batch_and_continues() -> None:
    poller = GatedPoller([poll_output(event("2001"))])
    selector = ScriptedSelector(error=ApplicationError("bad selection", non_retryable=True))
    async with ingestion_env(make_input(), poller, selector.select_story_candidates) as (
        env,
        handle,
    ):
        await poller.wait_for_call(1)
        await poller.release()
        await eventually(lambda: processed_contains(handle, "2001"), "failed batch was not marked")
        assert await candidate_status(env, "2001") is None
        await env.sleep(timedelta(seconds=30))
        await poller.wait_for_call(2)
        await poller.release()
        await wait_for_total_polls(handle, 2)
        assert selector.calls == 1
        result = await stop_ingestion(handle, poller)
    assert result.reason == "stopped"


@pytest.mark.asyncio
async def test_continue_as_new_preserves_cursor_and_processed_ids() -> None:
    poller = GatedPoller([poll_output(event("2001"))])
    selector = ScriptedSelector([selected("2001")])
    data = make_input(max_polls_per_run=1)
    async with ingestion_env(data, poller, selector.select_story_candidates) as (
        env,
        handle,
    ):
        await poller.wait_for_call(1)
        await poller.release()
        await poller.wait_for_call(2)
        second_run = await query_state(handle)
        assert second_run.cursor == "2001"
        assert second_run.processed_ids == ("2001",)
        assert second_run.polls_in_run == 0
        assert second_run.total_polls == 1

        await poller.release()
        await poller.wait_for_call(3)
        third_run = await query_state(handle)
        assert third_run.cursor == "2001"
        assert third_run.processed_ids == ("2001",)
        assert third_run.polls_in_run == 0
        assert third_run.total_polls == 2
        assert selector.calls == 1
        assert await candidate_awaiting(env, "2001")
        result = await stop_ingestion(handle, poller)
    assert result.reason == "stopped"


@pytest.mark.asyncio
async def test_stop_signal_returns_stopped_result() -> None:
    poller = GatedPoller([poll_output()])
    selector = ScriptedSelector()
    async with ingestion_env(make_input(), poller, selector.select_story_candidates) as (
        _env,
        handle,
    ):
        await poller.wait_for_call(1)
        await poller.release()
        await wait_for_total_polls(handle, 1)
        calls_at_stop = poller.calls
        result = await stop_ingestion(handle, poller)
        await asyncio.sleep(0.05)
        assert poller.calls == calls_at_stop
    assert result.reason == "stopped"
    assert result.source is SourceName.HN


@pytest.mark.asyncio
async def test_cancellation_stops_ingestion_and_leaves_candidates_running() -> None:
    poller = GatedPoller([poll_output(event("2001"))])
    selector = ScriptedSelector([selected("2001")])
    async with ingestion_env(make_input(), poller, selector.select_story_candidates) as (
        env,
        handle,
    ):
        await poller.wait_for_call(1)
        await poller.release()
        await eventually(lambda: candidate_awaiting(env, "2001"), "candidate did not start")
        await handle.cancel()
        with pytest.raises(WorkflowFailureError):
            await handle.result()
        assert await candidate_status(env, "2001") is CandidateStatus.AWAITING_REVIEW


@pytest.mark.asyncio
async def test_ingestion_state_query_tracks_counts() -> None:
    poller = GatedPoller([poll_output(event("2001"), event("2002"))])
    selector = ScriptedSelector([selected("2001")])
    async with ingestion_env(make_input(), poller, selector.select_story_candidates) as (
        _env,
        handle,
    ):
        await poller.wait_for_call(1)
        await poller.release()
        await eventually(lambda: _state_selected(handle), "selection count never updated")
        state = await query_state(handle)
        assert state.source is SourceName.HN
        assert state.cursor == "2002"
        assert len(state.processed_ids) == 2
        assert state.polls_in_run == 1
        assert state.total_polls == 1
        assert state.last_poll_at is not None
        assert state.last_event_count == 2
        assert state.last_new_event_count == 2
        assert state.last_selected_count == 1
        result = await stop_ingestion(handle, poller)
    assert result.reason == "stopped"


async def _state_selected(handle: StateHandle) -> bool:
    return (await query_state(handle)).last_selected_count == 1


def test_workflow_source_has_no_io_wall_clock_or_random_calls() -> None:
    module_file = source_ingestion.__file__
    assert module_file is not None
    source = Path(module_file).read_text()
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    forbidden = (
        "httpx",
        "anthropic",
        "temporalio.client",
        "ai_trend_pipeline.services",
        "ai_trend_pipeline.web",
        "ai_trend_pipeline.sources.hn",
        "ai_trend_pipeline.hn",
        "ai_trend_pipeline.daily_chart",
        "ai_trend_pipeline.worker",
    )
    for name in imported:
        for banned in forbidden:
            assert name != banned and not name.startswith(f"{banned}."), (name, banned)
    for needle in (
        "datetime.now",
        "time.time",
        "random.",
        "uuid.uuid4",
        "asyncio.sleep",
        "start_workflow(",
        "Client.connect",
    ):
        assert needle not in source, needle
    for present in (
        "workflow.now",
        "workflow.wait_condition",
        "workflow.continue_as_new",
        "start_child_workflow",
    ):
        assert present in source, present
