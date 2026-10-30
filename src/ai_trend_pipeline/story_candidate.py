"""StoryCandidateWorkflow: one durable review workflow per editorial story candidate."""

import hashlib
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any, Literal

from temporalio import workflow
from temporalio.common import (
    SearchAttributeKey,
    SearchAttributePair,
    SearchAttributeUpdate,
    TypedSearchAttributes,
    WorkflowIDReusePolicy,
)
from temporalio.exceptions import WorkflowAlreadyStartedError

with workflow.unsafe.imports_passed_through():
    from pydantic import (
        BaseModel,
        ConfigDict,
        Field,
        StringConstraints,
        field_validator,
    )

    from ai_trend_pipeline.research.models import StoryInput
    from ai_trend_pipeline.sources.models import (
        MAX_EXTERNAL_ID_LENGTH,
        MAX_METRICS,
        MAX_TITLE_LENGTH,
        HttpUrl1024,
        MetricKey,
        MetricValue,
        SourceName,
        ensure_aware_utc,
    )
    from ai_trend_pipeline.story_generation import (
        STORY_TASK_QUEUE,
        StoryGenerationInput,
        StoryGenerationStatus,
        StoryGenerationWorkflow,
        story_id_for,
    )
    from ai_trend_pipeline.story_status import (
        DEFAULT_STORY_REVIEW_TIMEOUT_S,
        STORY_UI_RETENTION,
    )
    from ai_trend_pipeline.story_visibility import (
        STORY_CANDIDATE_ID_ATTRIBUTE,
        STORY_CREATED_AT_ATTRIBUTE,
        STORY_SOURCE_ATTRIBUTE,
        STORY_STATUS_ATTRIBUTE,
        STORY_STATUS_DETAIL_ATTRIBUTE,
        STORY_TITLE_ATTRIBUTE,
        STORY_UPDATED_AT_ATTRIBUTE,
        bounded_detail,
        story_start_attributes,
    )

CANDIDATE_TASK_QUEUE = "story-candidate-task-queue"
CANDIDATE_ID_PREFIX = "candidate-v1-"
CANDIDATE_ID_HASH_LENGTH = 16
DEFAULT_CANDIDATE_REVIEW_TIMEOUT_S = 172800
MIN_CANDIDATE_REVIEW_TIMEOUT_S = 1
MAX_CANDIDATE_REVIEW_TIMEOUT_S = 604800
MAX_CANDIDATE_TEXT_LENGTH = 500
MAX_DECIDED_BY_LENGTH = 320
MAX_STORY_WORKFLOW_ID_LENGTH = 128
STORY_HANDOFF_PATCH = "story-generation-handoff-v1"
STORY_EXPIRED_DETAIL = "The review window closed."

CANDIDATE_STATUS_ATTRIBUTE_NAME = "CandidateStatus"
CANDIDATE_SOURCE_ATTRIBUTE_NAME = "CandidateSource"
CANDIDATE_STATUS_ATTRIBUTE = SearchAttributeKey.for_keyword(CANDIDATE_STATUS_ATTRIBUTE_NAME)
CANDIDATE_SOURCE_ATTRIBUTE = SearchAttributeKey.for_keyword(CANDIDATE_SOURCE_ATTRIBUTE_NAME)

CandidateText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_CANDIDATE_TEXT_LENGTH),
]
NoteText = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=MAX_CANDIDATE_TEXT_LENGTH)
]
DecidedByText = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=MAX_DECIDED_BY_LENGTH)
]


class CandidateStatus(StrEnum):
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


def candidate_id_for(source: SourceName, external_id: str) -> str:
    """Deterministic, versioned workflow id for one source item."""
    digest = hashlib.sha256(f"{source.value}\x1f{external_id}".encode()).hexdigest()
    return f"{CANDIDATE_ID_PREFIX}{digest[:CANDIDATE_ID_HASH_LENGTH]}"


