"""Time-skipping tests for StoryGenerationWorkflow and its named activities."""

import asyncio
import inspect
import time
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from temporalio import activity
from temporalio.client import WorkflowHandle
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from ai_trend_pipeline.analysis.models import FactInput, FactSet
from ai_trend_pipeline.research.models import ResearchInput, ResearchOutput
from ai_trend_pipeline.story_drafting import (
    CritiqueInput,
    CritiqueOutput,
    DraftInput,
    DraftOutput,
)
from ai_trend_pipeline.story_drafting.models import (
    STORY_CRITIQUE_INVALID_ERROR_TYPE,
    STORY_CRITIQUE_TOOL_MISSING_ERROR_TYPE,
    STORY_DRAFT_INVALID_ERROR_TYPE,
    STORY_DRAFT_TOOL_MISSING_ERROR_TYPE,
)
from ai_trend_pipeline.story_generation import (
    STORY_TASK_QUEUE,
    FinalStoryDecision,
    StoryGenerationInput,
    StoryGenerationResult,
    StoryGenerationState,
    StoryGenerationStatus,
    StoryGenerationWorkflow,
)
from ai_trend_pipeline.story_status import PrimaryStoryStatus, primary_story_status
from ai_trend_pipeline.story_visibility import STORY_STATUS_ATTRIBUTE, story_start_attributes
from ai_trend_pipeline.temporal_client import ensure_story_search_attributes
from tests.drafting_fixtures import make_failed_critique
from tests.story_generation_fixtures import (
    make_draft_output,
    make_facts,
    make_passing,
    make_story_input,
    make_supported_research,
    make_unsupported_research,
    with_media_worker,
)

Handler = Callable[[int, Any], Any]
StoryHandle = WorkflowHandle[StoryGenerationWorkflow, StoryGenerationResult]

DRAFT_VALIDATION_DETAIL = "The draft did not pass factual validation."
WRITER_UNUSABLE_DETAIL = "The writer did not return a usable draft."
CHECKER_UNUSABLE_DETAIL = "The checker did not return a usable result."
AFTER_REVISION_DETAIL = "The story did not pass checking after one revision."
EXPIRED_DETAIL = "The review window closed."


class FakeStoryActivities:
    """Named fakes with optional per-call gates so a test can query between stages."""

    def __init__(
        self,
        *,
        research: Handler | None = None,
        facts: Handler | None = None,
        draft: Handler | None = None,
        critique: Handler | None = None,
        gated: bool = False,
    ) -> None:
        self._handlers: dict[str, Handler] = {
            "research_story": research or _default_research,
            "calculate_facts": facts or _default_facts,
            "draft_story": draft or _default_draft,
            "critique_story": critique or _default_critique,
        }
        self._gated = gated
        self._gates: dict[str, asyncio.Queue[asyncio.Event]] = {
            name: asyncio.Queue() for name in self._handlers
        }
        self.calls: dict[str, int] = {name: 0 for name in self._handlers}
        self.inputs: dict[str, list[Any]] = {name: [] for name in self._handlers}

    async def _call(self, name: str, data: Any) -> Any:
        index = self.calls[name]
        self.calls[name] = index + 1
        self.inputs[name].append(data)
        if self._gated:
            gate = asyncio.Event()
            await self._gates[name].put(gate)
            await gate.wait()
        result = self._handlers[name](index, data)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, BaseException):
            raise result
        return result

    @activity.defn(name="research_story")
    async def research_story(self, data: ResearchInput) -> ResearchOutput:
        return cast(ResearchOutput, await self._call("research_story", data))

    @activity.defn(name="calculate_facts")
    async def calculate_facts(self, data: FactInput) -> FactSet:
        return cast(FactSet, await self._call("calculate_facts", data))

    @activity.defn(name="draft_story")
    async def draft_story(self, data: DraftInput) -> DraftOutput:
        return cast(DraftOutput, await self._call("draft_story", data))

    @activity.defn(name="critique_story")
    async def critique_story(self, data: CritiqueInput) -> CritiqueOutput:
        return cast(CritiqueOutput, await self._call("critique_story", data))

    def activities(self) -> list[Callable[..., Any]]:
        return [
            self.research_story,
            self.calculate_facts,
            self.draft_story,
            self.critique_story,
        ]

    async def wait_for_call(self, name: str, count: int) -> None:
        for _ in range(4000):
            if self.calls[name] >= count:
                return
            await asyncio.sleep(0.005)
        raise AssertionError(f"{name} was not called {count} times (saw {self.calls[name]})")

    async def release(self, name: str) -> None:
        gate = await asyncio.wait_for(self._gates[name].get(), timeout=10)
        gate.set()


