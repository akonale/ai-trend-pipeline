"""StoryPublishingService with a fake gateway, plus one real-Temporal integration class."""

import ast
import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from ai_trend_pipeline.media.models import MediaOutcome
from ai_trend_pipeline.publishing.models import (
    DestinationName,
    PublicationErrorCode,
    publication_key,
)
from ai_trend_pipeline.services.publishing import (
    PublishStoryRequest,
    StoryNotPublishable,
    StoryPublishingService,
    StoryPublishStale,
)
from ai_trend_pipeline.services.stories import (
    StoryDetailExpired,
    StoryDetailMalformed,
    StoryDetailNotFound,
)
from ai_trend_pipeline.services.temporal_gateway import TemporalStoryPublishingGateway
from ai_trend_pipeline.story_generation import (
    StoryGenerationResult,
    StoryGenerationState,
    StoryGenerationStatus,
)
from ai_trend_pipeline.story_publishing import (
    STORY_PUBLISHING_TASK_QUEUE,
    PublicationStatus,
    StoryPublishingInput,
    StoryPublishingState,
    StoryPublishingWorkflow,
)
from ai_trend_pipeline.temporal_client import ensure_story_search_attributes
from tests.publishing_fixtures import FakePds, make_activities, make_approved_package

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
CREATED = NOW - timedelta(hours=2)
DETAIL_ID = "story-v1-0123456789abcdef"
CANDIDATE_ID = "candidate-v1-0123456789abcdef"
SRC = Path(__file__).resolve().parent.parent / "src" / "ai_trend_pipeline"


class FakeGateway:
    def __init__(self, tmp_path: Path, status: StoryGenerationStatus = StoryGenerationStatus.READY):
        package, digest = make_approved_package(MediaOutcome.STATIC, media_root=tmp_path)
        self.package = package
        self.hash = digest
        self.status = status
        self.created = CREATED
        self.starts: list[StoryPublishingInput] = []
        self.states: dict[str, StoryPublishingState] = {}
        self.expose_after_start = True
        self.result_calls = 0

    @property
    def key(self) -> str:
        return publication_key(self.package.story_id, DestinationName.BLUESKY, self.hash)

    async def story_state(self, workflow_id: str) -> StoryGenerationState:
        if workflow_id != DETAIL_ID:
            raise StoryDetailNotFound(workflow_id)
        return StoryGenerationState(
            story_id=self.package.story_id,
            candidate_id=CANDIDATE_ID,
            story_created_at=self.created,
            status=self.status,
        )

    async def story_result(self, workflow_id: str) -> StoryGenerationResult:
        self.result_calls += 1
        return StoryGenerationResult(
            status="ready",
            story_id=self.package.story_id,
            candidate_id=CANDIDATE_ID,
            story_created_at=self.created,
            updated_at=self.created,
            package=self.package,
            content_hash=self.hash,
        )

    async def publication(self, publication_id: str) -> StoryPublishingState | None:
        return self.states.get(publication_id)

    async def start(self, data: StoryPublishingInput) -> None:
        self.starts.append(data)
        if self.expose_after_start:
            self.states[publication_key(data.story_id, data.destination, data.content_hash)] = (
                StoryPublishingState(
                    publication_id=publication_key(
                        data.story_id, data.destination, data.content_hash
                    ),
                    destination=data.destination,
                    status=PublicationStatus.PUBLISHING,
                    status_detail="Checking the approved story.",
                )
            )


def request(gateway: FakeGateway, **overrides: object) -> PublishStoryRequest:
    data: dict[str, object] = {"detail_id": DETAIL_ID, "content_hash": gateway.hash}
    data.update(overrides)
    return PublishStoryRequest.model_validate(data)


@pytest.mark.asyncio
async def test_publish_ready_story_starts_one_workflow_with_deterministic_id(
    tmp_path: Path,
) -> None:
    gateway = FakeGateway(tmp_path)
    state = await StoryPublishingService(gateway).publish(
        request(gateway, requested_by=" ada@example.com "), now=NOW
    )
    (data,) = gateway.starts
    assert data.story_workflow_id == DETAIL_ID
    assert data.story_id == gateway.package.story_id and data.candidate_id == CANDIDATE_ID
    assert data.story_created_at == CREATED and data.content_hash == gateway.hash
    assert data.package == gateway.package and data.requested_by == "ada@example.com"
    assert state.publication_id == gateway.key
    assert state.status is PublicationStatus.PUBLISHING


