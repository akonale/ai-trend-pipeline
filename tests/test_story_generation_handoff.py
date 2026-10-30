"""start_local handoff tests: candidate approval starts exactly one story."""

import asyncio
import time

import pytest
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.service import RPCError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from ai_trend_pipeline.services.candidates import CandidateNotFound
from ai_trend_pipeline.services.temporal_gateway import (
    TemporalCandidateGateway,
    TemporalCandidateHandle,
)
from ai_trend_pipeline.story_candidate import (
    CANDIDATE_SOURCE_ATTRIBUTE,
    CANDIDATE_STATUS_ATTRIBUTE,
    CANDIDATE_TASK_QUEUE,
    CandidateDecision,
    CandidateReviewState,
    CandidateStatus,
    StoryCandidate,
    StoryCandidateWorkflow,
    candidate_id_for,
    story_input_for,
)
from ai_trend_pipeline.story_generation import (
    STORY_TASK_QUEUE,
    StoryGenerationInput,
    StoryGenerationWorkflow,
    story_id_for,
)
from ai_trend_pipeline.story_visibility import (
    STORY_CANDIDATE_ID_ATTRIBUTE,
    STORY_CREATED_AT_ATTRIBUTE,
    STORY_SOURCE_ATTRIBUTE,
    STORY_STATUS_ATTRIBUTE,
    STORY_STATUS_DETAIL_ATTRIBUTE,
    STORY_TITLE_ATTRIBUTE,
    STORY_UPDATED_AT_ATTRIBUTE,
    story_start_attributes,
)
from ai_trend_pipeline.temporal_client import ensure_story_search_attributes
from tests.story_generation_fixtures import make_candidate

STORY_SEARCH_ATTRIBUTES = [
    CANDIDATE_STATUS_ATTRIBUTE,
    CANDIDATE_SOURCE_ATTRIBUTE,
    STORY_STATUS_ATTRIBUTE,
    STORY_TITLE_ATTRIBUTE,
    STORY_SOURCE_ATTRIBUTE,
    STORY_CANDIDATE_ID_ATTRIBUTE,
    STORY_CREATED_AT_ATTRIBUTE,
    STORY_UPDATED_AT_ATTRIBUTE,
    STORY_STATUS_DETAIL_ATTRIBUTE,
]


async def start_env() -> WorkflowEnvironment:
    # The SDK default for search_attributes contains an unresolved generic; typed keys are safe.
    env = await WorkflowEnvironment.start_local(  # pyright: ignore[reportUnknownMemberType]
        data_converter=pydantic_data_converter,
        search_attributes=STORY_SEARCH_ATTRIBUTES,
    )
    await ensure_story_search_attributes(env.client)
    return env


def make_worker(env: WorkflowEnvironment) -> Worker:
    return Worker(
        env.client,
        task_queue=CANDIDATE_TASK_QUEUE,
        workflows=[StoryCandidateWorkflow],
    )


async def start_candidate(
    env: WorkflowEnvironment,
    candidate: StoryCandidate,
) -> tuple[str, TemporalCandidateHandle]:
    gateway = TemporalCandidateGateway(env.client)
    await gateway.start(candidate, review_timeout_seconds=600)
    candidate_id = candidate_id_for(candidate.source, candidate.external_id)
    return candidate_id, gateway.handle(candidate_id)


async def wait_for_review(
    handle: TemporalCandidateHandle,
    status: CandidateStatus = CandidateStatus.AWAITING_REVIEW,
) -> CandidateReviewState:
    state: CandidateReviewState | None = None
    for _ in range(500):
        state = await handle.review_state()
        if state.status is status:
            return state
        await asyncio.sleep(0.01)
    raise AssertionError(f"candidate never reached {status}; last was {state}")


