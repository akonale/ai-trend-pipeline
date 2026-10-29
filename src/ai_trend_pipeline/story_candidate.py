"""StoryCandidateWorkflow: one durable review workflow per editorial story candidate."""

import hashlib
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal

from temporalio import workflow
from temporalio.common import SearchAttributeKey, SearchAttributePair, TypedSearchAttributes

with workflow.unsafe.imports_passed_through():
    from pydantic import (
        BaseModel,
        ConfigDict,
        Field,
        StringConstraints,
        field_validator,
    )

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

CANDIDATE_TASK_QUEUE = "story-candidate-task-queue"
CANDIDATE_ID_PREFIX = "candidate-v1-"
CANDIDATE_ID_HASH_LENGTH = 16
DEFAULT_CANDIDATE_REVIEW_TIMEOUT_S = 172800
MIN_CANDIDATE_REVIEW_TIMEOUT_S = 1
MAX_CANDIDATE_REVIEW_TIMEOUT_S = 604800
MAX_CANDIDATE_TEXT_LENGTH = 500
MAX_DECIDED_BY_LENGTH = 320

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


def candidate_search_attributes(candidate: StoryCandidate) -> TypedSearchAttributes:
    """The attributes every started candidate must carry so the inbox can list it."""
    return TypedSearchAttributes(
        [
            SearchAttributePair(CANDIDATE_STATUS_ATTRIBUTE, CandidateStatus.AWAITING_REVIEW.value),
            SearchAttributePair(CANDIDATE_SOURCE_ATTRIBUTE, candidate.source.value),
        ]
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


class StoryCandidateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["approved", "rejected", "expired"]
    candidate: StoryCandidate
    note: str = Field(default="", max_length=MAX_CANDIDATE_TEXT_LENGTH)
    decided_by: str | None = Field(default=None, max_length=MAX_DECIDED_BY_LENGTH)
    decided_at: datetime | None = None


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

    def _review_state(self) -> CandidateReviewState:
        candidate = self._candidate
        assert candidate is not None
        return CandidateReviewState(
            status=self._status,
            candidate=candidate,
            note=self._note,
            decided_by=self._decided_by,
            decided_at=self._decided_at,
        )

    def _record(self, status: CandidateStatus, decision: CandidateDecision) -> None:
        if self._decision is not None or self._status is not CandidateStatus.AWAITING_REVIEW:
            workflow.logger.warning(
                "Ignoring %s decision; status is %s", status.value, self._status.value
            )
            return
        self._decision = decision
        self._decision_status = status

    def _finish(
        self,
        status: CandidateStatus,
        note: str,
        decided_by: str | None,
    ) -> StoryCandidateResult:
        candidate = self._candidate
        assert candidate is not None
        self._status = status
        self._note = note
        self._decided_by = decided_by
        self._decided_at = workflow.now()
        _upsert_status_attribute(status)
        result_status: Literal["approved", "rejected", "expired"] = (
            "approved" if status is CandidateStatus.APPROVED else "rejected"
        )
        return StoryCandidateResult(
            status=result_status,
            candidate=candidate,
            note=note,
            decided_by=decided_by,
            decided_at=self._decided_at,
        )

    @workflow.run
    async def run(self, data: StoryCandidateInput) -> StoryCandidateResult:
        self._candidate = data.candidate
        self._status = CandidateStatus.AWAITING_REVIEW
        try:
            await workflow.wait_condition(
                lambda: self._decision is not None,
                timeout=timedelta(seconds=data.review_timeout_seconds),
            )
        except TimeoutError:
            self._status = CandidateStatus.EXPIRED
            self._decided_at = workflow.now()
            _upsert_status_attribute(CandidateStatus.EXPIRED)
            return StoryCandidateResult(
                status="expired",
                candidate=data.candidate,
                note="",
                decided_by=None,
                decided_at=self._decided_at,
            )
        decision = self._decision
        assert decision is not None
        decision_status = self._decision_status
        assert decision_status is not None
        return self._finish(decision_status, decision.note, decision.decided_by)

    @workflow.signal
    def approve(self, decision: CandidateDecision) -> None:
        self._record(CandidateStatus.APPROVED, decision)

    @workflow.signal
    def reject(self, decision: CandidateDecision) -> None:
        self._record(CandidateStatus.REJECTED, decision)

    @workflow.query
    def review_state(self) -> CandidateReviewState:
        return self._review_state()