@pytest.mark.asyncio
async def test_publish_returns_publishing_state_when_not_visible_yet(tmp_path: Path) -> None:
    gateway = FakeGateway(tmp_path)
    gateway.expose_after_start = False
    state = await StoryPublishingService(gateway).publish(request(gateway), now=NOW)
    assert state.status is PublicationStatus.PUBLISHING
    assert state.publication_id == gateway.key and len(gateway.starts) == 1


@pytest.mark.asyncio
async def test_double_click_returns_existing_publishing_state_without_second_start(
    tmp_path: Path,
) -> None:
    gateway = FakeGateway(tmp_path)
    service = StoryPublishingService(gateway)
    first = await service.publish(request(gateway), now=NOW)
    second = await service.publish(request(gateway), now=NOW)
    assert len(gateway.starts) == 1
    assert first.publication_id == second.publication_id
    assert second.status is PublicationStatus.PUBLISHING


@pytest.mark.asyncio
async def test_already_published_returns_result_without_start(tmp_path: Path) -> None:
    gateway = FakeGateway(tmp_path)
    gateway.states[gateway.key] = StoryPublishingState(
        publication_id=gateway.key, status=PublicationStatus.PUBLISHED
    )
    state = await StoryPublishingService(gateway).publish(request(gateway), now=NOW)
    assert state.status is PublicationStatus.PUBLISHED
    assert gateway.starts == []


@pytest.mark.asyncio
async def test_failed_publication_can_be_retried(tmp_path: Path) -> None:
    gateway = FakeGateway(tmp_path)
    gateway.states[gateway.key] = StoryPublishingState(
        publication_id=gateway.key,
        status=PublicationStatus.FAILED,
        error_code=PublicationErrorCode.UNAVAILABLE,
        retryable=True,
    )
    state = await StoryPublishingService(gateway).publish(request(gateway), now=NOW)
    assert len(gateway.starts) == 1
    assert state.status is PublicationStatus.PUBLISHING


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        StoryGenerationStatus.RESEARCHING,
        StoryGenerationStatus.REVIEW,
        StoryGenerationStatus.REJECTED,
        StoryGenerationStatus.UNSUPPORTED,
        StoryGenerationStatus.FAILED,
        StoryGenerationStatus.EXPIRED,
    ],
)
async def test_refuses_stories_that_are_not_ready_without_reading_the_result(
    tmp_path: Path, status: StoryGenerationStatus
) -> None:
    gateway = FakeGateway(tmp_path, status)
    with pytest.raises(StoryNotPublishable):
        await StoryPublishingService(gateway).publish(request(gateway), now=NOW)
    assert gateway.starts == [] and gateway.result_calls == 0


@pytest.mark.asyncio
async def test_refuses_non_ready_candidate_id_stale_hash_and_expired_story(
    tmp_path: Path,
) -> None:
    gateway = FakeGateway(tmp_path)
    service = StoryPublishingService(gateway)
    with pytest.raises(StoryNotPublishable):
        await service.publish(request(gateway, detail_id=CANDIDATE_ID), now=NOW)
    with pytest.raises(StoryDetailNotFound):
        await service.publish(request(gateway, detail_id="nonsense"), now=NOW)
    with pytest.raises(StoryPublishStale):
        await service.publish(request(gateway, content_hash="d" * 64), now=NOW)
    gateway.created = NOW - timedelta(hours=48)
    with pytest.raises(StoryDetailExpired):
        await service.publish(request(gateway), now=NOW)
    gateway.created = NOW - timedelta(hours=47, minutes=59)
    await service.publish(request(gateway), now=NOW)
    assert len(gateway.starts) == 1
    with pytest.raises(ValueError):
        await service.publish(request(gateway), now=datetime(2026, 10, 30, 12, 0))


class NoPackageGateway(FakeGateway):
    async def story_result(self, workflow_id: str) -> StoryGenerationResult:
        return StoryGenerationResult(
            status="ready",
            story_id="s",
            candidate_id=CANDIDATE_ID,
            story_created_at=CREATED,
            updated_at=CREATED,
        )


@pytest.mark.asyncio
async def test_malformed_ready_result_is_refused(tmp_path: Path) -> None:
    gateway = NoPackageGateway(tmp_path)
    with pytest.raises(StoryDetailMalformed):
        await StoryPublishingService(gateway).publish(request(gateway), now=NOW)
    assert gateway.starts == []


