"""StoryDetailService tests with a fake gateway; fixed aware clock, no Temporal."""

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode

from ai_trend_pipeline.services.stories import (
    GENERATION_STARTING_DETAIL,
    STORY_ID_PREFIX,
    DecideStoryRequest,
    StoryDecisionResult,
    StoryDetailExpired,
    StoryDetailGateway,
    StoryDetailKind,
    StoryDetailMalformed,
    StoryDetailNotFound,
    StoryDetailService,
    StoryDetailUnavailable,
    StoryNotReviewable,
    StoryReviewStale,
    story_detail_kind,
)
from ai_trend_pipeline.services.temporal_gateway import TemporalStoryDetailGateway
from ai_trend_pipeline.sources import SourceName
from ai_trend_pipeline.story_candidate import (
    CandidateReviewState,
    CandidateStatus,
    StoryCandidate,
    StoryCandidateResult,
)
from ai_trend_pipeline.story_drafting.models import StoryPackage
from ai_trend_pipeline.story_generation import (
    FinalStoryDecision,
    StoryGenerationResult,
    StoryGenerationState,
    StoryGenerationStatus,
)
from ai_trend_pipeline.story_status import PrimaryStoryStatus

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
OBSERVED = NOW - timedelta(hours=2)
CANDIDATE_ID = "candidate-v1-0123456789abcdef"
STORY_ID = "story-v1-fedcba9876543210"
FACT_A = "fact-v1-0000000000000001"
FACT_B = "fact-v1-0000000000000002"
HASH = "c" * 64

OPEN_STORY_STATES = (
    StoryGenerationStatus.RESEARCHING,
    StoryGenerationStatus.ANALYZING,
    StoryGenerationStatus.WRITING,
    StoryGenerationStatus.CHECKING,
)
TERMINAL_STORY_STATES = (
    StoryGenerationStatus.REJECTED,
    StoryGenerationStatus.UNSUPPORTED,
    StoryGenerationStatus.FAILED,
    StoryGenerationStatus.EXPIRED,
)


def make_candidate(
    *,
    title: str = "Rates hold steady",
    source: SourceName = SourceName.HN,
    observed_at: datetime = OBSERVED,
) -> StoryCandidate:
    return StoryCandidate.model_validate(
        {
            "source": source,
            "external_id": "story-1",
            "title": title,
            "url": "https://example.com/story",
            "observed_at": observed_at,
            "summary": "The central bank paused its rate rises.",
            "proposed_angle": "What the pause means.",
            "selection_reason": "It affects a large audience.",
            "metrics": {},
        }
    )


def make_package(**overrides: object) -> StoryPackage:
    data: dict[str, object] = {
        "story_id": STORY_ID,
        "headline": "Rates hold steady again",
        "summary": "The central bank paused its rate rises for a third time.",
        "body": "First paragraph.\n\nSecond paragraph.",
        "key_points": [
            {"text": "Rates did not change.", "fact_ids": [FACT_A]},
            {"text": "This is the third pause.", "fact_ids": [FACT_A, FACT_B]},
        ],
        "source_links": [
            {
                "evidence_id": "ev-1",
                "label": "Original report",
                "url": "https://example.com/report",
            }
        ],
        "caveats": ["The figure is provisional."],
        "post_text": "Rates held steady again.",
    }
    data.update(overrides)
    return StoryPackage.model_validate(data)


def candidate_state(
    status: CandidateStatus = CandidateStatus.AWAITING_REVIEW,
    *,
    story_workflow_id: str | None = None,
    title: str = "Rates hold steady",
    source: SourceName = SourceName.HN,
    observed_at: datetime = OBSERVED,
    note: str = "",
    decided_by: str | None = None,
    decided_at: datetime | None = None,
) -> CandidateReviewState:
    return CandidateReviewState(
        status=status,
        candidate=make_candidate(title=title, source=source, observed_at=observed_at),
        note=note,
        decided_by=decided_by,
        decided_at=decided_at,
        story_workflow_id=story_workflow_id,
    )


