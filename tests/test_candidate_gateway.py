"""TemporalCandidateGateway tests against a local Temporal dev server."""

import asyncio
import time
from datetime import UTC, datetime
from typing import cast

import pytest
from temporalio.client import Client, WorkflowExecution
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.service import RPCError, RPCStatusCode
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from ai_trend_pipeline.config import load_settings
from ai_trend_pipeline.services.candidates import (
    CandidateClosed,
    CandidateNotFound,
    CandidateService,
    CandidateSummary,
    CandidateUnavailable,
    DecideCandidateRequest,
    ListCandidatesRequest,
)
from ai_trend_pipeline.services.temporal_gateway import (
    TemporalCandidateGateway,
    pending_candidates_query,
)
from ai_trend_pipeline.sources import SourceName
from ai_trend_pipeline.story_candidate import (
    CANDIDATE_SOURCE_ATTRIBUTE,
    CANDIDATE_STATUS_ATTRIBUTE,
    CANDIDATE_TASK_QUEUE,
    CandidateDecision,
    CandidateStatus,
    StoryCandidate,
    StoryCandidateWorkflow,
    candidate_id_for,
)
from ai_trend_pipeline.temporal_client import ensure_candidate_search_attributes

OBSERVED = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def make_candidate(external_id: str = "1") -> StoryCandidate:
    return StoryCandidate.model_validate(
        {
            "source": SourceName.HN,
            "external_id": external_id,
            "title": f"Story {external_id}",
            "url": "https://example.com/story",
            "observed_at": OBSERVED,
            "summary": "A short summary",
            "proposed_angle": "Why it matters",
            "selection_reason": "Novel and useful",
        }
    )


async def start_env() -> WorkflowEnvironment:
    # The SDK default for search_attributes contains an unresolved generic; typed keys are safe.
    env = await WorkflowEnvironment.start_local(  # pyright: ignore[reportUnknownMemberType]
        data_converter=pydantic_data_converter,
        search_attributes=[CANDIDATE_STATUS_ATTRIBUTE, CANDIDATE_SOURCE_ATTRIBUTE],
    )
    await ensure_candidate_search_attributes(env.client)
    return env


def make_worker(env: WorkflowEnvironment) -> Worker:
    return Worker(env.client, task_queue=CANDIDATE_TASK_QUEUE, workflows=[StoryCandidateWorkflow])


async def wait_for_rows(
    env: WorkflowEnvironment, query: str, count: int, *, timeout: float = 10.0
) -> list[WorkflowExecution]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = [wf async for wf in env.client.list_workflows(query)]
        if len(rows) == count:
            return rows
        await asyncio.sleep(0.1)
    raise AssertionError(f"visibility never returned {count} rows for {query!r}")


async def wait_for_pending_count(
    gateway: TemporalCandidateGateway, count: int, *, timeout: float = 10.0
) -> None:
    deadline = time.monotonic() + timeout
    page = await gateway.list_pending(ListCandidatesRequest(limit=100))
    while time.monotonic() < deadline:
        if len(page.candidates) == count:
            return
        await asyncio.sleep(0.1)
        page = await gateway.list_pending(ListCandidatesRequest(limit=100))
    raise AssertionError(f"pending candidates never reached {count}: {page}")


@pytest.mark.asyncio
async def test_start_is_idempotent_for_same_source_and_external_id() -> None:
    candidate = make_candidate()
    candidate_id = candidate_id_for(candidate.source, candidate.external_id)
    async with await start_env() as env, make_worker(env):
        gateway = TemporalCandidateGateway(env.client)
        await gateway.start(candidate, review_timeout_seconds=3600)
        await gateway.start(candidate, review_timeout_seconds=3600)
        rows = await wait_for_rows(env, f"WorkflowId = '{candidate_id}'", 1)
        state = await gateway.handle(candidate_id).review_state()
        memo = await rows[0].memo()
    assert state.status is CandidateStatus.AWAITING_REVIEW
    assert state.candidate == candidate
    assert rows[0].id == candidate_id
    assert memo["candidate_title"] == candidate.title
    assert memo["candidate_proposed_angle"] == candidate.proposed_angle
    assert memo["candidate_observed_at"] == OBSERVED.isoformat()


