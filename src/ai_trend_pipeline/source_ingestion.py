"""Long-running per-source ingestion: poll, prefilter, select, start candidate children.

Everything here is deterministic workflow code. HTTP and LLM calls happen only inside the
activities named by the source registry and `story_selection`.
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal, cast

from temporalio import workflow
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import (
    ActivityError,
    WorkflowAlreadyStartedError,
    is_cancelled_exception,
)

with workflow.unsafe.imports_passed_through():
    from pydantic import (
        BaseModel,
        ConfigDict,
        Field,
        StringConstraints,
        field_validator,
        model_validator,
    )

    from ai_trend_pipeline.ingestion_logging import (
        IngestionLogEvent,
        log_event,
    )
    from ai_trend_pipeline.sources.models import (
        MAX_POLL_LIMIT,
        SourceEvent,
        SourceName,
        SourcePollInput,
        SourcePollOutput,
        ensure_aware_utc,
    )
    from ai_trend_pipeline.sources.registry import get_source
    from ai_trend_pipeline.story_candidate import (
        CANDIDATE_TASK_QUEUE,
        DEFAULT_CANDIDATE_REVIEW_TIMEOUT_S,
        MAX_CANDIDATE_REVIEW_TIMEOUT_S,
        MIN_CANDIDATE_REVIEW_TIMEOUT_S,
        StoryCandidate,
        StoryCandidateInput,
        StoryCandidateWorkflow,
        candidate_id_for,
        candidate_memo,
        candidate_search_attributes,
    )
    from ai_trend_pipeline.story_selection import (
        DEFAULT_MAX_CANDIDATES,
        MAX_BATCH_EVENTS,
        MAX_CANDIDATES,
        SelectedStory,
        SelectStoryCandidatesInput,
        SelectStoryCandidatesOutput,
        prefilter_events,
    )

SOURCE_INGESTION_TASK_QUEUE = "source-ingestion-task-queue"
SOURCE_INGESTION_WORKFLOW_ID_PREFIX = "source-ingestion-v1-"
SELECT_STORY_CANDIDATES_ACTIVITY = "select_story_candidates"

MAX_PROCESSED_IDS = 1000
MAX_PROCESSED_ID_LENGTH = 128
MAX_CURSOR_LENGTH = 256

DEFAULT_SOURCE_POLL_LIMIT = 30
DEFAULT_POLL_INTERVAL_SECONDS = 300
MIN_POLL_INTERVAL_SECONDS = 30
MAX_POLL_INTERVAL_SECONDS = 3600
DEFAULT_MAX_POLLS_PER_RUN = 50
MIN_MAX_POLLS_PER_RUN = 1
MAX_MAX_POLLS_PER_RUN = 500

DEFAULT_SOURCE_CANDIDATE_BUDGET = 20
MIN_SOURCE_CANDIDATE_BUDGET = 1
MAX_SOURCE_CANDIDATE_BUDGET = 500

POLL_ACTIVITY_TIMEOUT = timedelta(seconds=60)
SELECT_ACTIVITY_TIMEOUT = timedelta(seconds=120)
ACTIVITY_MAX_ATTEMPTS = 3

ProcessedId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_PROCESSED_ID_LENGTH),
]


def source_ingestion_workflow_id(source: SourceName) -> str:
    """Versioned chain id, one per source; a future rewrite can use a new prefix."""
    return f"{SOURCE_INGESTION_WORKFLOW_ID_PREFIX}{source.value}"


def _strip_cursor(value: object) -> object:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value


def budget_day_for(now: datetime) -> date:
    """The UTC day a budget counter belongs to."""
    return now.astimezone(UTC).date()


def budget_remaining(
    budget: int,
    started: int,
    day: date | None,
    now: datetime,
) -> int:
    """Remaining starts for `day`; a different (or missing) day resets to the full budget."""
    if day != budget_day_for(now):
        return budget
    return max(0, budget - started)


class SourceIngestionInput(BaseModel):
    """Configuration plus the bounded state carried across continue-as-new."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: SourceName
    poll_limit: int = Field(default=DEFAULT_SOURCE_POLL_LIMIT, ge=1, le=MAX_POLL_LIMIT)
    poll_interval_seconds: int = Field(
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        ge=MIN_POLL_INTERVAL_SECONDS,
        le=MAX_POLL_INTERVAL_SECONDS,
    )
    max_candidates: int = Field(default=DEFAULT_MAX_CANDIDATES, ge=1, le=MAX_CANDIDATES)
    max_polls_per_run: int = Field(
        default=DEFAULT_MAX_POLLS_PER_RUN,
        ge=MIN_MAX_POLLS_PER_RUN,
        le=MAX_MAX_POLLS_PER_RUN,
    )
    review_timeout_seconds: int = Field(
        default=DEFAULT_CANDIDATE_REVIEW_TIMEOUT_S,
        ge=MIN_CANDIDATE_REVIEW_TIMEOUT_S,
        le=MAX_CANDIDATE_REVIEW_TIMEOUT_S,
    )
    cursor: str | None = Field(default=None, max_length=MAX_CURSOR_LENGTH)
    processed_ids: tuple[ProcessedId, ...] = Field(default=(), max_length=MAX_PROCESSED_IDS)
    polls_in_run: int = Field(default=0, ge=0)
    total_polls: int = Field(default=0, ge=0)
    candidate_budget: int = Field(
        default=DEFAULT_SOURCE_CANDIDATE_BUDGET,
        ge=MIN_SOURCE_CANDIDATE_BUDGET,
        le=MAX_SOURCE_CANDIDATE_BUDGET,
    )
    budget_day: date | None = None
    candidates_started: int = Field(default=0, ge=0)

    @field_validator("cursor", mode="before")
    @classmethod
    def _normalize_cursor(cls, value: object) -> object:
        return _strip_cursor(value)

    @model_validator(mode="after")
    def _unique_processed_ids(self) -> "SourceIngestionInput":
        if len(set(self.processed_ids)) != len(self.processed_ids):
            raise ValueError("processed_ids must be unique")
        return self


