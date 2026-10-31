"""StoryPublishingWorkflow with the real Bluesky activity against the fake PDS."""

import ast
import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from temporalio.client import WorkflowFailureError, WorkflowHandle, WorkflowHistory
from temporalio.common import SearchAttributeKey
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from ai_trend_pipeline.bluesky import make_tid
from ai_trend_pipeline.media.models import MediaOutcome
from ai_trend_pipeline.publishing.models import (
    RETRYABLE_CODES,
    DestinationName,
    PublicationErrorCode,
    PublishedMediaKind,
    publication_key,
)
from ai_trend_pipeline.story_publishing import (
    FAILURE_DETAILS,
    PUBLISH_MAX_ATTEMPTS,
    PUBLISH_WORKFLOW_TIMEOUT,
    PUBLISHED_DETAIL,
    PUBLISHING_POSTING_DETAIL,
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
    STORY_STATUS_ATTRIBUTE,
    STORY_STATUS_DETAIL_ATTRIBUTE,
    STORY_UPDATED_AT_ATTRIBUTE,
    story_start_attributes,
)
from ai_trend_pipeline.temporal_client import ensure_story_search_attributes
from tests.publishing_fixtures import (
    DID,
    PASSWORD,
    FakePds,
    make_activities,
    make_approved_package,
    respond,
    transport_error,
)

CREATED = datetime(2026, 10, 30, 12, 0, 0, 123000, tzinfo=UTC)
STORY_WORKFLOW_ID = "story-v1-0123456789abcdef"
CANDIDATE = "candidate-v1-0123456789abcdef"
HISTORY = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "histories"
    / "story_publishing_v1_published.json"
)
SRC = Path(__file__).resolve().parent.parent / "src" / "ai_trend_pipeline"

Handle = WorkflowHandle[StoryPublishingWorkflow, StoryPublishingResult]


def make_data(
    media_root: Path, outcome: MediaOutcome | None = MediaOutcome.STATIC, **overrides: object
) -> StoryPublishingInput:
    package, digest = make_approved_package(outcome, media_root=media_root, **overrides)
    return StoryPublishingInput(
        destination=DestinationName.BLUESKY,
        story_workflow_id=STORY_WORKFLOW_ID,
        story_id=package.story_id,
        candidate_id=CANDIDATE,
        story_created_at=CREATED,
        package=package,
        content_hash=digest,
        requested_by="ada@example.com",
    )


def key_of(data: StoryPublishingInput) -> str:
    return publication_key(data.story_id, data.destination, data.content_hash)


@asynccontextmanager
async def publishing_env(pds: FakePds, media_root: Path) -> AsyncGenerator[WorkflowEnvironment]:
    activities = make_activities(pds, media_root)
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
            yield env


async def start(
    env: WorkflowEnvironment, data: StoryPublishingInput, workflow_id: str | None = None
) -> Handle:
    return await env.client.start_workflow(
        StoryPublishingWorkflow.run,
        data,
        id=workflow_id or key_of(data),
        task_queue=STORY_PUBLISHING_TASK_QUEUE,
        execution_timeout=PUBLISH_WORKFLOW_TIMEOUT,
        search_attributes=story_start_attributes(
            "publishing", "Title", "hn", data.candidate_id, data.story_created_at, CREATED
        ),
    )


async def publish(
    tmp_path: Path, pds: FakePds, outcome: MediaOutcome | None = MediaOutcome.STATIC, **kw: object
) -> StoryPublishingResult:
    data = make_data(tmp_path, outcome, **kw)
    async with publishing_env(pds, tmp_path) as env:
        return await (await start(env, data)).result()


def rkey_for(data: StoryPublishingInput) -> str:
    return make_tid(CREATED, key_of(data))


@pytest.mark.asyncio
async def test_text_only_story_publishes_and_returns_uri_and_public_url(tmp_path: Path) -> None:
    pds = FakePds()
    data = make_data(tmp_path, None)
    result = await publish(tmp_path, pds, None)
    rkey = rkey_for(data)
    assert result.status == "published" and result.error_code is None
    assert result.status_detail == PUBLISHED_DETAIL
    assert result.publication_id == key_of(data)
    assert result.requested_by == "ada@example.com"
    publication = result.publication
    assert publication is not None
    assert publication.media_kind is PublishedMediaKind.NONE
    assert publication.rkey == rkey
    assert publication.uri.endswith(f"/{rkey}")
    assert publication.public_url == f"https://bsky.app/profile/{DID}/post/{rkey}"
    assert set(pds.records) == {rkey}
    assert pds.records[rkey]["record"]["createdAt"].startswith("2026-")
    assert publication.published_at.year == 2026
    assert pds.names() == ["createSession", "getRecord", "createRecord"]


@pytest.mark.asyncio
async def test_static_story_uploads_png_and_maps_result(tmp_path: Path) -> None:
    pds = FakePds()
    result = await publish(tmp_path, pds)
    assert pds.names() == ["createSession", "getRecord", "uploadBlob", "createRecord"]
    assert result.publication is not None
    assert result.publication.media_kind is PublishedMediaKind.STATIC
    assert result.publication.artifact_id is not None
    assert not result.publication.already_published


