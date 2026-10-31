"""Unified story status use cases over the shared 053 visibility attributes.

Temporal remains the system of record. This module only reads bounded metadata through
visibility; it never queries one workflow per row. Workflows and activities never import it.
"""

import base64
import json
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_trend_pipeline.services.candidates import (
    InvalidPageToken,
    decode_page_token,
)
from ai_trend_pipeline.story_candidate import (
    CANDIDATE_ID_PREFIX,
    STORY_EXPIRED_DETAIL,
    CandidateReviewState,
    CandidateStatus,
    DecidedByText,
    StoryCandidateResult,
)
from ai_trend_pipeline.story_drafting.models import StoryPackage
from ai_trend_pipeline.story_generation import (
    STORY_ID_PREFIX,
    FinalStoryDecision,
    StoryGenerationResult,
    StoryGenerationState,
    StoryGenerationStatus,
)
from ai_trend_pipeline.story_status import (
    STORY_UI_RETENTION,
    PrimaryStoryStatus,
    primary_story_status,
)
from ai_trend_pipeline.story_visibility import bounded_detail

CANDIDATE_WORKFLOW_TYPE = "StoryCandidateWorkflow"
GENERATION_WORKFLOW_TYPE = "StoryGenerationWorkflow"
MAX_DETAIL_ID_CHARS = 128

DEFAULT_STORY_PAGE_SIZE = 20
MAX_STORY_PAGE_SIZE = 100
MAX_SEEN_CANDIDATES = 500
MAX_STORY_SCAN_PAGES = 5
STORY_START_GRACE = timedelta(minutes=1)

GENERATION_STARTING_DETAIL = "Starting story generation."
STORY_START_FAILED_DETAIL = "Story generation did not start."

CANDIDATE_AWAITING = "awaiting_review"
CANDIDATE_APPROVED = "approved"
CANDIDATE_REJECTED = "rejected"
CANDIDATE_EXPIRED = "expired"
STORY_START_FAILED = "failed"


class StoryFilter(StrEnum):
    ALL = "all"
    PENDING = "pending"
    WRITING = "writing"
    REVIEW = "review"
    READY = "ready"
    CLOSED = "closed"


def story_filter(value: str) -> StoryFilter:
    """Map the allow-listed query value; blank or anything else means All."""
    try:
        return StoryFilter(value.strip().lower())
    except ValueError:
        return StoryFilter.ALL


class ListStoriesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filter: StoryFilter = StoryFilter.ALL
    limit: int = Field(default=DEFAULT_STORY_PAGE_SIZE, ge=1, le=MAX_STORY_PAGE_SIZE)
    page_token: str | None = None

    @field_validator("page_token", mode="before")
    @classmethod
    def _blank_page_token(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value


class StoryRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str
    detail_id: str
    title: str
    source: str
    status: PrimaryStoryStatus
    status_detail: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


def _empty_story_rows() -> list[StoryRow]:
    return []


class StoryListPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stories: list[StoryRow] = Field(default_factory=_empty_story_rows)
    next_token: str | None = None


class StoryVisibilityRow(BaseModel):
    """One raw visibility row, before the candidate-to-story handoff is de-duplicated."""

    model_config = ConfigDict(extra="forbid")

    workflow_type: str
    workflow_id: str
    candidate_id: str | None = None
    title: str = ""
    source: str = ""
    internal_status: str = ""
    status_detail: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    execution_closed: bool = False


class StoryPageToken(BaseModel):
    model_config = ConfigDict(extra="forbid")

    v: Literal[1] = 1
    filter: StoryFilter = StoryFilter.ALL
    raw: str | None = None
    seen: list[str] = Field(default_factory=list, max_length=MAX_SEEN_CANDIDATES)


class StoryStatusError(Exception):
    """Base class for story status service errors."""


class InvalidStoryPageToken(StoryStatusError):
    def __init__(self, token: str) -> None:
        super().__init__(f"invalid story page token: {token!r}")
        self.token = token


class StoryStatusUnavailable(StoryStatusError):
    def __init__(self, message: str = "story status service unavailable") -> None:
        super().__init__(message)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def encode_story_page_token(token: StoryPageToken) -> str:
    """URL-safe base64 of a compact JSON cursor, without padding."""
    payload = json.dumps(
        {
            "v": token.v,
            "filter": token.filter.value,
            "raw": token.raw,
            "seen": token.seen,
        },
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_story_page_token(value: str, *, filter: StoryFilter) -> StoryPageToken:
    """Decode our own cursor. Garbage, wrong versions and filter mismatches are invalid."""
    try:
        payload = decode_page_token(value)
    except InvalidPageToken as exc:
        raise InvalidStoryPageToken(value) from exc
    try:
        data: object = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidStoryPageToken(value) from exc
    if not isinstance(data, dict):
        raise InvalidStoryPageToken(value)
    mapping = cast(dict[str, object], data)
    if mapping.get("v") != 1:
        raise InvalidStoryPageToken(value)
    raw_filter = mapping.get("filter")
    raw = mapping.get("raw")
    seen = mapping.get("seen")
    if not isinstance(raw_filter, str) or (raw is not None and not isinstance(raw, str)):
        raise InvalidStoryPageToken(value)
    if not isinstance(seen, list):
        raise InvalidStoryPageToken(value)
    seen_values = cast(list[object], seen)
    if any(not isinstance(item, str) for item in seen_values):
        raise InvalidStoryPageToken(value)
    try:
        parsed_filter = StoryFilter(raw_filter)
    except ValueError as exc:
        raise InvalidStoryPageToken(value) from exc
    if parsed_filter is not filter or len(seen_values) > MAX_SEEN_CANDIDATES:
        raise InvalidStoryPageToken(value)
    raw_cursor = raw
    if raw_cursor is not None:
        try:
            decode_page_token(raw_cursor)
        except InvalidPageToken as exc:
            raise InvalidStoryPageToken(value) from exc
    return StoryPageToken(
        filter=parsed_filter,
        raw=raw_cursor,
        seen=cast(list[str], seen_values),
    )


_FILTER_PREDICATES: dict[StoryFilter, str] = {
    StoryFilter.ALL: "",
    StoryFilter.PENDING: (
        "(StoryStatus = 'awaiting_review' OR CandidateStatus = 'awaiting_review')"
    ),
    StoryFilter.WRITING: (
        "(StoryStatus IN ('approved','researching','analyzing','writing','checking') "
        "OR CandidateStatus = 'approved')"
    ),
    StoryFilter.REVIEW: "StoryStatus = 'review'",
    StoryFilter.READY: "StoryStatus = 'ready'",
    StoryFilter.CLOSED: (
        "(StoryStatus IN ('rejected','unsupported','failed','expired') "
        "OR CandidateStatus IN ('rejected','expired') "
        "OR CandidateStatus = 'approved')"
    ),
}


def story_visibility_query(filter: StoryFilter, cutoff: datetime) -> str:
    """One visibility query for both workflow types, each with its own retention clock.

    Candidate executions predate `StoryCreatedAt`, so they use the built-in `StartTime`;
    generation executions keep the shared `StoryCreatedAt` attribute.
    """
    stamp = _as_utc(cutoff).strftime("%Y-%m-%dT%H:%M:%SZ")
    query = (
        f"((WorkflowType = '{CANDIDATE_WORKFLOW_TYPE}' AND StartTime > '{stamp}') "
        f"OR (WorkflowType = '{GENERATION_WORKFLOW_TYPE}' "
        f"AND StoryCreatedAt > '{stamp}'))"
    )
    predicate = _FILTER_PREDICATES[filter]
    return f"{query} AND {predicate}" if predicate else query


def retained(created_at: datetime, cutoff: datetime) -> bool:
    """Strict 48-hour window: an item exactly at the cutoff is outside the UI."""
    return _as_utc(created_at) > _as_utc(cutoff)


def _within_grace(updated_at: datetime | None, now: datetime) -> bool:
    if updated_at is None:
        return False
    return now - _as_utc(updated_at) <= STORY_START_GRACE


def collapse_story_rows(
    rows: list[StoryVisibilityRow], *, now: datetime
) -> list[StoryVisibilityRow]:
    """One row per StoryCandidateId, preferring the generation workflow."""
    groups: dict[str, list[StoryVisibilityRow]] = {}
    for row in rows:
        if row.candidate_id is None or row.created_at is None:
            continue
        groups.setdefault(row.candidate_id, []).append(row)

    collapsed: list[StoryVisibilityRow] = []
    for candidate_id, group in groups.items():
        generation = next(
            (row for row in group if row.workflow_type == GENERATION_WORKFLOW_TYPE), None
        )
        candidate = next(
            (row for row in group if row.workflow_type == CANDIDATE_WORKFLOW_TYPE), None
        )
        if generation is not None:
            chosen = generation
            internal_status = generation.internal_status
            status_detail = bounded_detail(generation.status_detail)
            created_at = generation.created_at or (
                candidate.created_at if candidate is not None else None
            )
            updated_at = generation.updated_at
        elif candidate is not None:
            chosen = candidate
            internal_status = candidate.internal_status
            status_detail = bounded_detail(candidate.status_detail)
            created_at = candidate.created_at
            updated_at = candidate.updated_at
            if internal_status == CANDIDATE_APPROVED:
                if not candidate.execution_closed or _within_grace(candidate.updated_at, now):
                    internal_status = CANDIDATE_APPROVED
                    status_detail = GENERATION_STARTING_DETAIL
                else:
                    internal_status = STORY_START_FAILED
                    status_detail = STORY_START_FAILED_DETAIL
            elif internal_status == CANDIDATE_EXPIRED and status_detail is None:
                status_detail = STORY_EXPIRED_DETAIL
        else:
            continue
        if created_at is None:
            continue
        collapsed.append(
            StoryVisibilityRow(
                workflow_type=chosen.workflow_type,
                workflow_id=chosen.workflow_id,
                candidate_id=candidate_id,
                title=chosen.title,
                source=chosen.source,
                internal_status=internal_status,
                status_detail=status_detail,
                created_at=created_at,
                updated_at=updated_at,
                execution_closed=chosen.execution_closed,
            )
        )
    return collapsed


def matches_story_filter(row: StoryVisibilityRow, filter: StoryFilter) -> bool:
    if filter is StoryFilter.ALL:
        return True
    return primary_story_status(row.internal_status).value == filter.value


def _timestamp(value: datetime | None) -> float:
    return 0.0 if value is None else _as_utc(value).timestamp()


def sort_story_rows(rows: list[StoryVisibilityRow]) -> list[StoryVisibilityRow]:
    """Newest StoryCreatedAt first, then the candidate id for a stable order."""
    return sorted(rows, key=lambda row: (-_timestamp(row.created_at), row.candidate_id or ""))


def build_story_row(row: StoryVisibilityRow) -> StoryRow:
    if row.candidate_id is None or row.created_at is None:
        raise ValueError("story visibility row cannot be placed in the 48-hour window")
    return StoryRow(
        candidate_id=row.candidate_id,
        detail_id=row.workflow_id,
        title=row.title,
        source=row.source,
        status=primary_story_status(row.internal_status),
        status_detail=row.status_detail,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class StoryStatusGateway(Protocol):
    async def list_stories(
        self, request: ListStoriesRequest, *, now: datetime, cutoff: datetime
    ) -> StoryListPage: ...


class StoryStatusService:
    def __init__(self, gateway: StoryStatusGateway) -> None:
        self._gateway = gateway

    async def list_stories(self, request: ListStoriesRequest, *, now: datetime) -> StoryListPage:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be a timezone-aware datetime")
        moment = now.astimezone(UTC)
        cutoff = moment - STORY_UI_RETENTION
        return await self._gateway.list_stories(request, now=moment, cutoff=cutoff)


class StoryDetailKind(StrEnum):
    CANDIDATE = "candidate"
    GENERATION = "generation"


def story_detail_kind(detail_id: str) -> StoryDetailKind:
    """Resolve the deterministic versioned workflow id; anything else does not exist."""
    if not detail_id or len(detail_id) > MAX_DETAIL_ID_CHARS:
        raise StoryDetailNotFound(detail_id)
    if detail_id.startswith(CANDIDATE_ID_PREFIX):
        return StoryDetailKind.CANDIDATE
    if detail_id.startswith(STORY_ID_PREFIX):
        return StoryDetailKind.GENERATION
    raise StoryDetailNotFound(detail_id)


class StoryDetailError(Exception):
    """Base class for story detail service errors."""


class StoryDetailNotFound(StoryDetailError):
    def __init__(self, detail_id: str) -> None:
        super().__init__(f"story detail {detail_id} not found")
        self.detail_id = detail_id


class StoryDetailExpired(StoryDetailError):
    def __init__(self, detail_id: str) -> None:
        super().__init__(f"story detail {detail_id} is past the 48-hour cutoff")
        self.detail_id = detail_id


class StoryNotReviewable(StoryDetailError):
    def __init__(self, detail_id: str) -> None:
        super().__init__(f"story {detail_id} is not awaiting a final decision")
        self.detail_id = detail_id


class StoryDetailMalformed(StoryDetailError):
    def __init__(self, detail_id: str) -> None:
        super().__init__(f"story detail {detail_id} violates its typed contract")
        self.detail_id = detail_id


class StoryDetailUnavailable(StoryDetailError):
    def __init__(self, message: str = "story detail service unavailable") -> None:
        super().__init__(message)


class StoryDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detail_id: str
    workflow_id: str
    candidate_id: str
    kind: StoryDetailKind
    title: str
    source: str
    created_at: datetime
    status: PrimaryStoryStatus
    internal_status: str
    status_detail: str | None = None
    package: StoryPackage | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    note: str = ""


class DecideStoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detail_id: str = Field(min_length=1, max_length=MAX_DETAIL_ID_CHARS)
    decision: Literal["approve", "reject"]
    decided_by: DecidedByText | None = None


class StoryDecisionResult(BaseModel):
    workflow_id: str
    status: Literal["ready", "rejected", "unsupported", "failed", "expired"]
    decided_by: str | None = None
    decided_at: datetime | None = None


_STORY_OPEN_STATUSES = frozenset(
    {
        StoryGenerationStatus.RESEARCHING,
        StoryGenerationStatus.ANALYZING,
        StoryGenerationStatus.WRITING,
        StoryGenerationStatus.CHECKING,
        StoryGenerationStatus.REVIEW,
    }
)
_STORY_TERMINAL_VALUES = frozenset({"ready", "rejected", "unsupported", "failed", "expired"})


def _aware_moment(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    return now.astimezone(UTC)


def _bounded_note(value: str) -> str:
    return bounded_detail(value) or ""


class StoryDetailGateway(Protocol):
    async def candidate_state(self, workflow_id: str) -> CandidateReviewState: ...

    async def candidate_result(self, workflow_id: str) -> StoryCandidateResult: ...

    async def story_state(self, workflow_id: str) -> StoryGenerationState: ...

    async def story_result(self, workflow_id: str) -> StoryGenerationResult: ...

    async def decide(
        self, workflow_id: str, decision: FinalStoryDecision, *, approve: bool
    ) -> None: ...


class StoryDetailService:
    """Read one candidate or story through queries and typed results; never parse history."""

    def __init__(self, gateway: StoryDetailGateway) -> None:
        self._gateway = gateway

    async def show(self, detail_id: str, *, now: datetime) -> StoryDetail:
        cutoff = _aware_moment(now) - STORY_UI_RETENTION
        kind = story_detail_kind(detail_id)
        if kind is StoryDetailKind.CANDIDATE:
            return await self._candidate_detail(detail_id, cutoff)
        return await self._generation_detail(detail_id, cutoff=cutoff)

    async def decide(self, request: DecideStoryRequest, *, now: datetime) -> StoryDecisionResult:
        cutoff = _aware_moment(now) - STORY_UI_RETENTION
        kind = story_detail_kind(request.detail_id)
        if kind is not StoryDetailKind.GENERATION:
            raise StoryNotReviewable(request.detail_id)
        state = await self._gateway.story_state(request.detail_id)
        if state.story_created_at is None:
            raise StoryDetailMalformed(request.detail_id)
        self._retain(request.detail_id, state.story_created_at, cutoff)
        if state.status is not StoryGenerationStatus.REVIEW:
            raise StoryNotReviewable(request.detail_id)
        decision = FinalStoryDecision(decided_by=request.decided_by)
        await self._gateway.decide(
            request.detail_id, decision, approve=request.decision == "approve"
        )
        result = await self._gateway.story_result(request.detail_id)
        if result.status == "ready" and result.package is None:
            raise StoryDetailMalformed(request.detail_id)
        if result.status not in _STORY_TERMINAL_VALUES:
            raise StoryDetailMalformed(request.detail_id)
        return StoryDecisionResult(
            workflow_id=request.detail_id,
            status=result.status,
            decided_by=result.decided_by,
            decided_at=result.decided_at,
        )

    def _retain(self, detail_id: str, created_at: datetime, cutoff: datetime) -> datetime:
        if not retained(created_at, cutoff):
            raise StoryDetailExpired(detail_id)
        return _as_utc(created_at)

    async def _candidate_detail(self, detail_id: str, cutoff: datetime) -> StoryDetail:
        state = await self._gateway.candidate_state(detail_id)
        candidate = state.candidate
        created = self._retain(detail_id, candidate.observed_at, cutoff)
        if state.status is CandidateStatus.AWAITING_REVIEW:
            return StoryDetail(
                detail_id=detail_id,
                workflow_id=detail_id,
                candidate_id=detail_id,
                kind=StoryDetailKind.CANDIDATE,
                title=candidate.title,
                source=candidate.source.value,
                created_at=created,
                status=PrimaryStoryStatus.PENDING,
                internal_status=state.status.value,
                package=None,
            )
        if state.status is CandidateStatus.APPROVED:
            return await self._approved_candidate(detail_id, state, created, cutoff)
        if state.status in (CandidateStatus.REJECTED, CandidateStatus.EXPIRED):
            result = await self._gateway.candidate_result(detail_id)
            if result.status == "approved":
                return await self._approved_candidate(detail_id, state, created, cutoff)
            if result.status not in ("rejected", "expired"):
                raise StoryDetailMalformed(detail_id)
            return StoryDetail(
                detail_id=detail_id,
                workflow_id=detail_id,
                candidate_id=detail_id,
                kind=StoryDetailKind.CANDIDATE,
                title=result.candidate.title,
                source=result.candidate.source.value,
                created_at=created,
                status=PrimaryStoryStatus.CLOSED,
                internal_status=result.status,
                package=None,
                decided_by=result.decided_by,
                decided_at=result.decided_at,
                note=_bounded_note(result.note),
            )
        raise StoryDetailMalformed(detail_id)

    async def _approved_candidate(
        self,
        detail_id: str,
        state: CandidateReviewState,
        created: datetime,
        cutoff: datetime,
    ) -> StoryDetail:
        title = state.candidate.title
        source = state.candidate.source.value
        workflow_id = state.story_workflow_id
        if workflow_id:
            try:
                return await self._generation_detail(
                    workflow_id,
                    cutoff=cutoff,
                    alias_detail_id=detail_id,
                    metadata=(title, source),
                )
            except StoryDetailNotFound:
                # The child may not be visible yet; the list shows it as still starting too.
                pass
        return StoryDetail(
            detail_id=detail_id,
            workflow_id=workflow_id or detail_id,
            candidate_id=detail_id,
            kind=StoryDetailKind.GENERATION,
            title=title,
            source=source,
            created_at=created,
            status=PrimaryStoryStatus.WRITING,
            internal_status=CandidateStatus.APPROVED.value,
            status_detail=GENERATION_STARTING_DETAIL,
            package=None,
        )

    async def _generation_detail(
        self,
        workflow_id: str,
        *,
        cutoff: datetime,
        alias_detail_id: str | None = None,
        metadata: tuple[str, str] | None = None,
    ) -> StoryDetail:
        requested_id = alias_detail_id or workflow_id
        state = await self._gateway.story_state(workflow_id)
        if state.story_created_at is None:
            raise StoryDetailMalformed(requested_id)
        created = self._retain(requested_id, state.story_created_at, cutoff)
        title, source = metadata if metadata is not None else ("", "")
        if metadata is None and state.candidate_id:
            try:
                review = await self._gateway.candidate_state(state.candidate_id)
            except StoryDetailNotFound:
                pass
            else:
                title = review.candidate.title
                source = review.candidate.source.value

        if state.status in _STORY_OPEN_STATUSES:
            return StoryDetail(
                detail_id=requested_id,
                workflow_id=workflow_id,
                candidate_id=state.candidate_id,
                kind=StoryDetailKind.GENERATION,
                title=title,
                source=source,
                created_at=created,
                status=primary_story_status(state.status.value),
                internal_status=state.status.value,
                status_detail=bounded_detail(state.status_detail),
                package=(state.package if state.status is StoryGenerationStatus.REVIEW else None),
            )

        if state.status not in (
            StoryGenerationStatus.READY,
            StoryGenerationStatus.REJECTED,
            StoryGenerationStatus.UNSUPPORTED,
            StoryGenerationStatus.FAILED,
            StoryGenerationStatus.EXPIRED,
        ):
            raise StoryDetailMalformed(requested_id)

        result = await self._gateway.story_result(workflow_id)
        if (
            result.story_id != state.story_id
            or result.candidate_id != state.candidate_id
            or result.status != state.status.value
        ):
            raise StoryDetailMalformed(requested_id)
        created = self._retain(requested_id, result.story_created_at, cutoff)
        if result.status == "ready" and result.package is None:
            raise StoryDetailMalformed(requested_id)
        return StoryDetail(
            detail_id=requested_id,
            workflow_id=workflow_id,
            candidate_id=state.candidate_id,
            kind=StoryDetailKind.GENERATION,
            title=title,
            source=source,
            created_at=created,
            status=primary_story_status(result.status),
            internal_status=result.status,
            status_detail=bounded_detail(result.status_detail),
            package=result.package if result.status == "ready" else None,
            decided_by=result.decided_by,
            decided_at=result.decided_at,
            note=_bounded_note(result.note),
        )


__all__ = [
    "CANDIDATE_WORKFLOW_TYPE",
    "CANDIDATE_ID_PREFIX",
    "DecideStoryRequest",
    "MAX_DETAIL_ID_CHARS",
    "STORY_ID_PREFIX",
    "StoryDecisionResult",
    "StoryDetail",
    "StoryDetailError",
    "StoryDetailExpired",
    "StoryDetailGateway",
    "StoryDetailKind",
    "StoryDetailMalformed",
    "StoryDetailNotFound",
    "StoryDetailService",
    "StoryDetailUnavailable",
    "StoryNotReviewable",
    "GENERATION_STARTING_DETAIL",
    "GENERATION_WORKFLOW_TYPE",
    "InvalidStoryPageToken",
    "ListStoriesRequest",
    "MAX_SEEN_CANDIDATES",
    "MAX_STORY_SCAN_PAGES",
    "STORY_START_FAILED_DETAIL",
    "STORY_START_GRACE",
    "StoryFilter",
    "StoryListPage",
    "StoryPageToken",
    "StoryRow",
    "StoryStatusError",
    "StoryStatusGateway",
    "StoryStatusService",
    "StoryStatusUnavailable",
    "StoryVisibilityRow",
    "build_story_row",
    "collapse_story_rows",
    "decode_story_page_token",
    "encode_story_page_token",
    "matches_story_filter",
    "retained",
    "sort_story_rows",
    "story_filter",
    "story_detail_kind",
    "story_visibility_query",
]
