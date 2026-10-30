"""Pure story-status helpers, the service, and the visibility scanner's pagination."""

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode

from ai_trend_pipeline.services.candidates import (
    CandidatePage,
    CandidateSummary,
    encode_page_token,
)
from ai_trend_pipeline.services.stories import (
    CANDIDATE_WORKFLOW_TYPE,
    GENERATION_STARTING_DETAIL,
    GENERATION_WORKFLOW_TYPE,
    MAX_SEEN_CANDIDATES,
    STORY_START_FAILED_DETAIL,
    InvalidStoryPageToken,
    ListStoriesRequest,
    StoryFilter,
    StoryListPage,
    StoryPageToken,
    StoryStatusService,
    StoryStatusUnavailable,
    StoryVisibilityRow,
    build_story_row,
    collapse_story_rows,
    decode_story_page_token,
    encode_story_page_token,
    matches_story_filter,
    retained,
    sort_story_rows,
    story_filter,
    story_visibility_query,
)
from ai_trend_pipeline.services.temporal_gateway import TemporalStoryStatusGateway
from ai_trend_pipeline.sources import SourceName
from ai_trend_pipeline.story_candidate import CandidateStatus
from ai_trend_pipeline.story_status import STORY_UI_RETENTION, PrimaryStoryStatus
from ai_trend_pipeline.story_visibility import (
    STORY_CANDIDATE_ID_ATTRIBUTE,
    STORY_CREATED_AT_ATTRIBUTE,
    STORY_SOURCE_ATTRIBUTE,
    STORY_STATUS_ATTRIBUTE,
    STORY_STATUS_DETAIL_ATTRIBUTE,
    STORY_TITLE_ATTRIBUTE,
    STORY_UPDATED_AT_ATTRIBUTE,
)
from ai_trend_pipeline.web.view_models import (
    build_candidate_inbox_view,
    build_story_list_view,
    pending_nav_badge,
)

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
CUTOFF = NOW - STORY_UI_RETENTION
IN_WINDOW = NOW - timedelta(hours=1)


def visibility_row(
    workflow_type: str,
    workflow_id: str,
    *,
    candidate_id: str | None = "candidate-v1-abc",
    title: str = "A story",
    source: str = "hn",
    status: str = "awaiting_review",
    detail: str | None = None,
    created_at: datetime | None = IN_WINDOW,
    updated_at: datetime | None = None,
    closed: bool = False,
) -> StoryVisibilityRow:
    return StoryVisibilityRow(
        workflow_type=workflow_type,
        workflow_id=workflow_id,
        candidate_id=candidate_id,
        title=title,
        source=source,
        internal_status=status,
        status_detail=detail,
        created_at=created_at,
        updated_at=updated_at,
        execution_closed=closed,
    )


def candidate_row(
    status: str,
    *,
    candidate_id: str | None = "candidate-v1-abc",
    title: str = "A story",
    source: str = "hn",
    detail: str | None = None,
    created_at: datetime | None = IN_WINDOW,
    updated_at: datetime | None = None,
    closed: bool = False,
) -> StoryVisibilityRow:
    return visibility_row(
        CANDIDATE_WORKFLOW_TYPE,
        "candidate-v1-abc",
        candidate_id=candidate_id,
        title=title,
        source=source,
        status=status,
        detail=detail,
        created_at=created_at,
        updated_at=updated_at,
        closed=closed,
    )


def story_row(
    status: str,
    *,
    title: str = "A story",
    source: str = "hn",
    detail: str | None = None,
    created_at: datetime | None = IN_WINDOW,
    updated_at: datetime | None = None,
    closed: bool = False,
) -> StoryVisibilityRow:
    return visibility_row(
        GENERATION_WORKFLOW_TYPE,
        "story-v1-abc",
        title=title,
        source=source,
        status=status,
        detail=detail,
        created_at=created_at,
        updated_at=updated_at,
        closed=closed,
    )


def primary_of(row: StoryVisibilityRow) -> PrimaryStoryStatus:
    collapsed = collapse_story_rows([row], now=NOW)
    assert len(collapsed) == 1
    return build_story_row(collapsed[0]).status


