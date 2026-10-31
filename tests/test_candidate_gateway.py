"""TemporalCandidateGateway tests against a local Temporal dev server."""

import asyncio
import time
from datetime import UTC, datetime, timedelta
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
from ai_trend_pipeline.story_status import STORY_UI_RETENTION
from ai_trend_pipeline.story_visibility import STORY_CREATED_AT_ATTRIBUTE
from ai_trend_pipeline.temporal_client import ensure_candidate_search_attributes

OBSERVED = datetime.now(UTC)


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


def cutoff_now() -> datetime:
    return datetime.now(UTC) - STORY_UI_RETENTION


async def wait_for_pending_count(
    gateway: TemporalCandidateGateway, count: int, *, timeout: float = 10.0
) -> None:
    deadline = time.monotonic() + timeout
    page = await gateway.list_pending(ListCandidatesRequest(limit=100), cutoff=cutoff_now())
    while time.monotonic() < deadline:
        if len(page.candidates) == count:
            return
        await asyncio.sleep(0.1)
        page = await gateway.list_pending(ListCandidatesRequest(limit=100), cutoff=cutoff_now())
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
        page = await gateway.list_pending(ListCandidatesRequest(limit=1), cutoff=cutoff_now())
        for _ in range(5):
            seen.extend(candidate.candidate_id for candidate in page.candidates)
            summaries.extend(page.candidates)
            if page.next_token is None:
                break
            page = await gateway.list_pending(
                ListCandidatesRequest(limit=1, page_token=page.next_token),
                cutoff=cutoff_now(),
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


def test_pending_query_uses_start_time_cutoff() -> None:
    cutoff = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
    assert CANDIDATE_TASK_QUEUE == "story-candidate-task-queue"
    query = pending_candidates_query(cutoff)
    assert query == (
        "WorkflowType = 'StoryCandidateWorkflow' AND "
        "CandidateStatus = 'awaiting_review' AND "
        "StartTime > '2026-10-28T12:00:00Z'"
    )
    assert "StoryCreatedAt" not in query


class EmptyVisibilityIterator:
    current_page: list[WorkflowExecution] = []
    next_page_token: bytes | None = None

    async def fetch_next_page(self) -> None:
        return None


class RecordingVisibilityClient:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def list_workflows(
        self, query: str, *, page_size: int | None = None, next_page_token: bytes | None = None
    ) -> EmptyVisibilityIterator:
        _ = (page_size, next_page_token)
        self.queries.append(query)
        return EmptyVisibilityIterator()


@pytest.mark.asyncio
async def test_list_pending_passes_the_cutoff() -> None:
    cutoff = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
    client = RecordingVisibilityClient()
    gateway = TemporalCandidateGateway(cast(Client, client))

    page = await gateway.list_pending(ListCandidatesRequest(), cutoff=cutoff)

    assert page.candidates == []
    assert client.queries == [pending_candidates_query(cutoff)]
    assert "2026-10-28T12:00:00Z" in client.queries[0]
    assert "StartTime >" in client.queries[0]
    assert "StoryCreatedAt" not in client.queries[0]


class FakeVisibilityExecution:
    def __init__(
        self,
        workflow_id: str,
        *,
        start_time: datetime,
        attributes: dict[object, object],
        memo: dict[str, object] | None = None,
    ) -> None:
        self.id = workflow_id
        self.workflow_type = "StoryCandidateWorkflow"
        self.start_time = start_time
        self.typed_search_attributes = attributes
        self._memo = memo or {}

    async def memo(self) -> dict[str, object]:
        return self._memo


class FixedVisibilityIterator:
    def __init__(self, page: list[FakeVisibilityExecution]) -> None:
        self.current_page = page
        self.next_page_token: bytes | None = None

    async def fetch_next_page(self) -> None:
        return None


class FixedVisibilityClient:
    def __init__(self, page: list[FakeVisibilityExecution]) -> None:
        self.page = page
        self.queries: list[str] = []

    def list_workflows(
        self, query: str, *, page_size: int | None = None, next_page_token: bytes | None = None
    ) -> FixedVisibilityIterator:
        _ = (page_size, next_page_token)
        self.queries.append(query)
        return FixedVisibilityIterator(self.page)


def _candidate_attributes() -> dict[object, object]:
    return {
        CANDIDATE_STATUS_ATTRIBUTE: "awaiting_review",
        CANDIDATE_SOURCE_ATTRIBUTE: "hn",
    }


@pytest.mark.asyncio
async def test_list_pending_includes_pre_053_candidate_and_excludes_the_boundary() -> None:
    now = datetime.now(UTC)
    cutoff = now - STORY_UI_RETENTION
    visible = FakeVisibilityExecution(
        "candidate-v1-visible",
        start_time=now - timedelta(hours=1),
        attributes=_candidate_attributes(),
        memo={"candidate_title": "Detected before 053"},
    )
    boundary = FakeVisibilityExecution(
        "candidate-v1-boundary",
        start_time=now - timedelta(hours=48),
        attributes=_candidate_attributes(),
    )
    client = FixedVisibilityClient([visible, boundary])
    gateway = TemporalCandidateGateway(cast(Client, client))

    page = await gateway.list_pending(ListCandidatesRequest(limit=100), cutoff=cutoff)

    assert [candidate.candidate_id for candidate in page.candidates] == ["candidate-v1-visible"]
    assert len(page.candidates) == 1
    assert page.next_token is None
    assert client.queries == [pending_candidates_query(cutoff)]


@pytest.mark.asyncio
async def test_list_pending_uses_start_time_not_story_created_at() -> None:
    now = datetime.now(UTC)
    cutoff = now - STORY_UI_RETENTION
    only_start = FakeVisibilityExecution(
        "candidate-v1-only-start",
        start_time=now - timedelta(hours=1),
        attributes=_candidate_attributes(),
    )
    old_start_attributes = _candidate_attributes()
    old_start_attributes[STORY_CREATED_AT_ATTRIBUTE] = now - timedelta(hours=1)
    old_start = FakeVisibilityExecution(
        "candidate-v1-old-start",
        start_time=now - timedelta(hours=48),
        attributes=old_start_attributes,
    )
    client = FixedVisibilityClient([only_start, old_start])
    gateway = TemporalCandidateGateway(cast(Client, client))

    page = await gateway.list_pending(ListCandidatesRequest(limit=100), cutoff=cutoff)

    assert [candidate.candidate_id for candidate in page.candidates] == ["candidate-v1-only-start"]


class FailingVisibilityClient:
    def list_workflows(
        self, query: str, *, page_size: int | None = None, next_page_token: bytes | None = None
    ) -> None:
        raise RPCError("visibility unavailable", RPCStatusCode.UNAVAILABLE, b"unavailable")


@pytest.mark.asyncio
async def test_list_pending_maps_rpc_error_to_unavailable() -> None:
    gateway = TemporalCandidateGateway(cast(Client, FailingVisibilityClient()))
    with pytest.raises(CandidateUnavailable):
        await gateway.list_pending(ListCandidatesRequest(), cutoff=cutoff_now())
