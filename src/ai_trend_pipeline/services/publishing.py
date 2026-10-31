"""Publish use case: start one publishing workflow for one Ready story and read its state.

Only an explicit request starts publishing. Nothing in candidate or final approval calls it.
Workflows and activities never import this module.
"""

from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from ai_trend_pipeline.media.models import SHA256_PATTERN
from ai_trend_pipeline.publishing.models import DestinationName, publication_key
from ai_trend_pipeline.services.stories import (
    MAX_DETAIL_ID_CHARS,
    StoryDetailError,
    StoryDetailExpired,
    StoryDetailKind,
    StoryDetailMalformed,
    retained,
    story_detail_kind,
)
from ai_trend_pipeline.story_generation import (
    DecidedByText,
    StoryGenerationResult,
    StoryGenerationState,
    StoryGenerationStatus,
)
from ai_trend_pipeline.story_publishing import (
    PUBLISHING_CHECKING_DETAIL,
    PublicationStatus,
    StoryPublishingInput,
    StoryPublishingState,
)
from ai_trend_pipeline.story_status import STORY_UI_RETENTION


class StoryNotPublishable(StoryDetailError):
    def __init__(self, detail_id: str) -> None:
        super().__init__(f"story {detail_id} is not a Ready story")
        self.detail_id = detail_id


class StoryPublishStale(StoryDetailError):
    def __init__(self, detail_id: str) -> None:
        super().__init__(f"story {detail_id} changed after it was approved")
        self.detail_id = detail_id


class PublishStoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detail_id: str = Field(min_length=1, max_length=MAX_DETAIL_ID_CHARS)
    content_hash: str = Field(pattern=SHA256_PATTERN)
    destination: DestinationName = DestinationName.BLUESKY
    requested_by: DecidedByText | None = None


class StoryPublishingGateway(Protocol):
    async def story_state(self, workflow_id: str) -> StoryGenerationState: ...

    async def story_result(self, workflow_id: str) -> StoryGenerationResult: ...

    async def publication(self, publication_id: str) -> StoryPublishingState | None: ...

    async def start(self, data: StoryPublishingInput) -> None: ...


_ACTIVE = frozenset({PublicationStatus.PUBLISHING, PublicationStatus.PUBLISHED})


def _aware_moment(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    return now.astimezone(UTC)


class StoryPublishingService:
    def __init__(self, gateway: StoryPublishingGateway) -> None:
        self._gateway = gateway

    async def _ready_state(self, detail_id: str, now: datetime) -> StoryGenerationState:
        moment = _aware_moment(now)
        if story_detail_kind(detail_id) is not StoryDetailKind.GENERATION:
            raise StoryNotPublishable(detail_id)
        state = await self._gateway.story_state(detail_id)
        if state.story_created_at is None:
            raise StoryDetailMalformed(detail_id)
        if not retained(state.story_created_at, moment - STORY_UI_RETENTION):
            raise StoryDetailExpired(detail_id)
        return state

    async def publish(self, request: PublishStoryRequest, *, now: datetime) -> StoryPublishingState:
        detail_id = request.detail_id
        state = await self._ready_state(detail_id, now)
        if state.status is not StoryGenerationStatus.READY:
            raise StoryNotPublishable(detail_id)
        result = await self._gateway.story_result(detail_id)
        if result.status != "ready" or result.package is None or result.content_hash is None:
            raise StoryDetailMalformed(detail_id)
        if request.content_hash != result.content_hash:
            raise StoryPublishStale(detail_id)
        package = result.package
        key = publication_key(package.story_id, request.destination, result.content_hash)
        existing = await self._gateway.publication(key)
        if existing is not None and existing.status in _ACTIVE:
            return existing
        await self._gateway.start(
            StoryPublishingInput(
                destination=request.destination,
                story_workflow_id=detail_id,
                story_id=package.story_id,
                candidate_id=result.candidate_id,
                story_created_at=result.story_created_at,
                package=package,
                content_hash=result.content_hash,
                requested_by=request.requested_by,
            )
        )
        started = await self._gateway.publication(key)
        if started is not None:
            return started
        return StoryPublishingState(
            publication_id=key,
            destination=request.destination,
            story_workflow_id=detail_id,
            story_id=package.story_id,
            candidate_id=result.candidate_id,
            story_created_at=result.story_created_at,
            content_hash=result.content_hash,
            status=PublicationStatus.PUBLISHING,
            status_detail=PUBLISHING_CHECKING_DETAIL,
            requested_by=request.requested_by,
            updated_at=_aware_moment(now),
        )

    async def publication_for(
        self,
        detail_id: str,
        *,
        now: datetime,
        destination: DestinationName = DestinationName.BLUESKY,
    ) -> StoryPublishingState | None:
        state = await self._ready_state(detail_id, now)
        if state.status is not StoryGenerationStatus.READY:
            return None
        result = await self._gateway.story_result(detail_id)
        if result.package is None or result.content_hash is None:
            raise StoryDetailMalformed(detail_id)
        key = publication_key(result.package.story_id, destination, result.content_hash)
        return await self._gateway.publication(key)