def raw_token(payload: object) -> str:
    encoded = json.dumps(payload).encode("utf-8")
    return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def test_every_candidate_and_story_internal_state_maps_to_one_primary_status() -> None:
    assert primary_of(candidate_row("awaiting_review")) is PrimaryStoryStatus.PENDING
    assert primary_of(candidate_row("approved")) is PrimaryStoryStatus.WRITING
    assert primary_of(candidate_row("rejected")) is PrimaryStoryStatus.CLOSED
    assert primary_of(candidate_row("expired")) is PrimaryStoryStatus.CLOSED

    writing = ("researching", "analyzing", "writing", "checking")
    for internal in writing:
        assert primary_of(story_row(internal)) is PrimaryStoryStatus.WRITING, internal
    assert primary_of(story_row("review")) is PrimaryStoryStatus.REVIEW
    assert primary_of(story_row("ready")) is PrimaryStoryStatus.READY
    for internal in ("rejected", "unsupported", "failed", "expired"):
        assert primary_of(story_row(internal)) is PrimaryStoryStatus.CLOSED, internal

    unknown = candidate_row("something-new")
    assert primary_of(unknown) is PrimaryStoryStatus.CLOSED
    assert primary_of(story_row("not-a-state")) is PrimaryStoryStatus.CLOSED


def test_collapse_prefers_the_generation_workflow() -> None:
    candidate = candidate_row(
        "approved", title="Candidate title", source="hn", created_at=NOW - timedelta(hours=2)
    )
    story = story_row(
        "researching",
        title="Story title",
        source="fake",
        created_at=NOW - timedelta(hours=2),
        updated_at=NOW,
    )
    collapsed = collapse_story_rows([candidate, story], now=NOW)

    assert len(collapsed) == 1
    built = build_story_row(collapsed[0])
    assert built.detail_id == "story-v1-abc"
    assert built.title == "Story title"
    assert built.source == "fake"
    assert built.status is PrimaryStoryStatus.WRITING


def test_generation_start_failure_maps_to_closed_after_the_grace() -> None:
    failed = collapse_story_rows(
        [
            candidate_row(
                "approved",
                closed=True,
                updated_at=NOW - timedelta(minutes=2),
            )
        ],
        now=NOW,
    )
    assert len(failed) == 1
    built = build_story_row(failed[0])
    assert built.status is PrimaryStoryStatus.CLOSED
    assert built.status_detail == STORY_START_FAILED_DETAIL

    running = collapse_story_rows(
        [candidate_row("approved", closed=False, updated_at=NOW - timedelta(minutes=2))],
        now=NOW,
    )
    assert running[0].internal_status == "approved"
    assert running[0].status_detail == GENERATION_STARTING_DETAIL
    assert build_story_row(running[0]).status is PrimaryStoryStatus.WRITING

    within_grace = collapse_story_rows(
        [candidate_row("approved", closed=True, updated_at=NOW - timedelta(seconds=30))],
        now=NOW,
    )
    assert within_grace[0].status_detail == GENERATION_STARTING_DETAIL
    assert build_story_row(within_grace[0]).status is PrimaryStoryStatus.WRITING


def test_created_at_never_comes_from_updated_at_or_start_time() -> None:
    old = story_row(
        "review",
        created_at=NOW - timedelta(hours=47),
        updated_at=NOW,
    )
    collapsed = collapse_story_rows([old], now=NOW)
    assert build_story_row(collapsed[0]).created_at == NOW - timedelta(hours=47)

    assert retained(NOW - timedelta(hours=47, minutes=59, seconds=59), CUTOFF) is True
    assert retained(NOW - timedelta(hours=48), CUTOFF) is False


def test_retention_boundary_is_strict_for_active_and_terminal_rows() -> None:
    visible = NOW - timedelta(hours=47, minutes=59, seconds=59)
    hidden = NOW - timedelta(hours=48)
    rows = [
        candidate_row("awaiting_review", created_at=visible),
        story_row("researching", created_at=visible),
        story_row("review", created_at=visible),
        story_row("ready", created_at=visible),
        story_row("rejected", created_at=visible),
    ]
    for row in rows:
        assert row.created_at is not None
        assert retained(row.created_at, CUTOFF) is True, row.internal_status

    for status in ("awaiting_review", "researching", "review", "ready", "rejected"):
        at_boundary = (
            candidate_row(status, created_at=hidden)
            if status == "awaiting_review"
            else story_row(status, created_at=hidden)
        )
        assert at_boundary.created_at is not None
        assert retained(at_boundary.created_at, CUTOFF) is False, status


