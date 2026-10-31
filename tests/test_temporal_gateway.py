"""Pure helper tests for the Temporal gateway."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.common import (
    SearchAttributePair,
    TypedSearchAttributes,
    WorkflowIDConflictPolicy,
    WorkflowIDReusePolicy,
)
from temporalio.service import RPCError, RPCStatusCode

from ai_trend_pipeline.media.models import MediaOutcome
from ai_trend_pipeline.publishing.models import (
    DestinationName,
    PublicationErrorCode,
    publication_key,
)
from ai_trend_pipeline.services.temporal_gateway import (
    TemporalStoryPublishingGateway,
    started_since_query,
)
from ai_trend_pipeline.story_publishing import (
    PUBLISH_WORKFLOW_TIMEOUT,
    STORY_PUBLISHING_TASK_QUEUE,
    PublicationStatus,
    StoryPublishingInput,
    StoryPublishingResult,
    StoryPublishingState,
    StoryPublishingWorkflow,
)
from ai_trend_pipeline.story_visibility import (
    STORY_CANDIDATE_ID_ATTRIBUTE,
    STORY_CREATED_AT_ATTRIBUTE,
    STORY_SOURCE_ATTRIBUTE,
    STORY_STATUS_ATTRIBUTE,
    STORY_TITLE_ATTRIBUTE,
)
from tests.publishing_fixtures import make_approved_package


def test_started_since_query() -> None:
    assert started_since_query(datetime(2026, 5, 1, tzinfo=UTC)) == (
        "WorkflowType = 'DailyChartWorkflow' AND StartTime >= '2026-05-01T00:00:00Z'"
    )


class FakePublishingHandle:
    def __init__(self, status: WorkflowExecutionStatus | None, result: object = None) -> None:
        self.status = status
        self._result = result
        self.queried = False

    async def describe(self) -> object:
        if self.status is None:
            raise RPCError("gone", RPCStatusCode.NOT_FOUND, b"")
        return SimpleNamespace(
            status=self.status,
            close_time=None,
            typed_search_attributes=TypedSearchAttributes(
                [
                    SearchAttributePair(STORY_TITLE_ATTRIBUTE, "Rates hold"),
                    SearchAttributePair(STORY_SOURCE_ATTRIBUTE, "hn"),
                ]
            ),
        )

    async def query(self, query: object) -> StoryPublishingState:
        _ = query
        self.queried = True
        return StoryPublishingState(publication_id=PUBLICATION_ID)

    async def result(self) -> object:
        return self._result


class FakePublishingClient:
    def __init__(self, handle: FakePublishingHandle) -> None:
        self.handle = handle
        self.started: list[dict[str, Any]] = []

    def get_workflow_handle_for(self, run: object, workflow_id: str) -> FakePublishingHandle:
        _ = (run, workflow_id)
        return self.handle

    async def start_workflow(self, run: object, data: object, **kwargs: Any) -> None:  # noqa: ANN401  # Any: captures SDK keyword arguments
        self.started.append({"run": run, "data": data, **kwargs})


PUBLICATION_ID = "publish-v1-x-bluesky-" + "a" * 64


def publishing_input() -> StoryPublishingInput:
    package, digest = make_approved_package(MediaOutcome.TEXT_ONLY)
    return StoryPublishingInput(
        destination=DestinationName.BLUESKY,
        story_workflow_id="story-v1-0123456789abcdef",
        story_id=package.story_id,
        candidate_id="candidate-v1-0123456789abcdef",
        story_created_at=datetime(2026, 10, 30, 10, 0, tzinfo=UTC),
        package=package,
        content_hash=digest,
    )


@pytest.mark.asyncio
async def test_publishing_start_uses_use_existing_allow_duplicate_and_inherited_attributes() -> (
    None
):
    client = FakePublishingClient(FakePublishingHandle(WorkflowExecutionStatus.RUNNING))
    data = publishing_input()
    await TemporalStoryPublishingGateway(cast(Client, client)).start(data)
    (call,) = client.started
    assert call["run"] is StoryPublishingWorkflow.run
    assert call["id"] == publication_key(data.story_id, data.destination, data.content_hash)
    assert call["task_queue"] == STORY_PUBLISHING_TASK_QUEUE
    assert call["id_conflict_policy"] is WorkflowIDConflictPolicy.USE_EXISTING
    assert call["id_reuse_policy"] is WorkflowIDReusePolicy.ALLOW_DUPLICATE
    assert call["execution_timeout"] == PUBLISH_WORKFLOW_TIMEOUT
    attributes: TypedSearchAttributes = call["search_attributes"]
    assert attributes.get(STORY_CANDIDATE_ID_ATTRIBUTE) == data.candidate_id
    assert attributes.get(STORY_CREATED_AT_ATTRIBUTE) == data.story_created_at
    assert attributes.get(STORY_STATUS_ATTRIBUTE) == "publishing"
    assert attributes.get(STORY_TITLE_ATTRIBUTE) == "Rates hold"
    assert attributes.get(STORY_SOURCE_ATTRIBUTE) == "hn"


@pytest.mark.asyncio
async def test_publication_maps_running_completed_and_abnormal_close_and_not_found() -> None:
    def gateway(handle: FakePublishingHandle) -> TemporalStoryPublishingGateway:
        return TemporalStoryPublishingGateway(cast(Client, FakePublishingClient(handle)))

    running = FakePublishingHandle(WorkflowExecutionStatus.RUNNING)
    state = await gateway(running).publication(PUBLICATION_ID)
    assert running.queried and state == StoryPublishingState(publication_id=PUBLICATION_ID)

    now = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
    result = StoryPublishingResult(
        publication_id=PUBLICATION_ID,
        destination=DestinationName.BLUESKY,
        story_workflow_id="story-v1-0123456789abcdef",
        story_id="s",
        candidate_id="c",
        story_created_at=now,
        content_hash="a" * 64,
        status="publish_failed",
        status_detail="Bluesky rejected the post.",
        error_code=PublicationErrorCode.REJECTED,
        updated_at=now,
    )
    completed = await gateway(
        FakePublishingHandle(WorkflowExecutionStatus.COMPLETED, result)
    ).publication(PUBLICATION_ID)
    assert completed is not None
    assert completed.status is PublicationStatus.FAILED
    assert completed.error_code is PublicationErrorCode.REJECTED and not completed.retryable

    for status in (
        WorkflowExecutionStatus.FAILED,
        WorkflowExecutionStatus.TERMINATED,
        WorkflowExecutionStatus.CANCELED,
        WorkflowExecutionStatus.TIMED_OUT,
    ):
        stopped = await gateway(FakePublishingHandle(status)).publication(PUBLICATION_ID)
        assert stopped is not None
        assert stopped.status is PublicationStatus.FAILED
        assert stopped.status_detail == "Publishing stopped before it finished. Try again."
        assert stopped.error_code is None and stopped.retryable

    assert await gateway(FakePublishingHandle(None)).publication(PUBLICATION_ID) is None