def _default_research(_index: int, data: Any) -> ResearchOutput:
    return make_supported_research(cast(ResearchInput, data).story)


def _default_facts(_index: int, data: Any) -> FactSet:
    return make_facts(cast(FactInput, data).story_id)


def _default_draft(_index: int, data: Any) -> DraftOutput:
    return make_draft_output(cast(DraftInput, data).story.candidate_id)


def _default_critique(_index: int, data: Any) -> CritiqueOutput:
    return make_passing(cast(CritiqueInput, data).draft.story_id)


async def start_env() -> WorkflowEnvironment:
    env = await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)
    await ensure_story_search_attributes(env.client)
    return env


def make_story_worker(env: WorkflowEnvironment, fakes: FakeStoryActivities) -> Worker:
    return Worker(
        env.client,
        task_queue=STORY_TASK_QUEUE,
        workflows=[StoryGenerationWorkflow],
        activities=fakes.activities(),
    )


def make_worker(
    env: WorkflowEnvironment, fakes: FakeStoryActivities
) -> AbstractAsyncContextManager[None]:
    """The story worker plus a fake-renderer media worker for the media step."""
    return with_media_worker(make_story_worker(env, fakes), env)


async def start_story(
    env: WorkflowEnvironment,
    *,
    observed_at: Any | None = None,
    review_timeout_seconds: int = 172800,
) -> StoryHandle:
    if observed_at is None:
        observed_at = await env.get_current_time()
    story = make_story_input(observed_at=observed_at)
    data = StoryGenerationInput(
        candidate_id=story.candidate_id,
        story=story,
        story_created_at=observed_at,
        review_timeout_seconds=review_timeout_seconds,
    )
    return await env.client.start_workflow(
        StoryGenerationWorkflow.run,
        data,
        id=f"story-test-{uuid.uuid4()}",
        task_queue=STORY_TASK_QUEUE,
        search_attributes=story_start_attributes(
            StoryGenerationStatus.RESEARCHING.value,
            story.title,
            story.source.value,
            story.candidate_id,
            observed_at,
            observed_at,
        ),
    )


async def wait_for_status(
    handle: StoryHandle,
    status: StoryGenerationStatus,
    *,
    timeout: float = 10.0,
) -> StoryGenerationState:
    deadline = time.monotonic() + timeout
    state: StoryGenerationState | None = None
    while time.monotonic() < deadline:
        state = await handle.query(StoryGenerationWorkflow.story_state)
        if state.status is status:
            return state
        await asyncio.sleep(0.01)
    raise AssertionError(f"state never reached {status}; last was {state}")


async def story_status_attribute(handle: StoryHandle) -> object:
    description = await handle.describe()
    return description.typed_search_attributes.get(STORY_STATUS_ATTRIBUTE, None)


def signal_quietly(
    handle: StoryHandle, signal: Callable[..., Any], decision: FinalStoryDecision
) -> Any:
    return handle.signal(signal, decision)