def test_filters_match_after_collapse() -> None:
    pending = collapse_story_rows([candidate_row("awaiting_review")], now=NOW)[0]
    writing = collapse_story_rows([candidate_row("approved")], now=NOW)[0]
    ready = collapse_story_rows([story_row("ready")], now=NOW)[0]
    closed = collapse_story_rows([story_row("rejected")], now=NOW)[0]

    assert matches_story_filter(pending, StoryFilter.PENDING)
    assert not matches_story_filter(pending, StoryFilter.WRITING)
    assert matches_story_filter(writing, StoryFilter.WRITING)
    assert matches_story_filter(ready, StoryFilter.READY)
    assert matches_story_filter(closed, StoryFilter.CLOSED)

    assert all(
        matches_story_filter(row, StoryFilter.ALL) for row in (pending, writing, ready, closed)
    )
    assert [
        row
        for row in (pending, writing, ready, closed)
        if matches_story_filter(row, StoryFilter.READY)
    ] == [ready]


def test_visibility_query_covers_all_filters_and_the_cutoff() -> None:
    cutoff = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
    base = (
        "(WorkflowType = 'StoryCandidateWorkflow' OR WorkflowType = 'StoryGenerationWorkflow') "
        "AND StoryCreatedAt > '2026-10-28T12:00:00Z'"
    )
    assert story_visibility_query(StoryFilter.ALL, cutoff) == base
    assert story_visibility_query(StoryFilter.PENDING, cutoff) == (
        base + " AND (StoryStatus = 'awaiting_review' OR CandidateStatus = 'awaiting_review')"
    )
    assert story_visibility_query(StoryFilter.WRITING, cutoff) == (
        base
        + " AND (StoryStatus IN ('approved','researching','analyzing','writing','checking') "
        + "OR CandidateStatus = 'approved')"
    )
    assert story_visibility_query(StoryFilter.REVIEW, cutoff) == (
        base + " AND StoryStatus = 'review'"
    )
    assert story_visibility_query(StoryFilter.READY, cutoff) == (
        base + " AND StoryStatus = 'ready'"
    )
    assert story_visibility_query(StoryFilter.CLOSED, cutoff) == (
        base
        + " AND (StoryStatus IN ('rejected','unsupported','failed','expired') "
        + "OR CandidateStatus IN ('rejected','expired'))"
    )


def test_story_filter_maps_unknown_and_blank_to_all() -> None:
    assert story_filter("writing") is StoryFilter.WRITING
    assert story_filter(" WRITING ") is StoryFilter.WRITING
    for value in ("", "   ", "unknown", "pending; drop table"):
        assert story_filter(value) is StoryFilter.ALL


def test_page_token_round_trip_and_rejects_stale_filter_or_garbage() -> None:
    token = StoryPageToken(
        filter=StoryFilter.WRITING,
        raw=encode_page_token(b"cursor"),
        seen=["candidate-v1-a", "candidate-v1-b"],
    )
    encoded = encode_story_page_token(token)
    assert decode_story_page_token(encoded, filter=StoryFilter.WRITING) == token

    with pytest.raises(InvalidStoryPageToken):
        decode_story_page_token(encoded, filter=StoryFilter.ALL)
    with pytest.raises(InvalidStoryPageToken):
        decode_story_page_token("!!!", filter=StoryFilter.ALL)
    with pytest.raises(InvalidStoryPageToken):
        decode_story_page_token(encode_page_token(b"not json"), filter=StoryFilter.ALL)
    with pytest.raises(InvalidStoryPageToken):
        decode_story_page_token(
            raw_token({"v": 2, "filter": "all", "raw": None, "seen": []}),
            filter=StoryFilter.ALL,
        )
    with pytest.raises(InvalidStoryPageToken):
        decode_story_page_token(raw_token([1, 2]), filter=StoryFilter.ALL)
    with pytest.raises(InvalidStoryPageToken):
        decode_story_page_token(
            raw_token(
                {
                    "v": 1,
                    "filter": "all",
                    "raw": None,
                    "seen": [f"candidate-v1-{n}" for n in range(MAX_SEEN_CANDIDATES + 1)],
                }
            ),
            filter=StoryFilter.ALL,
        )
    with pytest.raises(InvalidStoryPageToken):
        decode_story_page_token(
            raw_token({"v": 1, "filter": "all", "raw": "!!!", "seen": []}),
            filter=StoryFilter.ALL,
        )


