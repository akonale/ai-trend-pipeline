"""Time-skipping tests for StoryCandidateWorkflow and candidate search attributes."""

import asyncio
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from temporalio.api.enums.v1 import IndexedValueType
from temporalio.api.operatorservice.v1 import ListSearchAttributesRequest
from temporalio.client import WorkflowHandle
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.service import RPCError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from ai_trend_pipeline.services.temporal_gateway import (
    TemporalCandidateGateway,
    pending_candidates_query,
)
from ai_trend_pipeline.sources import SourceName
from ai_trend_pipeline.story_candidate import (
    CANDIDATE_SOURCE_ATTRIBUTE,
    CANDIDATE_SOURCE_ATTRIBUTE_NAME,
    CANDIDATE_STATUS_ATTRIBUTE,
    CANDIDATE_STATUS_ATTRIBUTE_NAME,
    CANDIDATE_TASK_QUEUE,
    CandidateDecision,
    CandidateReviewState,
    CandidateStatus,
    StoryCandidate,
    StoryCandidateInput,
    StoryCandidateResult,
    StoryCandidateWorkflow,
    candidate_id_for,
)
from ai_trend_pipeline.temporal_client import ensure_candidate_search_attributes

OBSERVED = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def make_candidate() -> StoryCandidate:
    return StoryCandidate.model_validate(
        {
            "source": SourceName.HN,
            "external_id": "123",
            "title": "A story",
            "url": "https://example.com/story",
            "published_at": datetime(2026, 10, 1, 9, 0, tzinfo=UTC),
            "observed_at": OBSERVED,
            "summary": "A short summary",
            "proposed_angle": "Why it matters",
            "selection_reason": "Novel and useful",
            "metrics": {"score": 10},
        }
    )


async def start_env() -> WorkflowEnvironment:
    env = await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)
    await ensure_candidate_search_attributes(env.client)
    return env


async def start_local_env() -> WorkflowEnvironment:
    # The SDK default for search_attributes contains an unresolved generic; typed keys are safe.
    env = await WorkflowEnvironment.start_local(  # pyright: ignore[reportUnknownMemberType]
        data_converter=pydantic_data_converter,
        search_attributes=[CANDIDATE_STATUS_ATTRIBUTE, CANDIDATE_SOURCE_ATTRIBUTE],
    )
    await ensure_candidate_search_attributes(env.client)
    return env


def make_worker(env: WorkflowEnvironment) -> Worker:
    return Worker(
        env.client,
        task_queue=CANDIDATE_TASK_QUEUE,
        workflows=[StoryCandidateWorkflow],
    )


async def start_workflow(
    env: WorkflowEnvironment, review_timeout_seconds: int = 3600
) -> WorkflowHandle[StoryCandidateWorkflow, StoryCandidateResult]:
    return await env.client.start_workflow(
        StoryCandidateWorkflow.run,
        StoryCandidateInput(
            candidate=make_candidate(), review_timeout_seconds=review_timeout_seconds
        ),
        id=f"wf-{uuid.uuid4()}",
        task_queue=CANDIDATE_TASK_QUEUE,
    )


async def wait_for_status(
    handle: WorkflowHandle[StoryCandidateWorkflow, StoryCandidateResult], status: CandidateStatus
) -> CandidateReviewState:
    state: CandidateReviewState | None = None
    for _ in range(200):
        state = await handle.query(StoryCandidateWorkflow.review_state)
        if state.status is status:
            return state
        await asyncio.sleep(0.02)
    raise AssertionError(f"state never reached {status}; last was {state}")


async def signal_quietly(
    handle: WorkflowHandle[StoryCandidateWorkflow, StoryCandidateResult],
    signal: Callable[..., Any],
    decision: CandidateDecision,
) -> None:
    """Send a signal that the server may reject once the workflow has closed."""
    try:
        await handle.signal(signal, decision)
    except RPCError:
        pass