@pytest.mark.asyncio
async def test_gif_story_publishes_static_fallback(tmp_path: Path) -> None:
    pds = FakePds()
    result = await publish(tmp_path, pds, MediaOutcome.GIF)
    upload = next(r for r in pds.requests if r.url.path.endswith("uploadBlob"))
    assert upload.headers["content-type"] == "image/png"
    assert result.publication is not None
    assert result.publication.media_kind is PublishedMediaKind.STATIC


@pytest.mark.asyncio
async def test_network_failure_is_retried_and_posts_once(tmp_path: Path) -> None:
    pds = FakePds()
    pds.on("getRecord", transport_error)
    result = await publish(tmp_path, pds)
    assert result.status == "published"
    assert pds.names().count("createSession") == 2
    assert pds.create_ok == 1 and len(pds.records) == 1


@pytest.mark.asyncio
async def test_existing_record_is_recovered_as_already_published(tmp_path: Path) -> None:
    pds = FakePds()
    data = make_data(tmp_path)
    pds.records[rkey_for(data)] = {}
    async with publishing_env(pds, tmp_path) as env:
        result = await (await start(env, data)).result()
    assert result.status == "published"
    assert result.publication is not None and result.publication.already_published
    assert "uploadBlob" not in pds.names() and "createRecord" not in pds.names()
    assert pds.create_ok == 0


@pytest.mark.asyncio
async def test_auth_failure_finishes_publish_failed_and_runs_activity_once(
    tmp_path: Path,
) -> None:
    pds = FakePds()
    pds.on("createSession", respond(401, "auth_failed.json"))
    data = make_data(tmp_path)
    async with publishing_env(pds, tmp_path) as env:
        first = await (await start(env, data)).result()
        assert first.status == "publish_failed"
        assert first.error_code is PublicationErrorCode.AUTH_FAILED
        assert not first.retryable and first.publication is None
        assert first.status_detail == FAILURE_DETAILS[PublicationErrorCode.AUTH_FAILED]
        assert pds.names().count("createSession") == 1
        # A corrected PDS: the same publication id may run again and succeeds.
        second = await (await start(env, data)).result()
    assert second.status == "published"
    assert pds.create_ok == 1


@pytest.mark.asyncio
async def test_unexpected_4xx_is_bounded_to_max_attempts(tmp_path: Path) -> None:
    pds = FakePds()
    pds.on("getRecord", *[respond(403)] * (PUBLISH_MAX_ATTEMPTS + 3))
    result = await publish(tmp_path, pds)
    assert result.status == "publish_failed"
    assert result.error_code is PublicationErrorCode.UNEXPECTED_RESPONSE
    assert result.retryable
    assert pds.names().count("getRecord") == PUBLISH_MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_missing_media_fails_without_http_requests(tmp_path: Path) -> None:
    pds = FakePds()
    package, digest = make_approved_package(MediaOutcome.STATIC, media_root=None)
    data = StoryPublishingInput(
        destination=DestinationName.BLUESKY,
        story_workflow_id=STORY_WORKFLOW_ID,
        story_id=package.story_id,
        candidate_id=CANDIDATE,
        story_created_at=CREATED,
        package=package,
        content_hash=digest,
    )
    async with publishing_env(pds, tmp_path) as env:
        result = await (await start(env, data)).result()
    assert result.status == "publish_failed"
    assert result.error_code is PublicationErrorCode.MEDIA_MISSING
    assert not result.retryable
    assert pds.requests == []


@pytest.mark.asyncio
async def test_post_text_too_long_fails_in_workflow_without_activity(tmp_path: Path) -> None:
    pds = FakePds()
    result = await publish(tmp_path, pds, None, post_text="x" * 290)
    assert result.status == "publish_failed"
    assert result.error_code is PublicationErrorCode.POST_TEXT_TOO_LONG
    assert result.status_detail == "The post text is longer than Bluesky allows."
    assert pds.requests == []


@pytest.mark.asyncio
async def test_query_reports_publishing_then_terminal_state_with_sanitized_detail(
    tmp_path: Path,
) -> None:
    pds = FakePds()
    pds.on("getRecord", respond(503))
    data = make_data(tmp_path)
    async with publishing_env(pds, tmp_path) as env:
        handle = await start(env, data)
        state = StoryPublishingState()
        for _ in range(200):
            state = await handle.query(StoryPublishingWorkflow.publication_state)
            if state.status_detail == PUBLISHING_POSTING_DETAIL and pds.requests:
                break
            await asyncio.sleep(0.05)
        assert state.status is PublicationStatus.PUBLISHING
        assert state.status_detail == PUBLISHING_POSTING_DETAIL
        assert state.publication is None and state.publication_id == key_of(data)
        result = await handle.result()
        final = await handle.query(StoryPublishingWorkflow.publication_state)
    assert result.status == "published"
    assert final.status is PublicationStatus.PUBLISHED
    assert final.status_detail == PUBLISHED_DETAIL
    assert final.publication == result.publication
    assert final.updated_at == result.updated_at


