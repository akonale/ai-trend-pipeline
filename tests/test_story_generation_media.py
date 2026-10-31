"""StoryGenerationWorkflow media step: child workflow, review package and frozen content hash."""

import asyncio
import base64
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import cast

import pytest
from pydantic import JsonValue
from temporalio import workflow
from temporalio.client import WorkflowExecutionStatus
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from ai_trend_pipeline.media.briefs import (
    StoryMediaInput,
    StoryMediaResult,
    media_workflow_id_for,
    package_matches_hash,
    story_content_hash,
    story_package_hash,
)
from ai_trend_pipeline.media.fake import FakeMediaRenderer
from ai_trend_pipeline.media.models import MediaOutcome
from ai_trend_pipeline.story_generation import (
    MEDIA_CHILD_TIMEOUT,
    MEDIA_DETAIL,
    MEDIA_REVIEW_DETAILS,
    FinalStoryDecision,
    StoryGenerationState,
    StoryGenerationStatus,
    StoryGenerationWorkflow,
)
from ai_trend_pipeline.story_media import STORY_MEDIA_TASK_QUEUE
from ai_trend_pipeline.story_status import PrimaryStoryStatus, primary_story_status
from tests.media_fixtures import make_manifest
from tests.story_generation_fixtures import (
    make_draft_output,
    make_facts,
    make_story_input,
    with_media_worker,
)
from tests.test_story_generation import (
    FakeStoryActivities,
    StoryHandle,
    make_story_worker,
    start_env,
    start_story,
    wait_for_status,
)


@workflow.defn(name="StoryMediaWorkflow")
class StaticMediaDouble:
    """Stands in for the real child and always reports a static outcome."""

    @workflow.run
    async def run(self, data: StoryMediaInput) -> StoryMediaResult:
        manifest = make_manifest(
            MediaOutcome.STATIC,
            story_id=data.story_id,
            story_content_hash=story_content_hash(data.package),
        )
        return StoryMediaResult(manifest=manifest)


@workflow.defn(name="StoryMediaWorkflow")
class HangingMediaDouble:
    """Never returns, so the parent's execution timeout is the only way out."""

    @workflow.run
    async def run(self, data: StoryMediaInput) -> StoryMediaResult:
        await workflow.wait_condition(lambda: False)
        raise AssertionError("unreachable")


@workflow.defn(name="StoryMediaWorkflow")
class GatedMediaDouble:
    """Waits for a `release` signal, then returns a static outcome."""

    def __init__(self) -> None:
        self._released = False

    @workflow.signal
    def release(self) -> None:
        self._released = True

    @workflow.run
    async def run(self, data: StoryMediaInput) -> StoryMediaResult:
        await workflow.wait_condition(lambda: self._released)
        manifest = make_manifest(
            MediaOutcome.STATIC,
            story_id=data.story_id,
            story_content_hash=story_content_hash(data.package),
        )
        return StoryMediaResult(manifest=manifest)


def drafted_media_id() -> str:
    story = make_story_input()
    return media_workflow_id_for(make_draft_output(story.candidate_id).draft)


@asynccontextmanager
async def with_double(
    env: WorkflowEnvironment, fakes: FakeStoryActivities, double: type[object]
) -> AsyncGenerator[None]:
    media = Worker(
        env.client,
        task_queue=STORY_MEDIA_TASK_QUEUE,
        workflows=[double],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    async with make_story_worker(env, fakes), media:
        yield


async def review_state(handle: StoryHandle) -> StoryGenerationState:
    return await wait_for_status(handle, StoryGenerationStatus.REVIEW)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("renderer", "outcome"),
    [
        (FakeMediaRenderer(), MediaOutcome.GIF),
        (FakeMediaRenderer(fail_animated_times=10), MediaOutcome.STATIC_FALLBACK),
        (FakeMediaRenderer(fail_static_times=10), MediaOutcome.TEXT_ONLY),
    ],
    ids=["gif", "static_fallback", "text_only"],
)
async def test_gif_static_fallback_and_text_only_manifests_reach_review(
    renderer: FakeMediaRenderer, outcome: MediaOutcome
) -> None:
    fakes = FakeStoryActivities()
    async with await start_env() as env:
        worker = make_story_worker(env, fakes)
        async with with_media_worker(worker, env, renderer=renderer):
            handle = await start_story(env)
            state = await review_state(handle)
    assert state.package is not None and state.package.media is not None
    assert state.package.media.outcome is outcome
    assert state.status_detail == MEDIA_REVIEW_DETAILS[outcome]


@pytest.mark.asyncio
async def test_static_outcome_when_child_returns_static() -> None:
    fakes = FakeStoryActivities()
    async with await start_env() as env, with_double(env, fakes, StaticMediaDouble):
        handle = await start_story(env)
        state = await review_state(handle)
    assert state.package is not None and state.package.media is not None
    assert state.package.media.outcome is MediaOutcome.STATIC
    assert state.status_detail == MEDIA_REVIEW_DETAILS[MediaOutcome.STATIC]


@pytest.mark.asyncio
async def test_media_child_timeout_gives_text_only() -> None:
    fakes = FakeStoryActivities()
    async with await start_env() as env, with_double(env, fakes, HangingMediaDouble):
        handle = await start_story(env)
        await wait_for_status(handle, StoryGenerationStatus.MEDIA)
        await env.sleep(MEDIA_CHILD_TIMEOUT + timedelta(minutes=1))
        state = await review_state(handle)
    assert state.package is not None and state.package.media is not None
    assert state.package.media.outcome is MediaOutcome.TEXT_ONLY
    assert state.status_detail == MEDIA_REVIEW_DETAILS[MediaOutcome.TEXT_ONLY]


