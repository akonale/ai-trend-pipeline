"""StoryGenerationWorkflow: turn an approved candidate into a review-ready StoryPackage.

The workflow is deterministic: every HTTP, LLM and clock operation happens in an activity or
through Temporal's deterministic APIs. Nothing here publishes anything.
"""

import hashlib
from collections.abc import Callable
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal, TypeVar, cast

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, is_cancelled_exception

with workflow.unsafe.imports_passed_through():
    from pydantic import (
        BaseModel,
        ConfigDict,
        Field,
        StringConstraints,
        field_validator,
        model_validator,
    )

    from ai_trend_pipeline.analysis.calculations import fact_input_for
    from ai_trend_pipeline.analysis.models import FactSet, FactSetStatus
    from ai_trend_pipeline.research.models import (
        ResearchFailureCode,
        ResearchInput,
        ResearchOutput,
        ResearchStatus,
        StoryInput,
    )
    from ai_trend_pipeline.sources.models import ensure_aware_utc
    from ai_trend_pipeline.story_drafting.models import (
        STORY_CRITIQUE_INVALID_ERROR_TYPE,
        STORY_CRITIQUE_TOOL_MISSING_ERROR_TYPE,
        STORY_DRAFT_INVALID_ERROR_TYPE,
        CritiqueInput,
        CritiqueOutput,
        DraftOutput,
        RevisionRequest,
        StoryPackage,
        draft_input_for,
    )
    from ai_trend_pipeline.story_status import (
        DEFAULT_STORY_REVIEW_TIMEOUT_S,
        STORY_UI_RETENTION,
    )
    from ai_trend_pipeline.story_visibility import bounded_detail, story_status_updates

STORY_TASK_QUEUE = "story-generation-task-queue"
STORY_ID_PREFIX = "story-v1-"
STORY_ID_HASH_LENGTH = 16
STORY_DRAFT_MAX_FACTS = 16

MIN_REVIEW_TIMEOUT_S = 1
MAX_REVIEW_TIMEOUT_S = 604800
MAX_CANDIDATE_ID_CHARS = 128
MAX_STORY_ID_CHARS = 64
MAX_DECIDED_BY_CHARS = 320
MAX_NOTE_CHARS = 500
MAX_STATUS_DETAIL_CHARS = 200

RESEARCH_STORY_ACTIVITY = "research_story"
CALCULATE_FACTS_ACTIVITY = "calculate_facts"
DRAFT_STORY_ACTIVITY = "draft_story"
CRITIQUE_STORY_ACTIVITY = "critique_story"

RESEARCH_ACTIVITY_TIMEOUT = timedelta(seconds=120)
FACTS_ACTIVITY_TIMEOUT = timedelta(seconds=30)
DRAFT_ACTIVITY_TIMEOUT = timedelta(seconds=120)
CRITIQUE_ACTIVITY_TIMEOUT = timedelta(seconds=120)
ACTIVITY_MAX_ATTEMPTS = 3

UNSUPPORTED_NO_SOURCE_DETAIL = "The candidate has no source link."
UNSUPPORTED_UNSAFE_SOURCE_DETAIL = "The source link could not be used safely."
UNSUPPORTED_ARTICLE_DETAIL = "The original article is no longer available."
UNSUPPORTED_EVIDENCE_DETAIL = "There was not enough readable evidence to support this story."
UNSUPPORTED_GENERIC_DETAIL = "The story could not be supported with the available evidence."
FAILED_VALIDATION_DETAIL = "The draft did not pass factual validation."
FAILED_WRITER_DETAIL = "The writer did not return a usable draft."
FAILED_CHECKER_DETAIL = "The checker did not return a usable result."
FAILED_RESEARCH_DETAIL = "The research step failed after retries."
FAILED_ANALYSIS_DETAIL = "The analysis step failed after retries."
FAILED_AFTER_REVISION_DETAIL = "The story did not pass checking after one revision."
FAILED_GENERIC_DETAIL = "Story generation failed."
EXPIRED_DETAIL = "The review window closed."