# The SDK's upsert signature also accepts SearchAttributeUpdate[Unknown], so Pyright reports the
# member as partially unknown; this helper keeps both call sites fully typed.
def _upsert_status_attribute(status: CandidateStatus) -> None:
    workflow.upsert_search_attributes(  # pyright: ignore[reportUnknownMemberType]
        [CANDIDATE_STATUS_ATTRIBUTE.value_set(status.value)]
    )


def _shared_terminal_updates(
    status: CandidateStatus, candidate: "StoryCandidate", updated_at: datetime
) -> list[SearchAttributeUpdate[Any]]:
    """One upsert for a decided candidate: its own keys plus the shared story metadata."""
    # The update type is generic per value type; this list deliberately mixes keyword and datetime.
    updates: list[SearchAttributeUpdate[Any]] = [
        CANDIDATE_STATUS_ATTRIBUTE.value_set(status.value),
        STORY_STATUS_ATTRIBUTE.value_set(status.value),
        STORY_UPDATED_AT_ATTRIBUTE.value_set(updated_at),
        STORY_CREATED_AT_ATTRIBUTE.value_set(candidate.observed_at),
        STORY_TITLE_ATTRIBUTE.value_set(candidate.title),
        STORY_SOURCE_ATTRIBUTE.value_set(candidate.source.value),
        STORY_CANDIDATE_ID_ATTRIBUTE.value_set(
            candidate_id_for(candidate.source, candidate.external_id)
        ),
    ]
    detail = bounded_detail(STORY_EXPIRED_DETAIL if status is CandidateStatus.EXPIRED else None)
    if detail is not None:
        updates.append(STORY_STATUS_DETAIL_ATTRIBUTE.value_set(detail))
    return updates