@pytest.mark.asyncio
async def test_every_internal_state_and_five_status_mapping() -> None:
    fakes = FakeStoryActivities(gated=True)
    async with await start_env() as env, make_worker(env, fakes):
        handle = await start_story(env)
        expected = [
            ("research_story", StoryGenerationStatus.RESEARCHING, PrimaryStoryStatus.WRITING),
            ("calculate_facts", StoryGenerationStatus.ANALYZING, PrimaryStoryStatus.WRITING),
            ("draft_story", StoryGenerationStatus.WRITING, PrimaryStoryStatus.WRITING),
            ("critique_story", StoryGenerationStatus.CHECKING, PrimaryStoryStatus.WRITING),
        ]
        for name, status, primary in expected:
            await fakes.wait_for_call(name, 1)
            state = await handle.query(StoryGenerationWorkflow.story_state)
            assert state.status is status
            assert primary_story_status(state.status.value) is primary
            assert await story_status_attribute(handle) == status.value
            await fakes.release(name)
        review = await wait_for_status(handle, StoryGenerationStatus.REVIEW)
        assert primary_story_status(review.status.value) is PrimaryStoryStatus.REVIEW
        assert await story_status_attribute(handle) == StoryGenerationStatus.REVIEW.value
        await handle.signal(StoryGenerationWorkflow.approve, FinalStoryDecision())
        result = await handle.result()
        ready = await handle.query(StoryGenerationWorkflow.story_state)
        ready_attribute = await story_status_attribute(handle)
    assert result.status == "ready"
    assert ready.status is StoryGenerationStatus.READY
    assert primary_story_status(ready.status.value) is PrimaryStoryStatus.READY
    assert result.package is not None
    assert ready_attribute == StoryGenerationStatus.READY.value


@pytest.mark.asyncio
async def test_unsupported_research_skips_facts_writing_and_checking() -> None:
    fakes = FakeStoryActivities(
        research=lambda _index, data: make_unsupported_research(cast(ResearchInput, data).story)
    )
    async with await start_env() as env, make_worker(env, fakes):
        result = await (await start_story(env)).result()
    assert result.status == "unsupported"
    assert result.status_detail == "The candidate has no source link."
    assert result.package is None and result.critique is None
    assert fakes.calls["calculate_facts"] == 0
    assert fakes.calls["draft_story"] == 0
    assert fakes.calls["critique_story"] == 0


@pytest.mark.asyncio
async def test_one_failed_check_triggers_exactly_one_revision() -> None:
    def draft(index: int, data: Any) -> DraftOutput:
        story_id = cast(DraftInput, data).story.candidate_id
        headline = "First draft" if index == 0 else "Revised draft"
        return make_draft_output(story_id, headline=headline)

    def critique(index: int, data: Any) -> CritiqueOutput:
        story_id = cast(CritiqueInput, data).draft.story_id
        if index == 0:
            return make_failed_critique(story_id=story_id)
        return make_passing(story_id)

    fakes = FakeStoryActivities(draft=draft, critique=critique)
    async with await start_env() as env, make_worker(env, fakes):
        handle = await start_story(env)
        await wait_for_status(handle, StoryGenerationStatus.REVIEW)
        assert fakes.calls["draft_story"] == 2
        assert fakes.calls["critique_story"] == 2
        second_draft_input = cast(DraftInput, fakes.inputs["draft_story"][1])
        assert second_draft_input.revision is not None
        assert second_draft_input.revision.round == 1
        assert [cast(CritiqueInput, item).round for item in fakes.inputs["critique_story"]] == [
            0,
            1,
        ]
        await handle.signal(StoryGenerationWorkflow.approve, FinalStoryDecision())
        result = await handle.result()
    assert result.status == "ready"
    assert result.package is not None
    assert result.package.headline == "Revised draft"