def story_state(
    status: StoryGenerationStatus = StoryGenerationStatus.RESEARCHING,
    *,
    package: StoryPackage | None = None,
    created_at: datetime | None = OBSERVED,
    candidate_id: str = CANDIDATE_ID,
    story_id: str = STORY_ID,
    detail: str | None = None,
    note: str = "",
    decided_by: str | None = None,
    decided_at: datetime | None = None,
    content_hash: str | None = None,
) -> StoryGenerationState:
    if content_hash is None and status is StoryGenerationStatus.REVIEW:
        content_hash = HASH
    return StoryGenerationState(
        story_id=story_id,
        candidate_id=candidate_id,
        story_created_at=created_at,
        updated_at=NOW,
        status=status,
        status_detail=detail,
        package=package,
        content_hash=content_hash,
        decided_by=decided_by,
        note=note,
        decided_at=decided_at,
    )


def story_result(
    status: str = "ready",
    *,
    package: StoryPackage | None = None,
    created_at: datetime = OBSERVED,
    candidate_id: str = CANDIDATE_ID,
    story_id: str = STORY_ID,
    detail: str | None = None,
    note: str = "",
    decided_by: str | None = None,
    decided_at: datetime | None = None,
) -> StoryGenerationResult:
    return StoryGenerationResult.model_validate(
        {
            "status": status,
            "story_id": story_id,
            "candidate_id": candidate_id,
            "story_created_at": created_at,
            "updated_at": NOW,
            "status_detail": detail,
            "package": package,
            "content_hash": HASH if status == "ready" else None,
            "critique": None,
            "decided_by": decided_by,
            "note": note,
            "decided_at": decided_at,
        }
    )


def candidate_result(
    status: str = "rejected",
    *,
    title: str = "Rates hold steady",
    source: SourceName = SourceName.HN,
    note: str = "",
    decided_by: str | None = None,
    decided_at: datetime | None = None,
) -> StoryCandidateResult:
    return StoryCandidateResult(
        status=status,  # pyright: ignore[reportArgumentType]
        candidate=make_candidate(title=title, source=source),
        note=note,
        decided_by=decided_by,
        decided_at=decided_at,
    )


class FakeGateway:
    """A recording StoryDetailGateway with per-workflow canned values or exceptions."""

    def __init__(self) -> None:
        self.candidate_states: dict[str, CandidateReviewState | Exception] = {}
        self.candidate_results: dict[str, StoryCandidateResult | Exception] = {}
        self.story_states: dict[str, StoryGenerationState | Exception] = {}
        self.story_results: dict[str, StoryGenerationResult | Exception] = {}
        self.calls: list[tuple[str, str]] = []
        self.signals: list[tuple[str, bool, FinalStoryDecision]] = []

    async def candidate_state(self, workflow_id: str) -> CandidateReviewState:
        self.calls.append(("candidate_state", workflow_id))
        value = self.candidate_states[workflow_id]
        if isinstance(value, Exception):
            raise value
        return value

    async def candidate_result(self, workflow_id: str) -> StoryCandidateResult:
        self.calls.append(("candidate_result", workflow_id))
        value = self.candidate_results[workflow_id]
        if isinstance(value, Exception):
            raise value
        return value

    async def story_state(self, workflow_id: str) -> StoryGenerationState:
        self.calls.append(("story_state", workflow_id))
        value = self.story_states[workflow_id]
        if isinstance(value, Exception):
            raise value
        return value

    async def story_result(self, workflow_id: str) -> StoryGenerationResult:
        self.calls.append(("story_result", workflow_id))
        value = self.story_results[workflow_id]
        if isinstance(value, Exception):
            raise value
        return value

    async def decide(
        self, workflow_id: str, decision: FinalStoryDecision, *, approve: bool
    ) -> None:
        self.calls.append(("decide", workflow_id))
        self.signals.append((workflow_id, approve, decision))


