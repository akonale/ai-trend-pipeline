"""CandidateService tests against a fake gateway."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.services.candidates import (
    CandidateClosed,
    CandidateGateway,
    CandidateHandle,
    CandidateNotFound,
    CandidatePage,
    CandidateReviewState,
    CandidateService,
    CandidateSummary,
    DecideCandidateRequest,
    ListCandidatesRequest,
)
from ai_trend_pipeline.sources import SourceName
from ai_trend_pipeline.story_candidate import (
    CandidateDecision,
    CandidateStatus,
    StoryCandidate,
    StoryCandidateResult,
    candidate_id_for,
)

OBSERVED = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def make_candidate(external_id: str = "123") -> StoryCandidate:
    return StoryCandidate(
        source=SourceName.HN,
        external_id=external_id,
        title="A story",
        observed_at=OBSERVED,
        summary="A short summary",
        proposed_angle="Why it matters",
        selection_reason="Novel and useful",
    )


def awaiting_state(candidate: StoryCandidate) -> CandidateReviewState:
    return CandidateReviewState(status=CandidateStatus.AWAITING_REVIEW, candidate=candidate)


def final_state(candidate: StoryCandidate, status: CandidateStatus) -> CandidateReviewState:
    return CandidateReviewState(
        status=status,
        candidate=candidate,
        note="done",
        decided_by="alice",
        decided_at=OBSERVED,
    )


class FakeHandle:
    def __init__(
        self,
        candidate_id: str,
        state: CandidateReviewState | None = None,
        *,
        review_error: Exception | None = None,
        signal_error: Exception | None = None,
        result: StoryCandidateResult | None = None,
    ) -> None:
        self.id = candidate_id
        self.state = state
        self.review_error = review_error
        self.signal_error = signal_error
        self.final_result = result
        self.sent: list[tuple[str, CandidateDecision]] = []

    async def review_state(self) -> CandidateReviewState:
        if self.review_error is not None:
            raise self.review_error
        assert self.state is not None
        return self.state

    async def approve(self, decision: CandidateDecision) -> None:
        self._send("approve", decision)

    async def reject(self, decision: CandidateDecision) -> None:
        self._send("reject", decision)

    def _send(self, name: str, decision: CandidateDecision) -> None:
        if self.signal_error is not None:
            raise self.signal_error
        self.sent.append((name, decision))

    async def result(self) -> StoryCandidateResult:
        assert self.final_result is not None
        return self.final_result


class FakeGateway:
    def __init__(self) -> None:
        self.attempts: list[tuple[StoryCandidate, int]] = []
        self.starts: list[str] = []
        self.list_requests: list[ListCandidatesRequest] = []
        self.page = CandidatePage(candidates=[])
        self.handles: dict[str, FakeHandle] = {}

    async def start(self, candidate: StoryCandidate, review_timeout_seconds: int) -> None:
        self.attempts.append((candidate, review_timeout_seconds))
        candidate_id = candidate_id_for(candidate.source, candidate.external_id)
        if candidate_id not in self.handles:
            self.starts.append(candidate_id)
            self.handles[candidate_id] = FakeHandle(candidate_id, awaiting_state(candidate))

    async def list_pending(self, request: ListCandidatesRequest) -> CandidatePage:
        self.list_requests.append(request)
        return self.page

    def handle(self, candidate_id: str) -> CandidateHandle:
        if candidate_id in self.handles:
            return self.handles[candidate_id]
        return FakeHandle(candidate_id, review_error=CandidateNotFound(candidate_id))


def make_service(gateway: FakeGateway, timeout: int = 172800) -> CandidateService:
    return CandidateService(gateway, Settings(candidate_review_timeout_s=timeout))


@pytest.mark.asyncio
async def test_start_derives_id_and_uses_settings_timeout() -> None:
    gateway = FakeGateway()
    service = make_service(gateway, timeout=1234)
    candidate = make_candidate()
    result = await service.start(candidate)
    assert result.candidate_id == candidate_id_for(candidate.source, candidate.external_id)
    assert gateway.attempts == [(candidate, 1234)]
    await service.start(candidate, review_timeout_seconds=60)
    assert gateway.attempts[-1] == (candidate, 60)


@pytest.mark.asyncio
async def test_start_twice_returns_same_id_and_one_gateway_start() -> None:
    gateway = FakeGateway()
    service = make_service(gateway)
    candidate = make_candidate()
    first = await service.start(candidate)
    second = await service.start(candidate)
    assert first.candidate_id == second.candidate_id
    assert len(gateway.attempts) == 2
    assert gateway.starts == [first.candidate_id]


@pytest.mark.asyncio
async def test_list_pending_passes_limit_and_page_token() -> None:
    gateway = FakeGateway()
    gateway.page = CandidatePage(
        candidates=[
            CandidateSummary(
                candidate_id="candidate-v1-0000000000000000",
                source=SourceName.HN,
                title="A story",
                proposed_angle="Why it matters",
                observed_at=OBSERVED,
                status=CandidateStatus.AWAITING_REVIEW,
            )
        ],
        next_token="next-token",
    )
    service = make_service(gateway)
    request = ListCandidatesRequest(limit=5, page_token="page-token")
    page = await service.list_pending(request)
    assert page == gateway.page
    assert gateway.list_requests == [request]
    with pytest.raises(ValidationError):
        ListCandidatesRequest(limit=101)


@pytest.mark.asyncio
async def test_show_and_decide_raise_typed_errors() -> None:
    gateway = FakeGateway()
    service = make_service(gateway)
    candidate = make_candidate()
    result = await service.start(candidate)
    candidate_id = result.candidate_id

    with pytest.raises(CandidateNotFound):
        await service.show("candidate-v1-ffffffffffffffff")
    with pytest.raises(CandidateNotFound):
        await service.decide(
            DecideCandidateRequest(candidate_id="candidate-v1-ffffffffffffffff", decision="approve")
        )

    gateway.handles[candidate_id].state = final_state(candidate, CandidateStatus.APPROVED)
    with pytest.raises(CandidateClosed) as closed_show:
        await service.show(candidate_id)
    assert closed_show.value.candidate_id == candidate_id
    assert closed_show.value.status == CandidateStatus.APPROVED

    with pytest.raises(CandidateClosed):
        await service.decide(DecideCandidateRequest(candidate_id=candidate_id, decision="reject"))

    gateway.handles[candidate_id].state = awaiting_state(candidate)
    gateway.handles[candidate_id].final_result = StoryCandidateResult(
        status="approved",
        candidate=candidate,
        note="ok",
        decided_by="alice",
        decided_at=OBSERVED,
    )
    decided = await service.decide(
        DecideCandidateRequest(
            candidate_id=candidate_id, decision="approve", note="ok", decided_by="alice"
        )
    )
    assert decided.status == "approved"
    assert gateway.handles[candidate_id].sent == [
        ("approve", CandidateDecision(note="ok", decided_by="alice"))
    ]


@pytest.mark.asyncio
async def test_lost_race_maps_to_candidate_closed() -> None:
    gateway = FakeGateway()
    service = make_service(gateway)
    candidate = make_candidate()
    result = await service.start(candidate)
    handle = gateway.handles[result.candidate_id]
    handle.signal_error = CandidateNotFound(result.candidate_id)
    with pytest.raises(CandidateClosed):
        await service.decide(
            DecideCandidateRequest(candidate_id=result.candidate_id, decision="approve")
        )


def test_protocols_are_structural() -> None:
    gateway: CandidateGateway = FakeGateway()
    handle: CandidateHandle = FakeHandle("id")
    assert gateway is not None
    assert handle.id == "id"
