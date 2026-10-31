"""StoryPublishingWorkflow: publish one approved story package to one destination.

One run per story, destination and approved content hash. The workflow validates the package in
workflow code (pure), runs the destination activity with bounded retries and returns a typed
result. Expected failures return `publish_failed`; they never fail the workflow, so the story
stays publishable. It never touches the story generation workflow or the approved package.
"""

from collections.abc import Mapping
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, is_cancelled_exception

with workflow.unsafe.imports_passed_through():
    from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

    from ai_trend_pipeline.media.models import MAX_MEDIA_STORY_ID_CHARS, SHA256_PATTERN
    from ai_trend_pipeline.publishing.formatter import PublicationRejected, format_publication
    from ai_trend_pipeline.publishing.models import (
        RETRYABLE_CODES,
        DestinationName,
        PublicationErrorCode,
        PublicationInput,
        PublicationResult,
        publication_key,
    )
    from ai_trend_pipeline.publishing.registry import capabilities_for
    from ai_trend_pipeline.sources.models import ensure_aware_utc
    from ai_trend_pipeline.story_drafting.models import StoryPackage
    from ai_trend_pipeline.story_generation import STORY_ID_PREFIX, DecidedByText
    from ai_trend_pipeline.story_visibility import story_status_updates

STORY_PUBLISHING_TASK_QUEUE = "story-publishing-task-queue"
PUBLISH_STORY_ACTIVITY = "publish_story_to_bluesky"
PUBLISH_ACTIVITY_START_TO_CLOSE = timedelta(seconds=60)
# One maximum 15-minute rate-limit delay still fits, and the activity always ends.
PUBLISH_ACTIVITY_SCHEDULE_TO_CLOSE = timedelta(minutes=20)
PUBLISH_MAX_ATTEMPTS = 4
PUBLISH_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=60),
    maximum_attempts=PUBLISH_MAX_ATTEMPTS,
    non_retryable_error_types=[
        code.value for code in PublicationErrorCode if code not in RETRYABLE_CODES
    ],
)
PUBLISH_WORKFLOW_TIMEOUT = timedelta(minutes=30)

MAX_STORY_WORKFLOW_ID_CHARS = 64
MAX_CANDIDATE_ID_CHARS = 128
MAX_STATUS_DETAIL_CHARS = 200

DESTINATION_LABELS: Mapping[DestinationName, str] = {DestinationName.BLUESKY: "Bluesky"}

PUBLISHING_CHECKING_DETAIL = "Checking the approved story."
PUBLISHING_POSTING_DETAIL = f"Posting to {DESTINATION_LABELS[DestinationName.BLUESKY]}."
PUBLISHED_DETAIL = f"Published to {DESTINATION_LABELS[DestinationName.BLUESKY]}."
STOPPED_DETAIL = "Publishing stopped before it finished. Try again."

FAILURE_DETAILS: Mapping[PublicationErrorCode, str] = {
    PublicationErrorCode.CONTENT_HASH_MISMATCH: "The story changed after it was approved.",
    PublicationErrorCode.MANIFEST_MISMATCH: "The story card does not match the approved story.",
    PublicationErrorCode.POST_TEXT_TOO_LONG: "The post text is longer than Bluesky allows.",
    PublicationErrorCode.ALT_TEXT_TOO_LONG: "The image description is longer than Bluesky allows.",
    PublicationErrorCode.MEDIA_NOT_SUPPORTED: "Bluesky does not support the approved image.",
    PublicationErrorCode.MEDIA_TOO_LARGE: "The approved image is larger than Bluesky allows.",
    PublicationErrorCode.MEDIA_MISSING: "The approved image file is missing.",
    PublicationErrorCode.ARTIFACT_HASH_MISMATCH: "The approved image file was changed.",
    PublicationErrorCode.AUTH_FAILED: "Bluesky rejected the account credentials.",
    PublicationErrorCode.RATE_LIMITED: "Bluesky is limiting posts. Try again later.",
    PublicationErrorCode.UNAVAILABLE: "Bluesky could not be reached. Try again.",
    PublicationErrorCode.REJECTED: "Bluesky rejected the post.",
    PublicationErrorCode.UNEXPECTED_RESPONSE: "Bluesky sent an unexpected response. Try again.",
}