def service_with(gateway: FakeGateway) -> StoryDetailService:
    return StoryDetailService(cast(StoryDetailGateway, gateway))


def calls_named(gateway: FakeGateway, name: str) -> list[tuple[str, str]]:
    return [call for call in gateway.calls if call[0] == name]


@pytest.mark.asyncio
async def test_candidate_pending_maps_to_pending_without_package() -> None:
    gateway = FakeGateway()
    gateway.candidate_states[CANDIDATE_ID] = candidate_state()
    detail = await service_with(gateway).show(CANDIDATE_ID, now=NOW)

    assert detail.detail_id == CANDIDATE_ID
    assert detail.workflow_id == CANDIDATE_ID
    assert detail.candidate_id == CANDIDATE_ID
    assert detail.kind is StoryDetailKind.CANDIDATE
    assert detail.status is PrimaryStoryStatus.PENDING
    assert detail.internal_status == "awaiting_review"
    assert detail.title == "Rates hold steady"
    assert detail.source == "hn"
    assert detail.created_at == OBSERVED
    assert detail.package is None
    assert calls_named(gateway, "candidate_result") == []


@pytest.mark.asyncio
async def test_candidate_approved_follows_the_generation_workflow() -> None:
    package = make_package()
    gateway = FakeGateway()
    gateway.candidate_states[CANDIDATE_ID] = candidate_state(
        CandidateStatus.APPROVED, story_workflow_id=STORY_ID
    )
    gateway.story_states[STORY_ID] = story_state(StoryGenerationStatus.RESEARCHING, package=package)

    detail = await service_with(gateway).show(CANDIDATE_ID, now=NOW)

    assert detail.detail_id == CANDIDATE_ID
    assert detail.workflow_id == STORY_ID
    assert detail.kind is StoryDetailKind.GENERATION
    assert detail.status is PrimaryStoryStatus.WRITING
    assert detail.package is None
    assert [name for name, _ in gateway.calls] == ["candidate_state", "story_state"]


@pytest.mark.asyncio
async def test_candidate_approved_without_story_is_writing_with_starting_detail() -> None:
    gateway = FakeGateway()
    gateway.candidate_states[CANDIDATE_ID] = candidate_state(CandidateStatus.APPROVED)

    detail = await service_with(gateway).show(CANDIDATE_ID, now=NOW)

    assert detail.status is PrimaryStoryStatus.WRITING
    assert detail.workflow_id == CANDIDATE_ID
    assert detail.status_detail == GENERATION_STARTING_DETAIL
    assert calls_named(gateway, "story_state") == []
    assert calls_named(gateway, "story_result") == []


@pytest.mark.asyncio
async def test_approved_candidate_with_a_missing_child_stays_writing() -> None:
    gateway = FakeGateway()
    gateway.candidate_states[CANDIDATE_ID] = candidate_state(
        CandidateStatus.APPROVED, story_workflow_id=STORY_ID
    )
    gateway.story_states[STORY_ID] = StoryDetailNotFound(STORY_ID)

    detail = await service_with(gateway).show(CANDIDATE_ID, now=NOW)

    assert detail.status is PrimaryStoryStatus.WRITING
    assert detail.workflow_id == STORY_ID
    assert detail.status_detail == GENERATION_STARTING_DETAIL


@pytest.mark.asyncio
async def test_open_story_states_use_the_query_and_only_review_has_a_package() -> None:
    package = make_package()
    for status in OPEN_STORY_STATES:
        gateway = FakeGateway()
        gateway.story_states[STORY_ID] = story_state(status, package=package)
        gateway.candidate_states[CANDIDATE_ID] = candidate_state(CandidateStatus.APPROVED)
        detail = await service_with(gateway).show(STORY_ID, now=NOW)

        assert detail.status is PrimaryStoryStatus.WRITING, status
        assert detail.package is None, status
        assert calls_named(gateway, "story_result") == [], status

    gateway = FakeGateway()
    gateway.story_states[STORY_ID] = story_state(StoryGenerationStatus.REVIEW, package=package)
    gateway.candidate_states[CANDIDATE_ID] = candidate_state(CandidateStatus.APPROVED)
    review = await service_with(gateway).show(STORY_ID, now=NOW)

    assert review.status is PrimaryStoryStatus.REVIEW
    assert review.package == package
    assert calls_named(gateway, "story_result") == []