@pytest.mark.asyncio
async def test_query_before_run_has_query_safe_defaults() -> None:
    state = StoryPublishingState()
    assert state.status is PublicationStatus.PUBLISHING and state.publication_id == ""


@pytest.mark.asyncio
async def test_search_attributes_inherit_candidate_id_and_story_created_at(
    tmp_path: Path,
) -> None:
    pds = FakePds()
    data = make_data(tmp_path)
    async with publishing_env(pds, tmp_path) as env:
        handle = await start(env, data)
        await handle.result()
        description = await handle.describe()
    attributes = description.typed_search_attributes
    assert attributes.get(STORY_CANDIDATE_ID_ATTRIBUTE) == CANDIDATE
    created = attributes.get(STORY_CREATED_AT_ATTRIBUTE)
    assert isinstance(created, datetime) and created == CREATED
    assert attributes.get(STORY_STATUS_ATTRIBUTE) == "published"
    assert attributes.get(STORY_STATUS_DETAIL_ATTRIBUTE) == PUBLISHED_DETAIL
    updated = attributes.get(STORY_UPDATED_AT_ATTRIBUTE)
    assert isinstance(updated, datetime) and updated > CREATED


@pytest.mark.asyncio
async def test_failed_status_is_upserted(tmp_path: Path) -> None:
    pds = FakePds()
    data = make_data(tmp_path, None, post_text="x" * 290)
    async with publishing_env(pds, tmp_path) as env:
        handle = await start(env, data)
        await handle.result()
        attributes = (await handle.describe()).typed_search_attributes
    assert attributes.get(STORY_STATUS_ATTRIBUTE) == "publish_failed"
    assert (
        attributes.get(SearchAttributeKey.for_keyword("StoryStatusDetail"))
        == (FAILURE_DETAILS[PublicationErrorCode.POST_TEXT_TOO_LONG])
    )


@pytest.mark.asyncio
async def test_workflow_id_must_equal_publication_key(tmp_path: Path) -> None:
    pds = FakePds()
    data = make_data(tmp_path)
    async with publishing_env(pds, tmp_path) as env:
        handle = await start(env, data, workflow_id="publish-v1-wrong-id")
        with pytest.raises(WorkflowFailureError) as info:
            await handle.result()
    cause = info.value.cause
    assert isinstance(cause, ApplicationError) and cause.type == "PublicationKeyMismatch"
    assert pds.requests == []


@pytest.mark.asyncio
async def test_result_and_state_contain_no_post_text_or_secrets(tmp_path: Path) -> None:
    pds = FakePds()
    data = make_data(tmp_path)
    async with publishing_env(pds, tmp_path) as env:
        handle = await start(env, data)
        result = await handle.result()
        state = await handle.query(StoryPublishingWorkflow.publication_state)
        description = await handle.describe()
    body = data.package.post_text or ""
    assert body
    shown = " ".join(
        [
            result.model_dump_json(),
            state.model_dump_json(),
            str(description.typed_search_attributes),
        ]
    )
    for secret in (body, data.package.headline, PASSWORD, "fake.access.jwt", "fake.refresh.jwt"):
        assert secret not in shown


def test_failure_details_cover_every_code_without_story_text() -> None:
    assert set(FAILURE_DETAILS) == set(PublicationErrorCode)
    assert all(0 < len(text) <= 200 for text in FAILURE_DETAILS.values())
    assert RETRYABLE_CODES <= set(FAILURE_DETAILS)


def test_input_requires_matching_story_id_and_story_workflow_prefix(tmp_path: Path) -> None:
    data = make_data(tmp_path, None)
    with pytest.raises(ValueError):
        StoryPublishingInput.model_validate({**data.model_dump(), "story_id": "different-story"})
    with pytest.raises(ValueError):
        StoryPublishingInput.model_validate(
            {**data.model_dump(), "story_workflow_id": "candidate-v1-abc"}
        )


@pytest.mark.asyncio
async def test_published_history_replays() -> None:
    package, digest = make_approved_package(MediaOutcome.STATIC, media_root=None)
    history = WorkflowHistory.from_json(
        publication_key(package.story_id, DestinationName.BLUESKY, digest),
        HISTORY.read_text(encoding="utf-8"),
    )
    replayer = Replayer(workflows=[StoryPublishingWorkflow], data_converter=pydantic_data_converter)
    result = await replayer.replay_workflow(history)
    assert result.replay_failure is None


def test_workflow_module_imports_no_httpx_services_or_web() -> None:
    tree = ast.parse((SRC / "story_publishing.py").read_text(encoding="utf-8"))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
    for module in modules:
        assert not module.startswith(
            ("httpx", "ai_trend_pipeline.services", "ai_trend_pipeline.web")
        )
        assert module != "ai_trend_pipeline.publishing.bluesky_story"