def test_request_validates_hash_and_destination() -> None:
    with pytest.raises(ValueError):
        PublishStoryRequest(detail_id=DETAIL_ID, content_hash="nope")
    assert (
        PublishStoryRequest(detail_id=DETAIL_ID, content_hash="a" * 64).destination
        is DestinationName.BLUESKY
    )


@pytest.mark.asyncio
async def test_publication_for_returns_none_before_publish_and_state_after(
    tmp_path: Path,
) -> None:
    gateway = FakeGateway(tmp_path)
    service = StoryPublishingService(gateway)
    assert await service.publication_for(DETAIL_ID, now=NOW) is None
    await service.publish(request(gateway), now=NOW)
    state = await service.publication_for(DETAIL_ID, now=NOW)
    assert state is not None and state.publication_id == gateway.key
    not_ready = FakeGateway(tmp_path, StoryGenerationStatus.REVIEW)
    assert await StoryPublishingService(not_ready).publication_for(DETAIL_ID, now=NOW) is None
    with pytest.raises(StoryNotPublishable):
        await service.publication_for(CANDIDATE_ID, now=NOW)
    gateway.created = NOW - timedelta(hours=49)
    with pytest.raises(StoryDetailExpired):
        await service.publication_for(DETAIL_ID, now=NOW)


def test_no_approval_path_references_publishing() -> None:
    allowed = {
        Path("services/publishing.py"),
        Path("services/temporal_gateway.py"),
        Path("worker.py"),
        Path("story_publishing.py"),
    }
    offenders: list[Path] = []
    for path in SRC.rglob("*.py"):
        relative = path.relative_to(SRC)
        if relative in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                names = [node.module, *(f"{node.module}.{alias.name}" for alias in node.names)]
            if any(name.endswith(("story_publishing", "services.publishing")) for name in names):
                offenders.append(relative)
    assert offenders == []


class RecordingClient:
    """Delegates to the real client and records the run id of every start."""

    def __init__(self, client: Client) -> None:
        self._client = client
        self.run_ids: list[str | None] = []

    async def start_workflow(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401  # Any: forwards the SDK's generic overloads untouched
        handle = await self._client.start_workflow(*args, **kwargs)
        self.run_ids.append(handle.result_run_id)
        return handle

    def __getattr__(self, name: str) -> object:
        return getattr(self._client, name)


class PublishingOnlyGateway(TemporalStoryPublishingGateway):
    """Real publishing start and read; the Ready story is served from a fake."""

    def __init__(self, client: Client, fake: FakeGateway) -> None:
        super().__init__(client)
        self._fake = fake

    async def story_state(self, workflow_id: str) -> StoryGenerationState:
        return await self._fake.story_state(workflow_id)

    async def story_result(self, workflow_id: str) -> StoryGenerationResult:
        return await self._fake.story_result(workflow_id)


@pytest.mark.asyncio
class TestPublishingIntegration:
    async def test_concurrent_publish_creates_one_run_and_one_record(self, tmp_path: Path) -> None:
        fake = FakeGateway(tmp_path)
        pds = FakePds()
        activities = make_activities(pds, tmp_path)
        async with await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter
        ) as env:
            await ensure_story_search_attributes(env.client)
            async with Worker(
                env.client,
                task_queue=STORY_PUBLISHING_TASK_QUEUE,
                workflows=[StoryPublishingWorkflow],
                activities=[activities.publish_story_to_bluesky],
            ):
                recorder = RecordingClient(env.client)
                service = StoryPublishingService(
                    PublishingOnlyGateway(cast(Client, recorder), fake)
                )
                first, second = await asyncio.gather(
                    service.publish(request(fake), now=NOW),
                    service.publish(request(fake), now=NOW),
                )
                assert first.publication_id == second.publication_id == fake.key
                assert len(set(recorder.run_ids)) == 1
                await env.client.get_workflow_handle(fake.key).result()
                final = await service.publish(request(fake), now=NOW)
                assert final.status is PublicationStatus.PUBLISHED
                publication = final.publication
                assert publication is not None
                assert publication.uri.endswith(publication.rkey)
                assert pds.create_ok == 1 and len(pds.records) == 1
                assert pds.names().count("createRecord") == 1
                assert len(recorder.run_ids) <= 2