@pytest.mark.asyncio
async def test_ready_uses_the_typed_result_and_returns_the_package() -> None:
    package = make_package()
    decided_at = NOW - timedelta(minutes=5)
    gateway = FakeGateway()
    gateway.story_states[STORY_ID] = story_state(
        StoryGenerationStatus.READY,
        package=None,
        decided_by="editor@example.com",
        decided_at=decided_at,
    )
    gateway.candidate_states[CANDIDATE_ID] = candidate_state(CandidateStatus.APPROVED)
    gateway.story_results[STORY_ID] = story_result(
        "ready", package=package, decided_by="editor@example.com", decided_at=decided_at
    )

    detail = await service_with(gateway).show(STORY_ID, now=NOW)

    assert detail.status is PrimaryStoryStatus.READY
    assert detail.internal_status == "ready"
    assert detail.package == package
    assert detail.decided_by == "editor@example.com"
    assert detail.decided_at == decided_at
    assert detail.title == "Rates hold steady"
    assert detail.source == "hn"
    assert len(calls_named(gateway, "story_result")) == 1
    assert [name for name, _ in gateway.calls] == [
        "story_state",
        "candidate_state",
        "story_result",
    ]


@pytest.mark.asyncio
async def test_each_closed_reason_maps_to_closed() -> None:
    for status in TERMINAL_STORY_STATES:
        gateway = FakeGateway()
        gateway.story_states[STORY_ID] = story_state(
            status, package=make_package(), detail="  raw\t detail  "
        )
        gateway.candidate_states[CANDIDATE_ID] = candidate_state(CandidateStatus.APPROVED)
        # A rejected story still carries the package; the detail view must not show it.
        gateway.story_results[STORY_ID] = story_result(
            status.value,
            package=make_package(),
            detail="  raw\t detail  ",
            note="  look closer ",
            decided_by="editor@example.com",
            decided_at=NOW,
        )

        detail = await service_with(gateway).show(STORY_ID, now=NOW)

        assert detail.status is PrimaryStoryStatus.CLOSED, status
        assert detail.internal_status == status.value, status
        assert detail.package is None, status
        assert detail.status_detail == "raw detail", status
        assert detail.note == "look closer", status

    for status in (CandidateStatus.REJECTED, CandidateStatus.EXPIRED):
        gateway = FakeGateway()
        gateway.candidate_states[CANDIDATE_ID] = candidate_state(status)
        gateway.candidate_results[CANDIDATE_ID] = candidate_result(
            status.value,
            title="Candidate title",
            source=SourceName.FAKE,
            note="  not now ",
            decided_by="editor@example.com",
            decided_at=NOW,
        )

        detail = await service_with(gateway).show(CANDIDATE_ID, now=NOW)

        assert detail.status is PrimaryStoryStatus.CLOSED, status
        assert detail.internal_status == status.value, status
        assert detail.kind is StoryDetailKind.CANDIDATE
        assert detail.package is None
        assert detail.title == "Candidate title"
        assert detail.source == "fake"
        assert detail.note == "not now"
        assert detail.decided_by == "editor@example.com"


def test_unknown_or_missing_id_is_not_found() -> None:
    for bad in ("", " " * 3, "x" * 129, "story-v2-abc", "random-id"):
        with pytest.raises(StoryDetailNotFound):
            story_detail_kind(bad)
    assert story_detail_kind(CANDIDATE_ID) is StoryDetailKind.CANDIDATE
    assert story_detail_kind(STORY_ID) is StoryDetailKind.GENERATION
    assert story_detail_kind("candidate-v1-" + "a" * 115) is StoryDetailKind.CANDIDATE
    with pytest.raises(StoryDetailNotFound):
        story_detail_kind("candidate-v1-" + "a" * 116)