class FakeExecution:
    def __init__(
        self,
        workflow_type: str,
        workflow_id: str,
        *,
        candidate_id: str,
        status: str,
        created_at: datetime,
        updated_at: datetime | None = None,
        title: str = "A story",
        source: str = "hn",
        closed: bool = False,
    ) -> None:
        self.workflow_type = workflow_type
        self.id = workflow_id
        self.status = (
            WorkflowExecutionStatus.COMPLETED if closed else WorkflowExecutionStatus.RUNNING
        )
        self.typed_search_attributes = {
            STORY_STATUS_ATTRIBUTE: status,
            STORY_TITLE_ATTRIBUTE: title,
            STORY_SOURCE_ATTRIBUTE: source,
            STORY_CANDIDATE_ID_ATTRIBUTE: candidate_id,
            STORY_CREATED_AT_ATTRIBUTE: created_at,
            STORY_UPDATED_AT_ATTRIBUTE: updated_at,
            STORY_STATUS_DETAIL_ATTRIBUTE: None,
        }

    async def memo(self) -> dict[str, object]:
        return {}


class FakeIterator:
    def __init__(self, pages: list[list[FakeExecution]], index: int) -> None:
        self._pages = pages
        self._index = index
        self.current_page: list[FakeExecution] | None = None
        self.next_page_token: bytes | None = None

    async def fetch_next_page(self) -> None:
        if self._index >= len(self._pages):
            self.current_page = []
            self.next_page_token = None
            return
        self.current_page = self._pages[self._index]
        self._index += 1
        self.next_page_token = bytes([self._index]) if self._index < len(self._pages) else None


class FakeClient:
    def __init__(self, pages: list[list[FakeExecution]]) -> None:
        self.pages = pages
        self.queries: list[str] = []

    def list_workflows(
        self,
        query: str,
        *,
        page_size: int | None = None,
        next_page_token: bytes | None = None,
    ) -> FakeIterator:
        self.queries.append(query)
        _ = page_size
        index = next_page_token[0] if next_page_token else 0
        return FakeIterator(self.pages, index)


def _gateway(pages: list[list[FakeExecution]]) -> tuple[TemporalStoryStatusGateway, FakeClient]:
    client = FakeClient(pages)
    return TemporalStoryStatusGateway(cast(Client, client)), client


@pytest.mark.asyncio
async def test_pagination_does_not_repeat_rows_across_pages() -> None:
    candidate_x = FakeExecution(
        CANDIDATE_WORKFLOW_TYPE,
        "candidate-v1-x",
        candidate_id="cand-x",
        status="approved",
        created_at=NOW - timedelta(hours=2),
    )
    story_x = FakeExecution(
        GENERATION_WORKFLOW_TYPE,
        "story-v1-x",
        candidate_id="cand-x",
        status="researching",
        created_at=NOW - timedelta(hours=2),
        updated_at=NOW,
    )
    candidate_y = FakeExecution(
        CANDIDATE_WORKFLOW_TYPE,
        "candidate-v1-y",
        candidate_id="cand-y",
        status="awaiting_review",
        created_at=NOW - timedelta(hours=1),
    )
    gateway, client = _gateway([[candidate_x], [story_x, candidate_y]])

    first = await gateway.list_stories(ListStoriesRequest(limit=1), now=NOW, cutoff=CUTOFF)
    assert [row.candidate_id for row in first.stories] == ["cand-y"]
    assert first.next_token is not None

    second = await gateway.list_stories(
        ListStoriesRequest(limit=1, page_token=first.next_token), now=NOW, cutoff=CUTOFF
    )
    assert [row.candidate_id for row in second.stories] == ["cand-x"]
    assert second.stories[0].detail_id == "story-v1-x"
    assert second.next_token is None
    assert all("StoryCreatedAt >" in query for query in client.queries)


def test_missing_metadata_is_dropped_or_uses_fallbacks() -> None:
    rows = [
        candidate_row("awaiting_review", candidate_id=None),
        candidate_row("awaiting_review", created_at=None),
        candidate_row(
            "rejected",
            title="",
            source="",
            detail="",
        ),
    ]
    collapsed = collapse_story_rows(rows, now=NOW)
    assert len(collapsed) == 1
    built = build_story_row(collapsed[0])
    assert built.title == ""
    assert built.source == ""
    assert built.status_detail is None