@pytest.mark.asyncio
async def test_running_child_with_same_id_gives_text_only_without_second_start() -> None:
    fakes = FakeStoryActivities()
    async with await start_env() as env, with_double(env, fakes, HangingMediaDouble):
        story = make_story_input()
        package = make_draft_output(story.candidate_id).draft
        existing = await env.client.start_workflow(
            "StoryMediaWorkflow",
            StoryMediaInput(
                story_id=package.story_id, package=package, facts=make_facts(package.story_id)
            ),
            id=drafted_media_id(),
            task_queue=STORY_MEDIA_TASK_QUEUE,
        )
        handle = await start_story(env)
        state = await review_state(handle)
        description = await existing.describe()
    assert state.package is not None and state.package.media is not None
    assert state.package.media.outcome is MediaOutcome.TEXT_ONLY
    assert description.status is WorkflowExecutionStatus.RUNNING


@pytest.mark.asyncio
async def test_status_is_media_and_signals_are_ignored_until_media_resolves() -> None:
    fakes = FakeStoryActivities()
    async with await start_env() as env, with_double(env, fakes, GatedMediaDouble):
        handle = await start_story(env)
        state = await wait_for_status(handle, StoryGenerationStatus.MEDIA)
        assert state.status_detail == MEDIA_DETAIL
        assert primary_story_status(state.status.value) is PrimaryStoryStatus.WRITING
        await handle.signal(StoryGenerationWorkflow.approve, FinalStoryDecision(note="early"))
        await handle.signal(StoryGenerationWorkflow.reject, FinalStoryDecision(note="early"))
        child = env.client.get_workflow_handle(drafted_media_id())
        await child.signal("release")
        reviewed = await review_state(handle)
        description = await handle.describe()
    assert description.status is WorkflowExecutionStatus.RUNNING
    assert reviewed.package is not None and reviewed.package.media is not None


@pytest.mark.asyncio
async def test_review_query_returns_package_with_media_and_content_hash() -> None:
    fakes = FakeStoryActivities()
    async with await start_env() as env:
        async with with_media_worker(make_story_worker(env, fakes), env):
            handle = await start_story(env)
            state = await review_state(handle)
    assert state.package is not None and state.package.media is not None
    assert state.content_hash == story_package_hash(state.package)


@pytest.mark.asyncio
async def test_ready_result_freezes_content_hash() -> None:
    fakes = FakeStoryActivities()
    async with await start_env() as env:
        async with with_media_worker(make_story_worker(env, fakes), env):
            handle = await start_story(env)
            state = await review_state(handle)
            await handle.signal(
                StoryGenerationWorkflow.approve,
                FinalStoryDecision(content_hash=state.content_hash),
            )
            result = await handle.result()
    assert result.status == "ready"
    assert result.package is not None and result.package.media is not None
    frozen = result.content_hash
    assert frozen is not None
    assert frozen == story_package_hash(result.package)
    assert frozen == state.content_hash
    assert package_matches_hash(result.package, frozen)
    changed = result.package.model_copy(update={"headline": "A different headline"})
    assert not package_matches_hash(changed, frozen)


@pytest.mark.asyncio
async def test_approve_with_mismatched_hash_is_ignored_then_matching_hash_approves() -> None:
    fakes = FakeStoryActivities()
    async with await start_env() as env:
        async with with_media_worker(make_story_worker(env, fakes), env):
            handle = await start_story(env)
            state = await review_state(handle)
            await handle.signal(
                StoryGenerationWorkflow.approve, FinalStoryDecision(content_hash="e" * 64)
            )
            await asyncio.sleep(0.2)
            still = await handle.query(StoryGenerationWorkflow.story_state)
            assert still.status is StoryGenerationStatus.REVIEW
            await handle.signal(
                StoryGenerationWorkflow.approve,
                FinalStoryDecision(content_hash=state.content_hash),
            )
            result = await handle.result()
    assert result.status == "ready"


@pytest.mark.asyncio
async def test_review_deadline_expires_story_stuck_in_media() -> None:
    fakes = FakeStoryActivities()
    async with await start_env() as env, with_double(env, fakes, HangingMediaDouble):
        now = await env.get_current_time()
        handle = await start_story(env, observed_at=now - timedelta(hours=47, minutes=55))
        await wait_for_status(handle, StoryGenerationStatus.MEDIA)
        await env.sleep(timedelta(minutes=30))
        result = await handle.result()
    assert result.status == "expired"
    assert result.package is None and result.content_hash is None


def _payload_bytes(node: JsonValue) -> list[bytes]:
    """Every decoded payload `data` value in a history JSON tree."""
    found: list[bytes] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "data" and isinstance(value, str):
                found.append(base64.b64decode(value))
            else:
                found.extend(_payload_bytes(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_payload_bytes(item))
    return found


@pytest.mark.asyncio
async def test_parent_history_contains_no_media_bytes() -> None:
    fakes = FakeStoryActivities()
    async with await start_env() as env:
        async with with_media_worker(make_story_worker(env, fakes), env):
            handle = await start_story(env)
            state = await review_state(handle)
            history = (await handle.fetch_history()).to_json()
    assert state.package is not None and state.package.media is not None
    payloads = b"\n".join(_payload_bytes(cast(JsonValue, json.loads(history))))
    assert b"\x89PNG" not in payloads
    assert b"GIF89a" not in payloads
    assert state.package.media.static_artifact is not None
    assert state.package.media.static_artifact.relative_path.encode() in payloads
    assert len(payloads) < 100_000