@pytest.mark.asyncio
async def test_gateway_not_found_and_rpc_errors_map_to_typed_errors() -> None:
    gateway = FakeGateway()
    gateway.candidate_states[CANDIDATE_ID] = StoryDetailNotFound(CANDIDATE_ID)
    with pytest.raises(StoryDetailNotFound):
        await service_with(gateway).show(CANDIDATE_ID, now=NOW)

    failing = TemporalStoryDetailGateway(
        cast(Client, FakeHandleClient(RPCError("gone", RPCStatusCode.NOT_FOUND, b"gone")))
    )
    with pytest.raises(StoryDetailNotFound):
        await failing.candidate_state(CANDIDATE_ID)

    unavailable = TemporalStoryDetailGateway(
        cast(Client, FakeHandleClient(RPCError("down", RPCStatusCode.UNAVAILABLE, b"down")))
    )
    with pytest.raises(StoryDetailUnavailable):
        await unavailable.story_state(STORY_ID)

    malformed = TemporalStoryDetailGateway(cast(Client, FakeHandleClient(ValueError("decode"))))
    with pytest.raises(StoryDetailMalformed):
        await malformed.story_result(STORY_ID)
    with pytest.raises(StoryDetailMalformed):
        await malformed.candidate_result(CANDIDATE_ID)


@pytest.mark.asyncio
async def test_cutoff_is_strict_for_every_state() -> None:
    visible = NOW - timedelta(hours=47, minutes=59, seconds=59)
    hidden = NOW - timedelta(hours=48)

    gateway = FakeGateway()
    gateway.candidate_states[CANDIDATE_ID] = candidate_state(observed_at=visible)
    detail = await service_with(gateway).show(CANDIDATE_ID, now=NOW)
    assert detail.status is PrimaryStoryStatus.PENDING

    gateway = FakeGateway()
    gateway.story_states[STORY_ID] = story_state(StoryGenerationStatus.REVIEW, created_at=visible)
    gateway.candidate_states[CANDIDATE_ID] = candidate_state(CandidateStatus.APPROVED)
    detail = await service_with(gateway).show(STORY_ID, now=NOW)
    assert detail.status is PrimaryStoryStatus.REVIEW

    gateway = FakeGateway()
    gateway.candidate_states[CANDIDATE_ID] = candidate_state(observed_at=hidden)
    with pytest.raises(StoryDetailExpired):
        await service_with(gateway).show(CANDIDATE_ID, now=NOW)
    assert calls_named(gateway, "candidate_result") == []

    gateway = FakeGateway()
    gateway.story_states[STORY_ID] = story_state(StoryGenerationStatus.READY, created_at=hidden)
    gateway.candidate_states[CANDIDATE_ID] = candidate_state(CandidateStatus.APPROVED)
    gateway.story_results[STORY_ID] = story_result(
        "ready", package=make_package(), created_at=hidden
    )
    with pytest.raises(StoryDetailExpired):
        await service_with(gateway).show(STORY_ID, now=NOW)
    assert calls_named(gateway, "story_result") == []


@pytest.mark.asyncio
async def test_candidate_metadata_fallback_does_not_fail_the_story() -> None:
    package = make_package()
    gateway = FakeGateway()
    gateway.story_states[STORY_ID] = story_state(StoryGenerationStatus.REVIEW, package=package)
    gateway.candidate_states[CANDIDATE_ID] = StoryDetailNotFound(CANDIDATE_ID)

    detail = await service_with(gateway).show(STORY_ID, now=NOW)

    assert detail.title == ""
    assert detail.source == ""
    assert detail.package == package

    gateway = FakeGateway()
    gateway.story_states[STORY_ID] = story_state(StoryGenerationStatus.REVIEW, package=package)
    gateway.candidate_states[CANDIDATE_ID] = StoryDetailUnavailable()
    with pytest.raises(StoryDetailUnavailable):
        await service_with(gateway).show(STORY_ID, now=NOW)