@pytest.mark.asyncio
async def test_failed_check_after_revision_is_terminal_failed() -> None:
    def critique(_index: int, data: Any) -> CritiqueOutput:
        return make_failed_critique(story_id=cast(CritiqueInput, data).draft.story_id)

    fakes = FakeStoryActivities(critique=critique)
    async with await start_env() as env, make_worker(env, fakes):
        handle = await start_story(env)
        result = await handle.result()
        state = await handle.query(StoryGenerationWorkflow.story_state)
    assert result.status == "failed"
    assert result.status_detail == AFTER_REVISION_DETAIL
    assert result.package is None and result.critique is None
    assert state.status is StoryGenerationStatus.FAILED
    assert fakes.calls["draft_story"] == 2
    assert fakes.calls["critique_story"] == 2


@pytest.mark.parametrize(
    ("activity_name", "error_type", "expected_detail"),
    [
        ("draft_story", STORY_DRAFT_INVALID_ERROR_TYPE, DRAFT_VALIDATION_DETAIL),
        ("draft_story", STORY_DRAFT_TOOL_MISSING_ERROR_TYPE, WRITER_UNUSABLE_DETAIL),
        ("critique_story", STORY_CRITIQUE_INVALID_ERROR_TYPE, CHECKER_UNUSABLE_DETAIL),
        ("critique_story", STORY_CRITIQUE_TOOL_MISSING_ERROR_TYPE, CHECKER_UNUSABLE_DETAIL),
    ],
)
@pytest.mark.asyncio
async def test_invalid_draft_and_tool_missing_errors_fail_without_retry(
    activity_name: str, error_type: str, expected_detail: str
) -> None:
    def failing(_index: int, _data: Any) -> Any:
        raise ApplicationError("non-retryable", type=error_type, non_retryable=True)

    handlers: dict[str, Handler | None] = {
        "research_story": None,
        "calculate_facts": None,
        "draft_story": None,
        "critique_story": None,
    }
    handlers[activity_name] = failing
    fakes = FakeStoryActivities(
        research=handlers["research_story"],
        facts=handlers["calculate_facts"],
        draft=handlers["draft_story"],
        critique=handlers["critique_story"],
    )
    async with await start_env() as env, make_worker(env, fakes):
        result = await (await start_story(env)).result()
    assert result.status == "failed"
    assert result.status_detail == expected_detail
    assert fakes.calls[activity_name] == 1


@pytest.mark.asyncio
async def test_research_retry_then_success_follows_the_policy() -> None:
    def research(index: int, data: Any) -> ResearchOutput:
        if index < 2:
            raise ApplicationError("transient research failure")
        return make_supported_research(cast(ResearchInput, data).story)

    fakes = FakeStoryActivities(research=research)
    async with await start_env() as env, make_worker(env, fakes):
        handle = await start_story(env)
        await wait_for_status(handle, StoryGenerationStatus.REVIEW)
        await handle.signal(StoryGenerationWorkflow.approve, FinalStoryDecision())
        result = await handle.result()
    assert fakes.calls["research_story"] == 3
    assert result.status == "ready"


@pytest.mark.asyncio
async def test_final_approve_returns_ready_package() -> None:
    fakes = FakeStoryActivities()
    async with await start_env() as env, make_worker(env, fakes):
        handle = await start_story(env)
        await wait_for_status(handle, StoryGenerationStatus.REVIEW)
        await handle.signal(
            StoryGenerationWorkflow.approve,
            FinalStoryDecision(note="ship it", decided_by="alice"),
        )
        result = await handle.result()
        state = await handle.query(StoryGenerationWorkflow.story_state)
        ready_attribute = await story_status_attribute(handle)
    assert result.status == "ready"
    assert result.package is not None
    drafted = make_draft_output(make_story_input().candidate_id).draft
    assert result.package.media is not None
    assert result.package.model_copy(update={"media": None}) == drafted
    assert result.critique is not None and result.critique.passed is True
    assert result.note == "ship it"
    assert result.decided_by == "alice"
    assert result.decided_at is not None
    assert state.status is StoryGenerationStatus.READY
    assert ready_attribute == StoryGenerationStatus.READY.value
    assert sorted(activity.__name__ for activity in fakes.activities()) == [
        "calculate_facts",
        "critique_story",
        "draft_story",
        "research_story",
    ]