def _empty_processed_ids() -> tuple[str, ...]:
    return ()


class SourceIngestionState(BaseModel):
    """Query snapshot; `source` is optional so the query is safe before the first task."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: SourceName | None = None
    cursor: str | None = Field(default=None, max_length=MAX_CURSOR_LENGTH)
    processed_ids: tuple[ProcessedId, ...] = Field(
        default_factory=_empty_processed_ids, max_length=MAX_PROCESSED_IDS
    )
    polls_in_run: int = Field(default=0, ge=0)
    total_polls: int = Field(default=0, ge=0)
    last_poll_at: datetime | None = None
    last_event_count: int = Field(default=0, ge=0)
    last_new_event_count: int = Field(default=0, ge=0)
    last_selected_count: int = Field(default=0, ge=0)
    candidate_budget: int = Field(
        default=DEFAULT_SOURCE_CANDIDATE_BUDGET,
        ge=MIN_SOURCE_CANDIDATE_BUDGET,
        le=MAX_SOURCE_CANDIDATE_BUDGET,
    )
    budget_day: date | None = None
    candidates_started: int = Field(default=0, ge=0)
    budget_remaining: int = Field(default=DEFAULT_SOURCE_CANDIDATE_BUDGET, ge=0)

    @field_validator("cursor", mode="before")
    @classmethod
    def _normalize_cursor(cls, value: object) -> object:
        return _strip_cursor(value)

    @field_validator("last_poll_at", mode="after")
    @classmethod
    def _to_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else ensure_aware_utc(value)

    @model_validator(mode="after")
    def _budget_remaining_from_counters(self) -> "SourceIngestionState":
        remaining = max(0, self.candidate_budget - self.candidates_started)
        if self.budget_remaining != remaining:
            object.__setattr__(self, "budget_remaining", remaining)
        return self


class SourceIngestionResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: SourceName
    reason: Literal["stopped"]
    total_polls: int = Field(ge=0)
    cursor: str | None = Field(default=None, max_length=MAX_CURSOR_LENGTH)

    @field_validator("cursor", mode="before")
    @classmethod
    def _normalize_cursor(cls, value: object) -> object:
        return _strip_cursor(value)


def filter_new_events(
    events: Sequence[SourceEvent],
    processed_ids: Sequence[str],
    *,
    limit: int = MAX_BATCH_EVENTS,
) -> list[SourceEvent]:
    """Drop already-processed IDs, keep the first occurrence, preserve order, cap the batch."""
    seen = set(processed_ids)
    kept: list[SourceEvent] = []
    for event in events:
        if event.external_id in seen:
            continue
        seen.add(event.external_id)
        kept.append(event)
        if len(kept) == limit:
            break
    return kept


def _numeric(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def advance_cursor(cursor: str | None, events: Sequence[SourceEvent]) -> str | None:
    """Advance to the highest numeric external ID; leave a non-numeric cursor untouched."""
    if cursor is not None and _numeric(cursor) is None:
        return cursor
    best = _numeric(cursor)
    for event in events:
        value = _numeric(event.external_id)
        if value is not None and (best is None or value > best):
            best = value
    if best is None:
        return cursor
    return str(best)


def append_processed_ids(
    existing: Sequence[str],
    new_ids: Sequence[str],
    *,
    limit: int = MAX_PROCESSED_IDS,
) -> tuple[str, ...]:
    """Append unseen IDs in order and keep the newest `limit` entries (FIFO eviction)."""
    if limit < 1:
        raise ValueError(f"limit must be positive, got {limit}")
    result = list(existing)
    seen = set(result)
    for external_id in new_ids:
        if external_id in seen:
            continue
        seen.add(external_id)
        result.append(external_id)
    if len(result) > limit:
        del result[: len(result) - limit]
    return tuple(result)


@workflow.defn
class SourceIngestionWorkflow:
    """Poll one source forever, selecting stories and starting reviewable candidates."""

    def __init__(self) -> None:
        self._stop_requested = False
        self._source: SourceName | None = None
        self._cursor: str | None = None
        self._processed_ids: tuple[str, ...] = ()
        self._polls_in_run = 0
        self._total_polls = 0
        self._last_poll_at: datetime | None = None
        self._last_event_count = 0
        self._last_new_event_count = 0
        self._last_selected_count = 0
        self._candidate_budget = DEFAULT_SOURCE_CANDIDATE_BUDGET
        self._budget_day: date | None = None
        self._candidates_started = 0
        self._budget_exhausted_logged_day: date | None = None

    def _state(self) -> SourceIngestionState:
        return SourceIngestionState(
            source=self._source,
            cursor=self._cursor,
            processed_ids=self._processed_ids,
            polls_in_run=self._polls_in_run,
            total_polls=self._total_polls,
            last_poll_at=self._last_poll_at,
            last_event_count=self._last_event_count,
            last_new_event_count=self._last_new_event_count,
            last_selected_count=self._last_selected_count,
            candidate_budget=self._candidate_budget,
            budget_day=self._budget_day,
            candidates_started=self._candidates_started,
        )

    def _stopped(self) -> SourceIngestionResult:
        source = self._source
        assert source is not None
        log_event(
            workflow.logger,
            IngestionLogEvent.STOPPED,
            source,
            total_polls=self._total_polls,
        )
        return SourceIngestionResult(
            source=source,
            reason="stopped",
            total_polls=self._total_polls,
            cursor=self._cursor,
        )

    def _next_input(self, data: SourceIngestionInput) -> SourceIngestionInput:
        return data.model_copy(
            update={
                "cursor": self._cursor,
                "processed_ids": self._processed_ids,
                "polls_in_run": 0,
                "total_polls": self._total_polls,
                "budget_day": self._budget_day,
                "candidates_started": self._candidates_started,
            }
        )

    def _roll_budget(self, now: datetime) -> None:
        day = budget_day_for(now)
        if self._budget_day != day:
            self._budget_day = day
            self._candidates_started = 0
            self._budget_exhausted_logged_day = None

    def _remaining(self, now: datetime) -> int:
        return budget_remaining(
            self._candidate_budget,
            self._candidates_started,
            self._budget_day,
            now,
        )

    def _record_poll(self, event_count: int, new_event_count: int) -> None:
        self._polls_in_run += 1
        self._total_polls += 1
        self._last_poll_at = workflow.now()
        self._last_event_count = event_count
        self._last_new_event_count = new_event_count
        self._last_selected_count = 0

    def _should_continue_as_new(self, data: SourceIngestionInput) -> bool:
        return (
            self._polls_in_run >= data.max_polls_per_run
            or workflow.info().is_continue_as_new_suggested()
        )

    async def _wait(self, data: SourceIngestionInput) -> None:
        try:
            await workflow.wait_condition(
                lambda: self._stop_requested,
                timeout=timedelta(seconds=data.poll_interval_seconds),
            )
        except TimeoutError:
            # The poll interval elapsed; the next loop iteration polls again.
            return

    async def _poll(self, data: SourceIngestionInput) -> SourcePollOutput | None:
        activity_name = get_source(data.source).poll_activity
        try:
            # The string-name overload returns Any; bind it to the declared result model.
            return cast(
                SourcePollOutput,
                await workflow.execute_activity(
                    activity_name,
                    SourcePollInput(source=data.source, limit=data.poll_limit),
                    start_to_close_timeout=POLL_ACTIVITY_TIMEOUT,
                    retry_policy=RetryPolicy(maximum_attempts=ACTIVITY_MAX_ATTEMPTS),
                    result_type=SourcePollOutput,
                ),
            )
        except ActivityError as exc:
            if is_cancelled_exception(exc):
                raise
            log_event(
                workflow.logger,
                IngestionLogEvent.POLL_FAILED,
                data.source,
                poll=self._total_polls + 1,
                error_type=type(exc).__name__,
            )
            return None

    async def _select(
        self,
        batch: Sequence[SourceEvent],
        max_candidates: int,
        source: SourceName,
    ) -> list[SelectedStory]:
        try:
            # Same string-name overload; the activity is named by contract, not imported.
            output = cast(
                SelectStoryCandidatesOutput,
                await workflow.execute_activity(
                    SELECT_STORY_CANDIDATES_ACTIVITY,
                    SelectStoryCandidatesInput(events=list(batch), max_candidates=max_candidates),
                    start_to_close_timeout=SELECT_ACTIVITY_TIMEOUT,
                    retry_policy=RetryPolicy(maximum_attempts=ACTIVITY_MAX_ATTEMPTS),
                    result_type=SelectStoryCandidatesOutput,
                ),
            )
        except ActivityError as exc:
            if is_cancelled_exception(exc):
                raise
            log_event(
                workflow.logger,
                IngestionLogEvent.SELECTION_FAILED,
                source,
                batch_size=len(batch),
                error_type=type(exc).__name__,
            )
            return []
        log_event(
            workflow.logger,
            IngestionLogEvent.SELECTION,
            source,
            batch_size=len(batch),
            selected_count=len(output.selections),
            max_candidates=max_candidates,
            budget_remaining=self._remaining(workflow.now()),
        )
        return output.selections

    async def _start_candidates(
        self,
        batch: Sequence[SourceEvent],
        selections: Sequence[SelectedStory],
        limit: int,
        data: SourceIngestionInput,
    ) -> int:
        by_id = {event.external_id: event for event in batch}
        started = 0
        for selection in selections:
            if started >= limit:
                break
            event = by_id.get(selection.external_id)
            if event is None:
                log_event(
                    workflow.logger,
                    IngestionLogEvent.SELECTION_OUTSIDE_BATCH,
                    data.source,
                    external_id=selection.external_id,
                )
                continue
            candidate = StoryCandidate(
                source=event.source,
                external_id=event.external_id,
                title=event.title,
                url=event.url,
                published_at=event.published_at,
                observed_at=event.observed_at,
                summary=selection.summary,
                proposed_angle=selection.proposed_angle,
                selection_reason=selection.selection_reason,
                metrics=dict(event.metrics),
            )
            candidate_id = candidate_id_for(candidate.source, candidate.external_id)
            try:
                await workflow.start_child_workflow(
                    StoryCandidateWorkflow.run,
                    StoryCandidateInput(
                        candidate=candidate,
                        review_timeout_seconds=data.review_timeout_seconds,
                    ),
                    id=candidate_id,
                    task_queue=CANDIDATE_TASK_QUEUE,
                    parent_close_policy=workflow.ParentClosePolicy.ABANDON,
                    id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                    search_attributes=candidate_search_attributes(candidate),
                    memo=candidate_memo(candidate),
                )
            except WorkflowAlreadyStartedError:
                log_event(
                    workflow.logger,
                    IngestionLogEvent.CANDIDATE_DUPLICATE,
                    data.source,
                    external_id=event.external_id,
                    candidate_id=candidate_id,
                )
                continue
            self._candidates_started += 1
            started += 1
            log_event(
                workflow.logger,
                IngestionLogEvent.CANDIDATE_STARTED,
                data.source,
                external_id=event.external_id,
                candidate_id=candidate_id,
                budget_remaining=self._remaining(workflow.now()),
            )
        return started

    @workflow.run
    async def run(self, data: SourceIngestionInput) -> SourceIngestionResult:
        self._source = data.source
        self._cursor = data.cursor
        self._processed_ids = data.processed_ids
        self._polls_in_run = data.polls_in_run
        self._total_polls = data.total_polls
        self._candidate_budget = data.candidate_budget
        self._budget_day = data.budget_day
        self._candidates_started = data.candidates_started
        if (
            data.total_polls > 0
            and data.budget_day is not None
            and data.budget_day == budget_day_for(workflow.now())
            and data.candidates_started >= data.candidate_budget
        ):
            # A continued chain already logged today's exhaustion; keep "once per day".
            self._budget_exhausted_logged_day = data.budget_day
        log_event(
            workflow.logger,
            IngestionLogEvent.STARTED,
            data.source,
            polls_in_run=self._polls_in_run,
            total_polls=self._total_polls,
        )

        while True:
            if self._stop_requested:
                return self._stopped()
            self._roll_budget(workflow.now())

            output = await self._poll(data)
            if output is None:
                self._record_poll(0, 0)
            else:
                self._cursor = advance_cursor(self._cursor, output.events)
                batch = prefilter_events(filter_new_events(output.events, self._processed_ids))
                self._record_poll(len(output.events), len(batch))
                remaining = self._remaining(workflow.now())
                log_event(
                    workflow.logger,
                    IngestionLogEvent.POLL,
                    data.source,
                    poll=self._total_polls,
                    event_count=len(output.events),
                    new_event_count=len(batch),
                    batch_size=len(batch),
                    cursor=self._cursor,
                    budget_remaining=remaining,
                )
                if batch:
                    if remaining == 0:
                        day = self._budget_day
                        assert day is not None
                        if self._budget_exhausted_logged_day != day:
                            log_event(
                                workflow.logger,
                                IngestionLogEvent.BUDGET_EXHAUSTED,
                                data.source,
                                day=str(day),
                                budget=self._candidate_budget,
                                candidates_started=self._candidates_started,
                            )
                            self._budget_exhausted_logged_day = day
                    else:
                        limit = min(data.max_candidates, remaining)
                        selections = await self._select(batch, limit, data.source)
                        self._processed_ids = append_processed_ids(
                            self._processed_ids,
                            [event.external_id for event in batch],
                        )
                        self._last_selected_count = await self._start_candidates(
                            batch, selections, limit, data
                        )

            if self._stop_requested:
                return self._stopped()
            if self._should_continue_as_new(data):
                log_event(
                    workflow.logger,
                    IngestionLogEvent.CONTINUE_AS_NEW,
                    data.source,
                    total_polls=self._total_polls,
                    processed_count=len(self._processed_ids),
                    cursor=self._cursor,
                )
                workflow.continue_as_new(self._next_input(data))
            await self._wait(data)

    @workflow.signal
    def stop(self) -> None:
        self._stop_requested = True

    @workflow.query
    def ingestion_state(self) -> SourceIngestionState:
        return self._state()