@pytest.mark.asyncio
async def test_decide_sends_one_final_decision_and_returns_the_result() -> None:
    package = make_package()
    gateway = FakeGateway()
    gateway.story_states[STORY_ID] = story_state(StoryGenerationStatus.REVIEW)
    gateway.story_results[STORY_ID] = story_result(
        "ready", package=package, decided_by="editor@example.com", decided_at=NOW
    )

    result = await service_with(gateway).decide(
        DecideStoryRequest(
            detail_id=STORY_ID,
            decision="approve",
            content_hash=HASH,
            decided_by="editor@example.com",
        ),
        now=NOW,
    )

    assert result == StoryDecisionResult(
        workflow_id=STORY_ID, status="ready", decided_by="editor@example.com", decided_at=NOW
    )
    assert len(gateway.signals) == 1
    workflow_id, approve, decision = gateway.signals[0]
    assert workflow_id == STORY_ID
    assert approve is True
    assert decision.decided_by == "editor@example.com"

    rejecting = FakeGateway()
    rejecting.story_states[STORY_ID] = story_state(StoryGenerationStatus.REVIEW)
    rejecting.story_results[STORY_ID] = story_result("rejected")
    rejected = await service_with(rejecting).decide(
        DecideStoryRequest(detail_id=STORY_ID, decision="reject"), now=NOW
    )
    assert rejected.status == "rejected"
    assert [approve for _, approve, _ in rejecting.signals] == [False]


@pytest.mark.asyncio
async def test_decide_rejects_candidate_ids_and_non_review_states() -> None:
    gateway = FakeGateway()
    gateway.candidate_states[CANDIDATE_ID] = candidate_state(
        CandidateStatus.APPROVED, story_workflow_id=STORY_ID
    )
    with pytest.raises(StoryNotReviewable):
        await service_with(gateway).decide(
            DecideStoryRequest(detail_id=CANDIDATE_ID, decision="approve", content_hash=HASH),
            now=NOW,
        )
    assert gateway.signals == []
    assert calls_named(gateway, "candidate_state") == []

    for status in (*OPEN_STORY_STATES, *TERMINAL_STORY_STATES):
        gateway = FakeGateway()
        gateway.story_states[STORY_ID] = story_state(status)
        with pytest.raises(StoryNotReviewable):
            await service_with(gateway).decide(
                DecideStoryRequest(detail_id=STORY_ID, decision="approve", content_hash=HASH),
                now=NOW,
            )
        assert gateway.signals == [], status
        assert calls_named(gateway, "story_result") == [], status


@pytest.mark.asyncio
async def test_decide_after_the_cutoff_sends_no_signal() -> None:
    gateway = FakeGateway()
    gateway.story_states[STORY_ID] = story_state(
        StoryGenerationStatus.REVIEW, created_at=NOW - timedelta(hours=48)
    )
    with pytest.raises(StoryDetailExpired):
        await service_with(gateway).decide(
            DecideStoryRequest(detail_id=STORY_ID, decision="approve", content_hash=HASH), now=NOW
        )
    assert gateway.signals == []
    assert calls_named(gateway, "story_result") == []

    gateway = FakeGateway()
    gateway.story_states[STORY_ID] = story_state(
        StoryGenerationStatus.REVIEW,
        created_at=NOW - timedelta(hours=47, minutes=59, seconds=59),
    )
    gateway.story_results[STORY_ID] = story_result("ready", package=make_package())
    await service_with(gateway).decide(
        DecideStoryRequest(detail_id=STORY_ID, decision="approve", content_hash=HASH), now=NOW
    )
    assert len(gateway.signals) == 1