class PublicationStatus(StrEnum):
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "publish_failed"


class StoryPublishingInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    destination: DestinationName
    story_workflow_id: str = Field(
        min_length=len(STORY_ID_PREFIX) + 1, max_length=MAX_STORY_WORKFLOW_ID_CHARS
    )
    story_id: str = Field(min_length=1, max_length=MAX_MEDIA_STORY_ID_CHARS)
    candidate_id: str = Field(min_length=1, max_length=MAX_CANDIDATE_ID_CHARS)
    story_created_at: datetime
    package: StoryPackage
    content_hash: str = Field(pattern=SHA256_PATTERN)
    requested_by: DecidedByText | None = None

    @field_validator("story_created_at", mode="after")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return ensure_aware_utc(value)

    @model_validator(mode="after")
    def _check(self) -> "StoryPublishingInput":
        if not self.story_workflow_id.startswith(STORY_ID_PREFIX):
            raise ValueError("story_workflow_id must be a story generation workflow id")
        if self.package.story_id != self.story_id:
            raise ValueError("package.story_id must equal story_id")
        return self


class StoryPublishingState(BaseModel):
    """Query snapshot with query-safe defaults. It never holds post text or response bodies."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    publication_id: str = ""
    destination: DestinationName | None = None
    story_workflow_id: str = ""
    story_id: str = ""
    candidate_id: str = ""
    story_created_at: datetime | None = None
    content_hash: str | None = Field(default=None, pattern=SHA256_PATTERN)
    status: PublicationStatus = PublicationStatus.PUBLISHING
    status_detail: str | None = Field(default=None, max_length=MAX_STATUS_DETAIL_CHARS)
    error_code: PublicationErrorCode | None = None
    retryable: bool = False
    requested_by: str | None = None
    updated_at: datetime | None = None
    publication: PublicationResult | None = None

    @field_validator("story_created_at", "updated_at", mode="after")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return None if value is None else ensure_aware_utc(value)


class StoryPublishingResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    publication_id: str = Field(min_length=1)
    destination: DestinationName
    story_workflow_id: str = Field(min_length=1, max_length=MAX_STORY_WORKFLOW_ID_CHARS)
    story_id: str = Field(min_length=1, max_length=MAX_MEDIA_STORY_ID_CHARS)
    candidate_id: str = Field(min_length=1, max_length=MAX_CANDIDATE_ID_CHARS)
    story_created_at: datetime
    content_hash: str = Field(pattern=SHA256_PATTERN)
    status: Literal["published", "publish_failed"]
    status_detail: str | None = Field(default=None, max_length=MAX_STATUS_DETAIL_CHARS)
    error_code: PublicationErrorCode | None = None
    retryable: bool = False
    requested_by: str | None = None
    updated_at: datetime
    publication: PublicationResult | None = None

    @field_validator("story_created_at", "updated_at", mode="after")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return ensure_aware_utc(value)

    @model_validator(mode="after")
    def _check(self) -> "StoryPublishingResult":
        if (self.publication is not None) != (self.status == "published"):
            raise ValueError("publication is set if and only if the status is published")
        return self


def _failure_code(exc: ActivityError) -> PublicationErrorCode:
    cause = exc.cause
    if isinstance(cause, ApplicationError):
        try:
            return PublicationErrorCode(cause.type or "")
        except ValueError:
            return PublicationErrorCode.UNEXPECTED_RESPONSE
    if isinstance(cause, TimeoutError):
        return PublicationErrorCode.UNAVAILABLE
    return PublicationErrorCode.UNEXPECTED_RESPONSE


@workflow.defn
class StoryPublishingWorkflow:
    def __init__(self) -> None:
        self._state = StoryPublishingState()

    def _transition(
        self,
        status: PublicationStatus,
        detail: str,
        *,
        error_code: PublicationErrorCode | None = None,
        publication: PublicationResult | None = None,
    ) -> None:
        now = workflow.now()
        self._state = self._state.model_copy(
            update={
                "status": status,
                "status_detail": detail,
                "error_code": error_code,
                "retryable": error_code in RETRYABLE_CODES if error_code is not None else False,
                "updated_at": now,
                "publication": publication,
            }
        )
        # Temporal types upsert_search_attributes only partly; strict Pyright flags it as unknown.
        workflow.upsert_search_attributes(  # pyright: ignore[reportUnknownMemberType]
            story_status_updates(status.value, now, detail)
        )

    def _finish(self) -> StoryPublishingResult:
        state = self._state
        assert state.destination is not None and state.story_created_at is not None
        assert state.content_hash is not None and state.updated_at is not None
        status: Literal["published", "publish_failed"] = (
            "published" if state.status is PublicationStatus.PUBLISHED else "publish_failed"
        )
        return StoryPublishingResult(
            publication_id=state.publication_id,
            destination=state.destination,
            story_workflow_id=state.story_workflow_id,
            story_id=state.story_id,
            candidate_id=state.candidate_id,
            story_created_at=state.story_created_at,
            content_hash=state.content_hash,
            status=status,
            status_detail=state.status_detail,
            error_code=state.error_code,
            retryable=state.retryable,
            requested_by=state.requested_by,
            updated_at=state.updated_at,
            publication=state.publication,
        )

    def _fail(self, code: PublicationErrorCode) -> StoryPublishingResult:
        self._transition(PublicationStatus.FAILED, FAILURE_DETAILS[code], error_code=code)
        return self._finish()

    @workflow.run
    async def run(self, data: StoryPublishingInput) -> StoryPublishingResult:
        key = publication_key(data.story_id, data.destination, data.content_hash)
        self._state = StoryPublishingState(
            publication_id=key,
            destination=data.destination,
            story_workflow_id=data.story_workflow_id,
            story_id=data.story_id,
            candidate_id=data.candidate_id,
            story_created_at=data.story_created_at,
            content_hash=data.content_hash,
            requested_by=data.requested_by,
        )
        self._transition(PublicationStatus.PUBLISHING, PUBLISHING_CHECKING_DETAIL)

        if workflow.info().workflow_id != key:
            raise ApplicationError(
                "Publication key mismatch", type="PublicationKeyMismatch", non_retryable=True
            )

        try:
            format_publication(data.package, data.content_hash, capabilities_for(data.destination))
        except PublicationRejected as exc:
            return self._fail(exc.code)

        self._transition(
            PublicationStatus.PUBLISHING,
            f"Posting to {DESTINATION_LABELS[data.destination]}.",
        )
        try:
            result = await workflow.execute_activity(
                PUBLISH_STORY_ACTIVITY,
                PublicationInput(
                    destination=data.destination,
                    story_id=data.story_id,
                    candidate_id=data.candidate_id,
                    story_created_at=data.story_created_at,
                    requested_at=workflow.now(),
                    package=data.package,
                    content_hash=data.content_hash,
                ),
                result_type=PublicationResult,
                start_to_close_timeout=PUBLISH_ACTIVITY_START_TO_CLOSE,
                schedule_to_close_timeout=PUBLISH_ACTIVITY_SCHEDULE_TO_CLOSE,
                retry_policy=PUBLISH_RETRY_POLICY,
            )
        except ActivityError as exc:
            if is_cancelled_exception(exc):
                raise
            return self._fail(_failure_code(exc))

        if (
            result.idempotency_key != key
            or result.content_hash != data.content_hash
            or result.story_id != data.story_id
            or result.destination != data.destination
        ):
            return self._fail(PublicationErrorCode.UNEXPECTED_RESPONSE)
        self._transition(PublicationStatus.PUBLISHED, PUBLISHED_DETAIL, publication=result)
        return self._finish()

    @workflow.query
    def publication_state(self) -> StoryPublishingState:
        return self._state