class StoryCandidate(BaseModel):
    """Compact normalized story candidate; untrusted text stays data, never instructions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: SourceName
    external_id: str = Field(min_length=1, max_length=MAX_EXTERNAL_ID_LENGTH)
    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)
    url: HttpUrl1024 | None = None
    published_at: datetime | None = None
    observed_at: datetime
    summary: CandidateText
    proposed_angle: CandidateText
    selection_reason: CandidateText
    metrics: dict[MetricKey, MetricValue] = Field(default_factory=dict, max_length=MAX_METRICS)

    @field_validator("external_id", "title", mode="before")
    @classmethod
    def _strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("published_at", "observed_at", mode="after")
    @classmethod
    def _to_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else ensure_aware_utc(value)


def candidate_search_attributes(
    candidate: StoryCandidate, updated_at: datetime | None = None
) -> TypedSearchAttributes:
    """The candidate keys plus the seven shared story keys the list pages read."""
    stamp = candidate.observed_at if updated_at is None else ensure_aware_utc(updated_at)
    candidate_id = candidate_id_for(candidate.source, candidate.external_id)
    return TypedSearchAttributes(
        [
            SearchAttributePair(CANDIDATE_STATUS_ATTRIBUTE, CandidateStatus.AWAITING_REVIEW.value),
            SearchAttributePair(CANDIDATE_SOURCE_ATTRIBUTE, candidate.source.value),
            SearchAttributePair(STORY_STATUS_ATTRIBUTE, CandidateStatus.AWAITING_REVIEW.value),
            SearchAttributePair(STORY_TITLE_ATTRIBUTE, candidate.title),
            SearchAttributePair(STORY_SOURCE_ATTRIBUTE, candidate.source.value),
            SearchAttributePair(STORY_CANDIDATE_ID_ATTRIBUTE, candidate_id),
            SearchAttributePair(STORY_CREATED_AT_ATTRIBUTE, candidate.observed_at),
            SearchAttributePair(STORY_UPDATED_AT_ATTRIBUTE, stamp),
            SearchAttributePair(STORY_STATUS_DETAIL_ATTRIBUTE, ""),
        ]
    )


def story_input_for(candidate: StoryCandidate) -> StoryInput:
    """Pure mapping from the compact candidate to the research/writing contract."""
    return StoryInput(
        candidate_id=candidate_id_for(candidate.source, candidate.external_id),
        source=candidate.source,
        external_id=candidate.external_id,
        title=candidate.title,
        url=candidate.url,
        published_at=candidate.published_at,
        observed_at=candidate.observed_at,
        summary=candidate.summary,
        proposed_angle=candidate.proposed_angle,
        selection_reason=candidate.selection_reason,
        metrics=dict(candidate.metrics),
    )


def candidate_memo(candidate: StoryCandidate) -> dict[str, str]:
    """The compact inbox fields, identical for direct and ingestion-started candidates."""
    return {
        "candidate_title": candidate.title,
        "candidate_proposed_angle": candidate.proposed_angle,
        "candidate_observed_at": candidate.observed_at.isoformat(),
    }


class StoryCandidateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate: StoryCandidate
    review_timeout_seconds: int = Field(
        default=DEFAULT_CANDIDATE_REVIEW_TIMEOUT_S,
        ge=MIN_CANDIDATE_REVIEW_TIMEOUT_S,
        le=MAX_CANDIDATE_REVIEW_TIMEOUT_S,
    )
    story_review_timeout_seconds: int = Field(
        default=DEFAULT_STORY_REVIEW_TIMEOUT_S,
        ge=1,
        le=604800,
    )


class CandidateDecision(BaseModel):
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


class CandidateReviewState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CandidateStatus
    candidate: StoryCandidate
    note: str = Field(default="", max_length=MAX_CANDIDATE_TEXT_LENGTH)
    decided_by: str | None = Field(default=None, max_length=MAX_DECIDED_BY_LENGTH)
    decided_at: datetime | None = None
    story_workflow_id: str | None = Field(default=None, max_length=MAX_STORY_WORKFLOW_ID_LENGTH)


class StoryCandidateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["approved", "rejected", "expired"]
    candidate: StoryCandidate
    note: str = Field(default="", max_length=MAX_CANDIDATE_TEXT_LENGTH)
    decided_by: str | None = Field(default=None, max_length=MAX_DECIDED_BY_LENGTH)
    decided_at: datetime | None = None
    story_workflow_id: str | None = Field(default=None, max_length=MAX_STORY_WORKFLOW_ID_LENGTH)


@workflow.defn
class StoryCandidateWorkflow:
    """Wait for a human Approve/Reject decision or expire the candidate."""

    def __init__(self) -> None:
        self._status = CandidateStatus.AWAITING_REVIEW
        self._candidate: StoryCandidate | None = None
        self._decision: CandidateDecision | None = None
        self._decision_status: CandidateStatus | None = None
        self._note = ""
        self._decided_by: str | None = None
        self._decided_at: datetime | None = None
        self._story_workflow_id: str | None = None
        self._story_review_timeout_seconds = DEFAULT_STORY_REVIEW_TIMEOUT_S

    def _review_state(self) -> CandidateReviewState:
        candidate = self._candidate
        assert candidate is not None
        return CandidateReviewState(
            status=self._status,
            candidate=candidate,
            note=self._note,
            decided_by=self._decided_by,
            decided_at=self._decided_at,
            story_workflow_id=self._story_workflow_id,
        )

    def _record(self, status: CandidateStatus, decision: CandidateDecision) -> None:
        if self._decision is not None or self._status is not CandidateStatus.AWAITING_REVIEW:
            workflow.logger.warning(
                "Ignoring %s decision; status is %s", status.value, self._status.value
            )
            return
        self._decision = decision
        self._decision_status = status

    def _record_terminal(self, status: CandidateStatus, note: str, decided_by: str | None) -> None:
        self._status = status
        self._note = note
        self._decided_by = decided_by
        self._decided_at = workflow.now()

    def _result_with(
        self, status: CandidateStatus, note: str, decided_by: str | None
    ) -> StoryCandidateResult:
        candidate = self._candidate
        assert candidate is not None
        result_status: Literal["approved", "rejected", "expired"] = (
            "approved"
            if status is CandidateStatus.APPROVED
            else "rejected"
            if status is CandidateStatus.REJECTED
            else "expired"
        )
        return StoryCandidateResult(
            status=result_status,
            candidate=candidate,
            note=note,
            decided_by=decided_by,
            decided_at=self._decided_at,
            story_workflow_id=self._story_workflow_id,
        )

    def _expire_old(self) -> StoryCandidateResult:
        self._status = CandidateStatus.EXPIRED
        self._decided_at = workflow.now()
        _upsert_status_attribute(CandidateStatus.EXPIRED)
        return self._result_with(CandidateStatus.EXPIRED, "", None)

    def _finish_old(
        self,
        status: CandidateStatus,
        note: str,
        decided_by: str | None,
    ) -> StoryCandidateResult:
        self._record_terminal(status, note, decided_by)
        _upsert_status_attribute(status)
        return self._result_with(status, note, decided_by)

    async def _finish_new(
        self,
        status: CandidateStatus,
        note: str,
        decided_by: str | None,
    ) -> StoryCandidateResult:
        candidate = self._candidate
        assert candidate is not None
        self._record_terminal(status, note, decided_by)
        decided_at = self._decided_at
        assert decided_at is not None
        workflow.upsert_search_attributes(  # pyright: ignore[reportUnknownMemberType]
            _shared_terminal_updates(status, candidate, decided_at)
        )
        if status is CandidateStatus.APPROVED:
            story = story_input_for(candidate)
            story_id = story_id_for(story.candidate_id)
            self._story_workflow_id = story_id
            try:
                await workflow.start_child_workflow(
                    StoryGenerationWorkflow.run,
                    StoryGenerationInput(
                        candidate_id=story.candidate_id,
                        story=story,
                        story_created_at=candidate.observed_at,
                        review_timeout_seconds=self._story_review_timeout_seconds,
                    ),
                    id=story_id,
                    task_queue=STORY_TASK_QUEUE,
                    parent_close_policy=workflow.ParentClosePolicy.ABANDON,
                    id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                    search_attributes=story_start_attributes(
                        StoryGenerationStatus.RESEARCHING.value,
                        candidate.title,
                        candidate.source.value,
                        story.candidate_id,
                        candidate.observed_at,
                        workflow.now(),
                    ),
                )
            except WorkflowAlreadyStartedError:
                workflow.logger.warning(
                    "Story workflow already started; keeping its deterministic id"
                )
        return self._result_with(status, note, decided_by)

    @workflow.run
    async def run(self, data: StoryCandidateInput) -> StoryCandidateResult:
        self._candidate = data.candidate
        self._status = CandidateStatus.AWAITING_REVIEW
        self._story_review_timeout_seconds = data.story_review_timeout_seconds
        remaining = (
            data.candidate.observed_at + STORY_UI_RETENTION - workflow.now()
        ).total_seconds()
        effective_timeout = max(0.0, min(float(data.review_timeout_seconds), remaining))
        try:
            if effective_timeout <= 0:
                raise TimeoutError
            await workflow.wait_condition(
                lambda: self._decision is not None,
                timeout=timedelta(seconds=effective_timeout),
            )
        except TimeoutError:
            if workflow.patched(STORY_HANDOFF_PATCH):
                return await self._finish_new(CandidateStatus.EXPIRED, "", None)
            return self._expire_old()
        decision = self._decision
        assert decision is not None
        decision_status = self._decision_status
        assert decision_status is not None
        if workflow.patched(STORY_HANDOFF_PATCH):
            return await self._finish_new(decision_status, decision.note, decision.decided_by)
        return self._finish_old(decision_status, decision.note, decision.decided_by)

    @workflow.signal
    def approve(self, decision: CandidateDecision) -> None:
        self._record(CandidateStatus.APPROVED, decision)

    @workflow.signal
    def reject(self, decision: CandidateDecision) -> None:
        self._record(CandidateStatus.REJECTED, decision)

    @workflow.query
    def review_state(self) -> CandidateReviewState:
        return self._review_state()