@pytest.mark.asyncio
async def test_repeated_decision_returns_the_first_terminal_status() -> None:
    gateway = FakeGateway()
    gateway.story_states[STORY_ID] = story_state(StoryGenerationStatus.REVIEW)
    gateway.story_results[STORY_ID] = story_result("ready", package=make_package())
    service = service_with(gateway)

    first = await service.decide(
        DecideStoryRequest(detail_id=STORY_ID, decision="approve", content_hash=HASH), now=NOW
    )
    assert first.status == "ready"

    # The workflow recorded the first decision; the query now reports the terminal state.
    gateway.story_states[STORY_ID] = story_state(StoryGenerationStatus.READY)
    with pytest.raises(StoryNotReviewable):
        await service.decide(DecideStoryRequest(detail_id=STORY_ID, decision="reject"), now=NOW)

    assert len(gateway.signals) == 1
    _, approve, _ = gateway.signals[0]
    assert approve is True
    assert first.status == "ready"


@pytest.mark.asyncio
async def test_malformed_query_or_result_raises_story_detail_malformed() -> None:
    gateway = FakeGateway()
    gateway.story_states[STORY_ID] = story_state(StoryGenerationStatus.RESEARCHING, created_at=None)
    with pytest.raises(StoryDetailMalformed):
        await service_with(gateway).show(STORY_ID, now=NOW)

    gateway = FakeGateway()
    gateway.story_states[STORY_ID] = story_state(StoryGenerationStatus.READY)
    gateway.candidate_states[CANDIDATE_ID] = candidate_state(CandidateStatus.APPROVED)
    gateway.story_results[STORY_ID] = story_result("ready", package=None)
    with pytest.raises(StoryDetailMalformed):
        await service_with(gateway).show(STORY_ID, now=NOW)

    gateway = FakeGateway()
    gateway.story_states[STORY_ID] = story_state(StoryGenerationStatus.READY)
    gateway.candidate_states[CANDIDATE_ID] = candidate_state(CandidateStatus.APPROVED)
    gateway.story_results[STORY_ID] = story_result("rejected", package=make_package())
    with pytest.raises(StoryDetailMalformed):
        await service_with(gateway).show(STORY_ID, now=NOW)

    gateway = FakeGateway()
    gateway.story_states[STORY_ID] = story_state(StoryGenerationStatus.READY)
    gateway.candidate_states[CANDIDATE_ID] = candidate_state(CandidateStatus.APPROVED)
    gateway.story_results[STORY_ID] = story_result(
        "ready", package=make_package(), story_id="story-v1-other"
    )
    with pytest.raises(StoryDetailMalformed):
        await service_with(gateway).show(STORY_ID, now=NOW)

    gateway = FakeGateway()
    gateway.story_states[STORY_ID] = story_state(StoryGenerationStatus.READY)
    gateway.candidate_states[CANDIDATE_ID] = candidate_state(CandidateStatus.APPROVED)
    gateway.story_results[STORY_ID] = story_result(
        "ready", package=make_package(), candidate_id="candidate-v1-other"
    )
    with pytest.raises(StoryDetailMalformed):
        await service_with(gateway).show(STORY_ID, now=NOW)

    gateway = FakeGateway()
    gateway.story_states[STORY_ID] = story_state(StoryGenerationStatus.REVIEW)
    gateway.candidate_states[CANDIDATE_ID] = StoryDetailMalformed(CANDIDATE_ID)
    with pytest.raises(StoryDetailMalformed):
        await service_with(gateway).show(STORY_ID, now=NOW)

    gateway = FakeGateway()
    gateway.story_states[STORY_ID] = story_state(StoryGenerationStatus.REVIEW)
    gateway.story_results[STORY_ID] = story_result("ready", package=None)
    with pytest.raises(StoryDetailMalformed):
        await service_with(gateway).decide(
            DecideStoryRequest(detail_id=STORY_ID, decision="approve", content_hash=HASH), now=NOW
        )