@pytest.mark.asyncio
async def test_final_reject_returns_rejected() -> None:
    fakes = FakeStoryActivities()
    async with await start_env() as env, make_worker(env, fakes):
        handle = await start_story(env)
        await wait_for_status(handle, StoryGenerationStatus.REVIEW)
        await handle.signal(StoryGenerationWorkflow.reject, FinalStoryDecision(note="no"))
        result = await handle.result()
        state = await handle.query(StoryGenerationWorkflow.story_state)
    assert result.status == "rejected"
    assert result.package is not None and result.critique is not None
    assert result.note == "no"
    assert state.status is StoryGenerationStatus.REJECTED
    assert all(
        name not in {"post_chart_to_bluesky", "render_chart_png"}
        for name in (activity.__name__ for activity in fakes.activities())
    )


@pytest.mark.asyncio
async def test_first_final_decision_wins() -> None:
    fakes = FakeStoryActivities()
    async with await start_env() as env, make_worker(env, fakes):
        approved = await start_story(env)
        await wait_for_status(approved, StoryGenerationStatus.REVIEW)
        await approved.signal(StoryGenerationWorkflow.approve, FinalStoryDecision(note="first"))
        try:
            await signal_quietly(
                approved, StoryGenerationWorkflow.reject, FinalStoryDecision(note="second")
            )
        except RPCError:
            pass
        approved_result = await approved.result()

        rejected = await start_story(env)
        await wait_for_status(rejected, StoryGenerationStatus.REVIEW)
        await rejected.signal(StoryGenerationWorkflow.reject, FinalStoryDecision(note="first"))
        try:
            await signal_quietly(
                rejected, StoryGenerationWorkflow.approve, FinalStoryDecision(note="second")
            )
        except RPCError:
            pass
        rejected_result = await rejected.result()
    assert approved_result.status == "ready"
    assert approved_result.note == "first"
    assert rejected_result.status == "rejected"
    assert rejected_result.note == "first"


@pytest.mark.asyncio
async def test_review_expires_at_the_ui_cutoff_not_the_configured_timeout() -> None:
    async with await start_env() as env, make_worker(env, FakeStoryActivities()):
        now = await env.get_current_time()
        observed_at = now - timedelta(hours=47, minutes=59)
        handle = await start_story(env, observed_at=observed_at, review_timeout_seconds=604800)
        await env.sleep(timedelta(hours=2))
        result = await handle.result()
    assert result.status == "expired"
    assert result.status_detail == EXPIRED_DETAIL
    assert result.package is None and result.critique is None


@pytest.mark.asyncio
async def test_story_started_after_the_cutoff_expires_without_activities() -> None:
    fakes = FakeStoryActivities()
    async with await start_env() as env, make_worker(env, fakes):
        now = await env.get_current_time()
        result = await (await start_story(env, observed_at=now - timedelta(hours=49))).result()
    assert result.status == "expired"
    assert result.status_detail == EXPIRED_DETAIL
    assert fakes.calls == {
        "research_story": 0,
        "calculate_facts": 0,
        "draft_story": 0,
        "critique_story": 0,
    }


def test_workflow_source_has_no_io_clock_randomness_or_publishing() -> None:
    source = (
        Path(__file__).resolve().parent.parent / "src" / "ai_trend_pipeline" / "story_generation.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "httpx",
        "anthropic",
        "temporalio.client",
        "datetime.now",
        "time.time",
        "random.",
        "uuid.uuid4",
        "bluesky",
        "render",
        "daily_chart",
    ):
        assert forbidden not in source, forbidden
    for required in (
        "research_story",
        "calculate_facts",
        "draft_story",
        "critique_story",
        "workflow.wait_condition",
    ):
        assert required in source, required