class FakeServiceGateway:
    def __init__(self, page: StoryListPage) -> None:
        self.page = page
        self.calls: list[tuple[ListStoriesRequest, datetime, datetime]] = []
        self.handle_calls = 0

    async def list_stories(
        self, request: ListStoriesRequest, *, now: datetime, cutoff: datetime
    ) -> StoryListPage:
        self.calls.append((request, now, cutoff))
        return self.page

    def handle(self, candidate_id: str) -> None:
        self.handle_calls += 1
        raise AssertionError(f"per-row workflow query for {candidate_id}")


@pytest.mark.asyncio
async def test_list_uses_one_gateway_call_and_no_handle() -> None:
    gateway = FakeServiceGateway(StoryListPage(stories=[build_story_row(story_row("review"))]))
    service = StoryStatusService(gateway)

    page = await service.list_stories(ListStoriesRequest(), now=NOW)

    assert len(page.stories) == 1
    assert len(gateway.calls) == 1
    assert gateway.handle_calls == 0
    _, moment, cutoff = gateway.calls[0]
    assert moment == NOW
    assert cutoff == NOW - STORY_UI_RETENTION

    with pytest.raises(ValueError):
        await service.list_stories(ListStoriesRequest(), now=datetime(2026, 10, 30, 12, 0))


def test_pending_count_helper_excludes_items_at_the_cutoff() -> None:
    visible = NOW - timedelta(hours=47, minutes=59, seconds=59)
    hidden = NOW - timedelta(hours=48)
    cutoff = NOW - STORY_UI_RETENTION

    candidates = [
        CandidateSummary(
            candidate_id=f"candidate-v1-{label}",
            source=SourceName.HN,
            title=label,
            proposed_angle="why",
            observed_at=stamp,
            status=CandidateStatus.AWAITING_REVIEW,
        )
        for label, stamp in (("visible", visible), ("hidden", hidden))
    ]
    kept = [
        candidate
        for candidate in candidates
        if candidate.observed_at is not None and retained(candidate.observed_at, cutoff)
    ]
    inbox = build_candidate_inbox_view(CandidatePage(candidates=kept), now=NOW)
    badge = pending_nav_badge(CandidatePage(candidates=kept), first_page=True)

    stories = [
        build_story_row(collapse_story_rows([story_row("ready", created_at=stamp)], now=NOW)[0])
        for stamp in (visible, hidden)
        if retained(stamp, cutoff)
    ]
    story_view = build_story_list_view(
        StoryListPage(stories=stories), now=NOW, filter=StoryFilter.ALL, page_token=None
    )

    assert len(kept) == 1
    assert inbox.count_text == "1 story waiting for review."
    assert badge is not None and badge.text == "1"
    assert len(stories) == 1
    assert story_view.count_text == "1 story from the last 48 hours."


class FailingVisibilityClient:
    def list_workflows(
        self, query: str, *, page_size: int | None = None, next_page_token: bytes | None = None
    ) -> None:
        _ = (query, page_size, next_page_token)
        raise RPCError("visibility unavailable", RPCStatusCode.UNAVAILABLE, b"unavailable")


@pytest.mark.asyncio
async def test_service_failure_maps_to_story_status_unavailable() -> None:
    gateway = TemporalStoryStatusGateway(cast(Client, FailingVisibilityClient()))
    service = StoryStatusService(gateway)

    with pytest.raises(StoryStatusUnavailable):
        await service.list_stories(ListStoriesRequest(), now=NOW)


def test_sort_orders_newest_first_then_candidate_id() -> None:
    old = visibility_row(
        GENERATION_WORKFLOW_TYPE, "story-old", candidate_id="b", created_at=NOW - timedelta(hours=2)
    )
    new = visibility_row(
        GENERATION_WORKFLOW_TYPE, "story-new", candidate_id="a", created_at=NOW - timedelta(hours=1)
    )
    same_new = visibility_row(
        GENERATION_WORKFLOW_TYPE,
        "story-new-2",
        candidate_id="c",
        created_at=NOW - timedelta(hours=1),
    )
    assert [row.candidate_id for row in sort_story_rows([old, new, same_new])] == [
        "a",
        "c",
        "b",
    ]