@pytest.mark.asyncio
async def test_start_after_close_returns_existing_candidate() -> None:
    candidate = make_candidate("2")
    candidate_id = candidate_id_for(candidate.source, candidate.external_id)
    async with await start_env() as env, make_worker(env):
        gateway = TemporalCandidateGateway(env.client)
        await gateway.start(candidate, review_timeout_seconds=3600)
        await gateway.handle(candidate_id).approve(CandidateDecision(note="done"))
        result = await gateway.handle(candidate_id).result()
        await gateway.start(candidate, review_timeout_seconds=3600)
        rows = await wait_for_rows(env, f"WorkflowId = '{candidate_id}'", 1)
        state = await gateway.handle(candidate_id).review_state()
    assert result.status == "approved"
    assert state.status is CandidateStatus.APPROVED
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_list_pending_paginates_and_excludes_decided() -> None:
    candidates = [make_candidate(str(i)) for i in range(1, 4)]
    decided = candidates[2]
    decided_id = candidate_id_for(decided.source, decided.external_id)
    async with await start_env() as env, make_worker(env):
        gateway = TemporalCandidateGateway(env.client)
        for candidate in candidates:
            await gateway.start(candidate, review_timeout_seconds=3600)
        await gateway.handle(decided_id).approve(CandidateDecision())
        await gateway.handle(decided_id).result()
        await wait_for_pending_count(gateway, 2)

        seen: list[str] = []
        summaries: list[CandidateSummary] = []
        page = await gateway.list_pending(ListCandidatesRequest(limit=1))
        for _ in range(5):
            seen.extend(candidate.candidate_id for candidate in page.candidates)
            summaries.extend(page.candidates)
            if page.next_token is None:
                break
            page = await gateway.list_pending(
                ListCandidatesRequest(limit=1, page_token=page.next_token)
            )
    assert len(seen) == 2
    assert set(seen) == {
        candidate_id_for(candidate.source, candidate.external_id) for candidate in candidates[:2]
    }
    assert decided_id not in seen
    assert all(candidate.status is CandidateStatus.AWAITING_REVIEW for candidate in summaries)
    assert all(candidate.observed_at == OBSERVED for candidate in summaries)


@pytest.mark.asyncio
async def test_show_maps_missing_and_closed_to_typed_errors() -> None:
    candidate = make_candidate("4")
    candidate_id = candidate_id_for(candidate.source, candidate.external_id)
    async with await start_env() as env, make_worker(env):
        gateway = TemporalCandidateGateway(env.client)
        service = CandidateService(gateway, load_settings({}))
        with pytest.raises(CandidateNotFound):
            await service.show("candidate-v1-0000000000000000")
        await gateway.start(candidate, review_timeout_seconds=3600)
        await gateway.handle(candidate_id).reject(CandidateDecision(note="no"))
        await gateway.handle(candidate_id).result()
        with pytest.raises(CandidateClosed) as closed:
            await service.show(candidate_id)
    assert closed.value.status is CandidateStatus.REJECTED


@pytest.mark.asyncio
async def test_decide_maps_missing_and_closed_and_returns_result() -> None:
    candidate = make_candidate("5")
    candidate_id = candidate_id_for(candidate.source, candidate.external_id)
    async with await start_env() as env, make_worker(env):
        gateway = TemporalCandidateGateway(env.client)
        service = CandidateService(gateway, load_settings({}))
        with pytest.raises(CandidateNotFound):
            await service.decide(
                DecideCandidateRequest(
                    candidate_id="candidate-v1-0000000000000000", decision="approve"
                )
            )
        await gateway.start(candidate, review_timeout_seconds=3600)
        result = await service.decide(
            DecideCandidateRequest(
                candidate_id=candidate_id, decision="approve", note="ok", decided_by="alice"
            )
        )
        with pytest.raises(CandidateClosed):
            await service.decide(
                DecideCandidateRequest(candidate_id=candidate_id, decision="reject")
            )
    assert result.status == "approved"
    assert result.note == "ok"
    assert result.decided_by == "alice"


def test_pending_query_mentions_workflow_type_and_status() -> None:
    assert CANDIDATE_TASK_QUEUE == "story-candidate-task-queue"
    assert pending_candidates_query() == (
        "WorkflowType = 'StoryCandidateWorkflow' AND CandidateStatus = 'awaiting_review'"
    )


class FailingVisibilityClient:
    def list_workflows(
        self, query: str, *, page_size: int | None = None, next_page_token: bytes | None = None
    ) -> None:
        raise RPCError("visibility unavailable", RPCStatusCode.UNAVAILABLE, b"unavailable")


@pytest.mark.asyncio
async def test_list_pending_maps_rpc_error_to_unavailable() -> None:
    gateway = TemporalCandidateGateway(cast(Client, FailingVisibilityClient()))
    with pytest.raises(CandidateUnavailable):
        await gateway.list_pending(ListCandidatesRequest())