@pytest.mark.asyncio
async def test_naive_now_raises_value_error() -> None:
    gateway = FakeGateway()
    service = service_with(gateway)
    naive = datetime(2026, 10, 30, 12, 0)
    with pytest.raises(ValueError):
        await service.show(CANDIDATE_ID, now=naive)
    with pytest.raises(ValueError):
        await service.decide(
            DecideStoryRequest(detail_id=STORY_ID, decision="approve", content_hash=HASH), now=naive
        )
    assert gateway.calls == []


def test_detail_service_imports_no_publishing_or_chart_modules() -> None:
    source_path = Path(__file__).parent.parent / "src/ai_trend_pipeline/services/stories.py"
    source = source_path.read_text()
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    forbidden = ("bluesky", "render", "daily_chart", "chart_spec", "fetch_history")
    for module in modules:
        for name in forbidden:
            assert name not in module, f"{name} imported from {module}"
    assert STORY_ID_PREFIX == "story-v1-"
    assert "fetch_history" not in source


class FakeHandle:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def query(self, function: object) -> object:
        _ = function
        raise self._error

    async def result(self) -> object:
        raise self._error

    async def signal(self, function: object, argument: object) -> None:
        _, _ = function, argument
        raise self._error


class FakeHandleClient:
    def __init__(self, error: Exception) -> None:
        self._handle = FakeHandle(error)

    def get_workflow_handle_for(self, run: object, workflow_id: str) -> FakeHandle:
        _ = (run, workflow_id)
        return self._handle


@pytest.mark.asyncio
async def test_detail_carries_content_hash_for_review_and_ready() -> None:
    gateway = FakeGateway()
    gateway.candidate_states[CANDIDATE_ID] = candidate_state(CandidateStatus.APPROVED)
    gateway.story_states[STORY_ID] = story_state(
        StoryGenerationStatus.REVIEW, package=make_package()
    )
    review = await service_with(gateway).show(STORY_ID, now=NOW)
    assert review.content_hash == HASH

    gateway.story_states[STORY_ID] = story_state(StoryGenerationStatus.READY)
    gateway.story_results[STORY_ID] = story_result("ready", package=make_package())
    ready = await service_with(gateway).show(STORY_ID, now=NOW)
    assert ready.content_hash == HASH


@pytest.mark.asyncio
async def test_approve_with_stale_or_missing_hash_raises_before_signal() -> None:
    for sent in ("", "d" * 64):
        gateway = FakeGateway()
        gateway.story_states[STORY_ID] = story_state(StoryGenerationStatus.REVIEW)
        with pytest.raises(StoryReviewStale):
            await service_with(gateway).decide(
                DecideStoryRequest(detail_id=STORY_ID, decision="approve", content_hash=sent),
                now=NOW,
            )
        assert gateway.signals == []
        assert calls_named(gateway, "story_result") == []


@pytest.mark.asyncio
async def test_reject_needs_no_hash() -> None:
    gateway = FakeGateway()
    gateway.story_states[STORY_ID] = story_state(StoryGenerationStatus.REVIEW)
    gateway.story_results[STORY_ID] = story_result("rejected")
    result = await service_with(gateway).decide(
        DecideStoryRequest(detail_id=STORY_ID, decision="reject"), now=NOW
    )
    assert result.status == "rejected"
    assert [(approve, d.content_hash) for _, approve, d in gateway.signals] == [(False, None)]


@pytest.mark.asyncio
async def test_matching_hash_is_passed_in_the_approve_signal() -> None:
    gateway = FakeGateway()
    gateway.story_states[STORY_ID] = story_state(StoryGenerationStatus.REVIEW)
    gateway.story_results[STORY_ID] = story_result("ready", package=make_package())
    await service_with(gateway).decide(
        DecideStoryRequest(detail_id=STORY_ID, decision="approve", content_hash=HASH), now=NOW
    )
    assert gateway.signals[0][2].content_hash == HASH