async def wait_for_visibility(
    env: WorkflowEnvironment, query: str, count: int, *, timeout: float = 10.0
) -> list[str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ids = [wf.id async for wf in env.client.list_workflows(query)]
        if len(ids) == count:
            return ids
        await asyncio.sleep(0.1)
    raise AssertionError(f"visibility never returned {count} rows for {query!r}")


@pytest.mark.asyncio
async def test_ensure_candidate_search_attributes_is_idempotent() -> None:
    async with await start_local_env() as env:
        await ensure_candidate_search_attributes(env.client)
        await ensure_candidate_search_attributes(env.client)
        response = await env.client.operator_service.list_search_attributes(
            ListSearchAttributesRequest(namespace=env.client.namespace)
        )
    attributes = dict(response.custom_attributes)
    assert (
        attributes[CANDIDATE_STATUS_ATTRIBUTE_NAME] == IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD
    )
    assert (
        attributes[CANDIDATE_SOURCE_ATTRIBUTE_NAME] == IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD
    )


@pytest.mark.asyncio
async def test_approve_completes_approved_and_sets_terminal_status_attribute() -> None:
    async with await start_env() as env:
        await ensure_candidate_search_attributes(env.client)
        async with make_worker(env):
            handle = await start_workflow(env)
            await wait_for_status(handle, CandidateStatus.AWAITING_REVIEW)
            await handle.signal(
                StoryCandidateWorkflow.approve,
                CandidateDecision(note="ship it", decided_by="alice"),
            )
            result = await handle.result()
            description = await handle.describe()
    assert result.status == "approved"
    assert result.note == "ship it"
    assert result.decided_by == "alice"
    assert description.typed_search_attributes.get(CANDIDATE_STATUS_ATTRIBUTE, None) == "approved"


@pytest.mark.asyncio
async def test_reject_completes_rejected() -> None:
    async with await start_env() as env:
        await ensure_candidate_search_attributes(env.client)
        async with make_worker(env):
            handle = await start_workflow(env)
            await wait_for_status(handle, CandidateStatus.AWAITING_REVIEW)
            await handle.signal(StoryCandidateWorkflow.reject, CandidateDecision(note="no"))
            result = await handle.result()
            final = await handle.query(StoryCandidateWorkflow.review_state)
    assert result.status == "rejected"
    assert result.note == "no"
    assert final.status is CandidateStatus.REJECTED


@pytest.mark.asyncio
async def test_no_decision_expires_after_review_timeout() -> None:
    async with await start_env() as env:
        async with make_worker(env):
            handle = await start_workflow(env, review_timeout_seconds=1)
            result = await handle.result()
            final = await handle.query(StoryCandidateWorkflow.review_state)
    assert result.status == "expired"
    assert result.decided_by is None
    assert final.status is CandidateStatus.EXPIRED
    assert final.decided_at is not None


@pytest.mark.asyncio
async def test_first_terminal_decision_wins() -> None:
    async with await start_env() as env:
        async with make_worker(env):
            approved = await start_workflow(env)
            await wait_for_status(approved, CandidateStatus.AWAITING_REVIEW)
            await approved.signal(StoryCandidateWorkflow.approve, CandidateDecision(note="first"))
            await signal_quietly(
                approved, StoryCandidateWorkflow.reject, CandidateDecision(note="second")
            )
            approved_result = await approved.result()

            rejected = await start_workflow(env)
            await wait_for_status(rejected, CandidateStatus.AWAITING_REVIEW)
            await rejected.signal(StoryCandidateWorkflow.reject, CandidateDecision(note="first"))
            await signal_quietly(
                rejected, StoryCandidateWorkflow.approve, CandidateDecision(note="second")
            )
            rejected_result = await rejected.result()
    assert approved_result.status == "approved"
    assert approved_result.note == "first"
    assert rejected_result.status == "rejected"
    assert rejected_result.note == "first"


@pytest.mark.asyncio
async def test_decisions_after_terminal_or_expiry_are_ignored() -> None:
    async with await start_env() as env:
        async with make_worker(env):
            approved = await start_workflow(env)
            await wait_for_status(approved, CandidateStatus.AWAITING_REVIEW)
            await approved.signal(StoryCandidateWorkflow.approve, CandidateDecision(note="first"))
            approved_result = await approved.result()
            await signal_quietly(
                approved, StoryCandidateWorkflow.reject, CandidateDecision(note="late")
            )
            approved_final = await approved.query(StoryCandidateWorkflow.review_state)

            expired = await start_workflow(env, review_timeout_seconds=1)
            expired_result = await expired.result()
            await signal_quietly(
                expired, StoryCandidateWorkflow.approve, CandidateDecision(note="late")
            )
            expired_final = await expired.query(StoryCandidateWorkflow.review_state)
    assert approved_result.status == "approved"
    assert approved_result.note == "first"
    assert approved_final.status is CandidateStatus.APPROVED
    assert approved_final.note == "first"
    assert expired_result.status == "expired"
    assert expired_final.status is CandidateStatus.EXPIRED
    assert expired_final.note == ""


@pytest.mark.asyncio
async def test_review_state_query_tracks_awaiting_and_final() -> None:
    async with await start_env() as env:
        async with make_worker(env):
            handle = await start_workflow(env)
            awaiting = await wait_for_status(handle, CandidateStatus.AWAITING_REVIEW)
            assert awaiting.candidate == make_candidate()
            assert awaiting.decided_by is None
            assert awaiting.decided_at is None
            await handle.signal(
                StoryCandidateWorkflow.approve,
                CandidateDecision(note="ok", decided_by="alice"),
            )
            await handle.result()
            final = await handle.query(StoryCandidateWorkflow.review_state)
    assert awaiting.note == ""
    assert final.status is CandidateStatus.APPROVED
    assert final.note == "ok"
    assert final.decided_by == "alice"
    assert final.decided_at is not None


@pytest.mark.asyncio
async def test_visibility_status_changes_from_awaiting_to_terminal() -> None:
    async with await start_local_env() as env:
        await ensure_candidate_search_attributes(env.client)
        gateway = TemporalCandidateGateway(env.client)
        candidate = make_candidate()
        candidate_id = candidate_id_for(candidate.source, candidate.external_id)
        async with make_worker(env):
            await gateway.start(candidate, review_timeout_seconds=600)
            assert await wait_for_visibility(env, pending_candidates_query(), 1) == [candidate_id]
            await gateway.handle(candidate_id).approve(CandidateDecision())
            await gateway.handle(candidate_id).result()
            terminal_query = (
                "WorkflowType = 'StoryCandidateWorkflow' AND CandidateStatus = 'approved'"
            )
            assert await wait_for_visibility(env, terminal_query, 1) == [candidate_id]

    assert CANDIDATE_STATUS_ATTRIBUTE.name == CANDIDATE_STATUS_ATTRIBUTE_NAME
    assert CANDIDATE_SOURCE_ATTRIBUTE.name == CANDIDATE_SOURCE_ATTRIBUTE_NAME