_UNSAFE_SOURCE_CODES = frozenset(
    {
        ResearchFailureCode.UNSAFE_URL,
        ResearchFailureCode.BLOCKED_NETWORK,
        ResearchFailureCode.TOO_MANY_REDIRECTS,
    }
)
_ARTICLE_CODES = frozenset(
    {
        ResearchFailureCode.MISSING_ARTICLE,
        ResearchFailureCode.FETCH_FAILED,
        ResearchFailureCode.UNSUPPORTED_CONTENT_TYPE,
    }
)
_EVIDENCE_CODES = frozenset(
    {
        ResearchFailureCode.MALFORMED_CONTENT,
        ResearchFailureCode.INSUFFICIENT_EVIDENCE,
    }
)

CandidateIdText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_CANDIDATE_ID_CHARS)
]
NoteText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=MAX_NOTE_CHARS)]
DecidedByText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_DECIDED_BY_CHARS),
]

T = TypeVar("T")
FailureDetail = Callable[[ActivityError], str]


def story_id_for(candidate_id: str) -> str:
    """Deterministic, versioned story workflow id for one candidate workflow id."""
    digest = hashlib.sha256(candidate_id.encode("utf-8")).hexdigest()
    return f"{STORY_ID_PREFIX}{digest[:STORY_ID_HASH_LENGTH]}"


class StoryGenerationStatus(StrEnum):
    RESEARCHING = "researching"
    ANALYZING = "analyzing"
    WRITING = "writing"
    CHECKING = "checking"
    REVIEW = "review"
    READY = "ready"
    REJECTED = "rejected"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"
    EXPIRED = "expired"


class StoryGenerationInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: CandidateIdText
    story: StoryInput
    story_created_at: datetime
    review_timeout_seconds: int = Field(
        default=DEFAULT_STORY_REVIEW_TIMEOUT_S,
        ge=MIN_REVIEW_TIMEOUT_S,
        le=MAX_REVIEW_TIMEOUT_S,
    )

    @field_validator("candidate_id", mode="before")
    @classmethod
    def _strip_candidate_id(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("story_created_at", mode="after")
    @classmethod
    def _to_utc(cls, value: datetime) -> datetime:
        return ensure_aware_utc(value)

    @model_validator(mode="after")
    def _check_story(self) -> "StoryGenerationInput":
        if self.story.candidate_id != self.candidate_id:
            raise ValueError("story.candidate_id must equal candidate_id")
        if self.story_created_at != self.story.observed_at:
            raise ValueError("story_created_at must equal story.observed_at")
        return self


class FinalStoryDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    note: NoteText = ""
    decided_by: DecidedByText | None = None

    @field_validator("decided_by", mode="before")
    @classmethod
    def _blank_decided_by(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value


class StoryGenerationState(BaseModel):
    """Query snapshot with query-safe defaults; it never carries prompts or evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    story_id: str = Field(default="", max_length=MAX_STORY_ID_CHARS)
    candidate_id: str = Field(default="", max_length=MAX_CANDIDATE_ID_CHARS)
    story_created_at: datetime | None = None
    updated_at: datetime | None = None
    status: StoryGenerationStatus = StoryGenerationStatus.RESEARCHING
    status_detail: str | None = Field(default=None, max_length=MAX_STATUS_DETAIL_CHARS)
    package: StoryPackage | None = None
    critique: CritiqueOutput | None = None
    decided_by: str | None = Field(default=None, max_length=MAX_DECIDED_BY_CHARS)
    note: str = Field(default="", max_length=MAX_NOTE_CHARS)
    decided_at: datetime | None = None

    @field_validator("story_created_at", "updated_at", "decided_at", mode="after")
    @classmethod
    def _to_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else ensure_aware_utc(value)


class StoryGenerationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["ready", "rejected", "unsupported", "failed", "expired"]
    story_id: str = Field(min_length=1, max_length=MAX_STORY_ID_CHARS)
    candidate_id: str = Field(min_length=1, max_length=MAX_CANDIDATE_ID_CHARS)
    story_created_at: datetime
    updated_at: datetime
    status_detail: str | None = Field(default=None, max_length=MAX_STATUS_DETAIL_CHARS)
    package: StoryPackage | None = None
    critique: CritiqueOutput | None = None
    decided_by: str | None = Field(default=None, max_length=MAX_DECIDED_BY_CHARS)
    note: str = Field(default="", max_length=MAX_NOTE_CHARS)
    decided_at: datetime | None = None

    @field_validator("story_created_at", "updated_at", "decided_at", mode="after")
    @classmethod
    def _to_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else ensure_aware_utc(value)


def _application_error_type(exc: ActivityError) -> str | None:
    cause = exc.cause
    return cause.type if isinstance(cause, ApplicationError) else None


def _draft_failure_detail(exc: ActivityError) -> str:
    if _application_error_type(exc) == STORY_DRAFT_INVALID_ERROR_TYPE:
        return FAILED_VALIDATION_DETAIL
    return FAILED_WRITER_DETAIL


def _checker_failure_detail(exc: ActivityError) -> str:
    error_type = _application_error_type(exc)
    if error_type in {
        STORY_CRITIQUE_INVALID_ERROR_TYPE,
        STORY_CRITIQUE_TOOL_MISSING_ERROR_TYPE,
    }:
        return FAILED_CHECKER_DETAIL
    return FAILED_CHECKER_DETAIL


def _unsupported_detail(research: ResearchOutput) -> str:
    failures = research.failures
    if not failures:
        return UNSUPPORTED_GENERIC_DETAIL
    code = failures[0].code
    if code is ResearchFailureCode.NO_SOURCE_URL:
        return UNSUPPORTED_NO_SOURCE_DETAIL
    if code in _UNSAFE_SOURCE_CODES:
        return UNSUPPORTED_UNSAFE_SOURCE_DETAIL
    if code in _ARTICLE_CODES:
        return UNSUPPORTED_ARTICLE_DETAIL
    if code in _EVIDENCE_CODES:
        return UNSUPPORTED_EVIDENCE_DETAIL
    return UNSUPPORTED_GENERIC_DETAIL


@workflow.defn
class StoryGenerationWorkflow:
    """Research, analyze, draft, check once at most, then wait for a final human decision."""

    def __init__(self) -> None:
        self._story_id = ""
        self._candidate_id = ""
        self._story_created_at: datetime | None = None
        self._updated_at: datetime | None = None
        self._status = StoryGenerationStatus.RESEARCHING
        self._status_detail: str | None = None
        self._package: StoryPackage | None = None
        self._critique: CritiqueOutput | None = None
        self._decided_by: str | None = None
        self._note = ""
        self._decided_at: datetime | None = None
        self._decision: FinalStoryDecision | None = None
        self._decision_status: StoryGenerationStatus | None = None
        self._failure_detail = FAILED_GENERIC_DETAIL

    def _state(self) -> StoryGenerationState:
        return StoryGenerationState(
            story_id=self._story_id,
            candidate_id=self._candidate_id,
            story_created_at=self._story_created_at,
            updated_at=self._updated_at,
            status=self._status,
            status_detail=self._status_detail,
            package=self._package,
            critique=self._critique,
            decided_by=self._decided_by,
            note=self._note,
            decided_at=self._decided_at,
        )

    def _transition(self, status: StoryGenerationStatus, detail: str | None = None) -> None:
        self._status = status
        self._updated_at = workflow.now()
        if detail is not None:
            self._status_detail = bounded_detail(detail)
        workflow.upsert_search_attributes(  # pyright: ignore[reportUnknownMemberType]
            story_status_updates(status.value, self._updated_at, self._status_detail)
        )

    def _result(
        self, status: Literal["ready", "rejected", "unsupported", "failed", "expired"]
    ) -> StoryGenerationResult:
        created = self._story_created_at
        updated = self._updated_at
        assert created is not None and updated is not None
        return StoryGenerationResult(
            status=status,
            story_id=self._story_id,
            candidate_id=self._candidate_id,
            story_created_at=created,
            updated_at=updated,
            status_detail=self._status_detail,
            package=self._package,
            critique=self._critique,
            decided_by=self._decided_by,
            note=self._note,
            decided_at=self._decided_at,
        )

    def _finish(
        self,
        internal: StoryGenerationStatus,
        result_status: Literal["ready", "rejected", "unsupported", "failed", "expired"],
        detail: str | None = None,
        *,
        package: StoryPackage | None = None,
        critique: CritiqueOutput | None = None,
    ) -> StoryGenerationResult:
        self._transition(internal, detail)
        self._package = package
        self._critique = critique
        return self._result(result_status)

    def _expire(self) -> StoryGenerationResult:
        return self._finish(StoryGenerationStatus.EXPIRED, "expired", EXPIRED_DETAIL)

    def _failed(self) -> StoryGenerationResult:
        return self._finish(StoryGenerationStatus.FAILED, "failed", self._failure_detail)

    def _unsupported(self, detail: str) -> StoryGenerationResult:
        return self._finish(StoryGenerationStatus.UNSUPPORTED, "unsupported", detail)

    def _review_deadline(self) -> datetime:
        created = self._story_created_at
        assert created is not None
        return created + STORY_UI_RETENTION

    def _deadline_passed(self) -> bool:
        return self._review_deadline() <= workflow.now()

    def _record(self, status: StoryGenerationStatus, decision: FinalStoryDecision) -> None:
        if self._decision is not None or self._status is not StoryGenerationStatus.REVIEW:
            workflow.logger.warning(
                "Ignoring %s decision; status is %s", status.value, self._status.value
            )
            return
        self._decision = decision
        self._decision_status = status

    async def _execute(
        self,
        activity_name: str,
        data: BaseModel,
        timeout: timedelta,
        result_type: type[T],
        failure_detail: FailureDetail,
    ) -> T | None:
        try:
            # The string-name overload returns Any; bind it to the declared result model.
            return cast(
                T,
                await workflow.execute_activity(
                    activity_name,
                    data,
                    start_to_close_timeout=timeout,
                    retry_policy=RetryPolicy(maximum_attempts=ACTIVITY_MAX_ATTEMPTS),
                    result_type=result_type,
                ),
            )
        except ActivityError as exc:
            if is_cancelled_exception(exc):
                raise
            self._failure_detail = failure_detail(exc)
            return None

    async def _review(
        self,
        data: StoryGenerationInput,
        package: StoryPackage,
        critique: CritiqueOutput,
    ) -> StoryGenerationResult:
        if self._deadline_passed():
            return self._expire()
        remaining = (self._review_deadline() - workflow.now()).total_seconds()
        effective_timeout = min(float(data.review_timeout_seconds), remaining)
        if effective_timeout <= 0:
            return self._expire()
        self._transition(StoryGenerationStatus.REVIEW)
        try:
            await workflow.wait_condition(
                lambda: self._decision is not None,
                timeout=timedelta(seconds=effective_timeout),
            )
        except TimeoutError:
            return self._expire()

        decision = self._decision
        decision_status = self._decision_status
        assert decision is not None and decision_status is not None
        self._decided_by = decision.decided_by
        self._note = decision.note
        self._decided_at = workflow.now()
        if decision_status is StoryGenerationStatus.READY:
            return self._finish(
                StoryGenerationStatus.READY,
                "ready",
                package=package,
                critique=critique,
            )
        return self._finish(
            StoryGenerationStatus.REJECTED,
            "rejected",
            package=package,
            critique=critique,
        )

    @workflow.run
    async def run(self, data: StoryGenerationInput) -> StoryGenerationResult:
        self._story_id = story_id_for(data.candidate_id)
        self._candidate_id = data.candidate_id
        self._story_created_at = data.story_created_at
        self._updated_at = workflow.now()

        if self._deadline_passed():
            return self._expire()
        self._transition(StoryGenerationStatus.RESEARCHING)

        research = await self._execute(
            RESEARCH_STORY_ACTIVITY,
            ResearchInput(story=data.story),
            RESEARCH_ACTIVITY_TIMEOUT,
            ResearchOutput,
            lambda _exc: FAILED_RESEARCH_DETAIL,
        )
        if research is None:
            return self._failed()
        if self._deadline_passed():
            return self._expire()
        if research.status is ResearchStatus.UNSUPPORTED:
            return self._unsupported(_unsupported_detail(research))

        self._transition(StoryGenerationStatus.ANALYZING)
        fact_input = fact_input_for(research, data.story).model_copy(
            update={"max_facts": STORY_DRAFT_MAX_FACTS}
        )
        facts = await self._execute(
            CALCULATE_FACTS_ACTIVITY,
            fact_input,
            FACTS_ACTIVITY_TIMEOUT,
            FactSet,
            lambda _exc: FAILED_ANALYSIS_DETAIL,
        )
        if facts is None:
            return self._failed()
        if self._deadline_passed():
            return self._expire()
        if facts.status is FactSetStatus.UNSUPPORTED:
            return self._unsupported(UNSUPPORTED_GENERIC_DETAIL)

        self._transition(StoryGenerationStatus.WRITING)
        try:
            draft_input = draft_input_for(data.story, research, facts)
        except ValueError:
            self._failure_detail = FAILED_GENERIC_DETAIL
            return self._failed()
        draft = await self._execute(
            DRAFT_STORY_ACTIVITY,
            draft_input,
            DRAFT_ACTIVITY_TIMEOUT,
            DraftOutput,
            _draft_failure_detail,
        )
        if draft is None:
            return self._failed()
        if self._deadline_passed():
            return self._expire()

        self._transition(StoryGenerationStatus.CHECKING)
        check = await self._execute(
            CRITIQUE_STORY_ACTIVITY,
            CritiqueInput(draft_input=draft_input, draft=draft.draft, round=0),
            CRITIQUE_ACTIVITY_TIMEOUT,
            CritiqueOutput,
            _checker_failure_detail,
        )
        if check is None:
            return self._failed()
        if self._deadline_passed():
            return self._expire()

        if check.passed:
            return await self._review(data, draft.draft, check)

        revision = RevisionRequest(prior_draft=draft.draft, critique=check, round=1)
        revised_input = draft_input.model_copy(update={"revision": revision})
        revised = await self._execute(
            DRAFT_STORY_ACTIVITY,
            revised_input,
            DRAFT_ACTIVITY_TIMEOUT,
            DraftOutput,
            _draft_failure_detail,
        )
        if revised is None:
            return self._failed()
        if self._deadline_passed():
            return self._expire()

        revised_check = await self._execute(
            CRITIQUE_STORY_ACTIVITY,
            CritiqueInput(draft_input=revised_input, draft=revised.draft, round=1),
            CRITIQUE_ACTIVITY_TIMEOUT,
            CritiqueOutput,
            _checker_failure_detail,
        )
        if revised_check is None:
            return self._failed()
        if not revised_check.passed:
            return self._finish(
                StoryGenerationStatus.FAILED, "failed", FAILED_AFTER_REVISION_DETAIL
            )
        if self._deadline_passed():
            return self._expire()
        return await self._review(data, revised.draft, revised_check)

    @workflow.signal
    def approve(self, decision: FinalStoryDecision) -> None:
        self._record(StoryGenerationStatus.READY, decision)

    @workflow.signal
    def reject(self, decision: FinalStoryDecision) -> None:
        self._record(StoryGenerationStatus.REJECTED, decision)

    @workflow.query
    def story_state(self) -> StoryGenerationState:
        return self._state()