async def wait_for_visibility(
    env: WorkflowEnvironment, query: str, count: int, *, timeout: float = 15.0
) -> list[str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ids = [workflow.id async for workflow in env.client.list_workflows(query)]
        if len(ids) == count:
            return ids
        await asyncio.sleep(0.05)
    raise AssertionError(f"visibility never returned {count} rows for {query!r}")


async def workflows_of_type(env: WorkflowEnvironment, workflow_type: str) -> list[str]:
    return [
        workflow.id
        async for workflow in env.client.list_workflows(f"WorkflowType = '{workflow_type}'")
    ]


@pytest.mark.asyncio
async def test_approved_candidate_starts_exactly_one_story_and_returns_its_id() -> None:
    async with await start_env() as env, make_worker(env):
        now = await env.get_current_time()
        candidate = make_candidate(observed_at=now)
        candidate_id, handle = await start_candidate(env, candidate)
        await wait_for_review(handle)
        await handle.approve(CandidateDecision(note="ship it"))
        result = await handle.result()
        expected = story_id_for(candidate_id)
        description = await env.client.get_workflow_handle(expected).describe()
        story_ids = await wait_for_visibility(env, "WorkflowType = 'StoryGenerationWorkflow'", 1)
        task_queue = description.raw_description.workflow_execution_info.task_queue
    assert result.story_workflow_id == expected
    assert story_ids == [expected]
    assert task_queue == STORY_TASK_QUEUE


@pytest.mark.asyncio
async def test_duplicate_approval_cannot_start_a_second_story() -> None:
    async with await start_env() as env, make_worker(env):
        now = await env.get_current_time()
        candidate = make_candidate(observed_at=now)
        candidate_id, handle = await start_candidate(env, candidate)
        await wait_for_review(handle)
        await handle.approve(CandidateDecision(note="first"))
        result = await handle.result()
        try:
            await handle.approve(CandidateDecision(note="second"))
        except (RPCError, CandidateNotFound):
            pass
        story_ids = await wait_for_visibility(env, "WorkflowType = 'StoryGenerationWorkflow'", 1)
    assert result.story_workflow_id == story_id_for(candidate_id)
    assert story_ids == [result.story_workflow_id]


@pytest.mark.asyncio
async def test_duplicate_story_start_is_tolerated() -> None:
    async with await start_env() as env, make_worker(env):
        now = await env.get_current_time()
        candidate = make_candidate(observed_at=now)
        candidate_id = candidate_id_for(candidate.source, candidate.external_id)
        story = story_input_for(candidate)
        story_id = story_id_for(candidate_id)
        await env.client.start_workflow(
            StoryGenerationWorkflow.run,
            StoryGenerationInput(
                candidate_id=candidate_id,
                story=story,
                story_created_at=candidate.observed_at,
            ),
            id=story_id,
            task_queue=STORY_TASK_QUEUE,
            search_attributes=story_start_attributes(
                "researching",
                candidate.title,
                candidate.source.value,
                candidate_id,
                candidate.observed_at,
                candidate.observed_at,
            ),
        )
        _candidate_id, handle = await start_candidate(env, candidate)
        await wait_for_review(handle)
        await handle.approve(CandidateDecision())
        result = await handle.result()
        story_ids = await wait_for_visibility(env, "WorkflowType = 'StoryGenerationWorkflow'", 1)
    assert result.story_workflow_id == story_id
    assert story_ids == [story_id]


@pytest.mark.asyncio
async def test_handoff_metadata_is_shared_and_de_duplicable() -> None:
    async with await start_env() as env, make_worker(env):
        now = await env.get_current_time()
        candidate = make_candidate(observed_at=now)
        candidate_id, handle = await start_candidate(env, candidate)
        await wait_for_review(handle)
        await handle.approve(CandidateDecision())
        result = await handle.result()
        story_id = result.story_workflow_id
        assert story_id is not None
        ids = await wait_for_visibility(env, f"StoryCandidateId = '{candidate_id}'", 2)
        descriptions = {
            workflow_id: await env.client.get_workflow_handle(workflow_id).describe()
            for workflow_id in ids
        }
    assert set(descriptions) == {candidate_id, story_id}
    candidate_row = descriptions[candidate_id]
    story_row = descriptions[story_id]
    assert candidate_row.typed_search_attributes.get(STORY_STATUS_ATTRIBUTE, None) == ("approved")
    assert story_row.typed_search_attributes.get(STORY_STATUS_ATTRIBUTE, None) == ("researching")
    for attribute in (STORY_TITLE_ATTRIBUTE, STORY_SOURCE_ATTRIBUTE):
        assert candidate_row.typed_search_attributes.get(attribute, None) == (
            story_row.typed_search_attributes.get(attribute, None)
        )
    created = candidate_row.typed_search_attributes.get(STORY_CREATED_AT_ATTRIBUTE, None)
    assert created == story_row.typed_search_attributes.get(STORY_CREATED_AT_ATTRIBUTE, None)
    assert created == candidate.observed_at
    assert (
        candidate_row.typed_search_attributes.get(STORY_CANDIDATE_ID_ATTRIBUTE, None)
        == candidate_id
    )
    assert story_row.typed_search_attributes.get(STORY_CANDIDATE_ID_ATTRIBUTE, None) == candidate_id


@pytest.mark.asyncio
async def test_rejected_candidate_starts_no_story() -> None:
    async with await start_env() as env, make_worker(env):
        now = await env.get_current_time()
        candidate = make_candidate(observed_at=now)
        _candidate_id, handle = await start_candidate(env, candidate)
        await wait_for_review(handle)
        await handle.reject(CandidateDecision(note="no"))
        result = await handle.result()
        story_ids = await workflows_of_type(env, "StoryGenerationWorkflow")
    assert result.story_workflow_id is None
    assert story_ids == []
