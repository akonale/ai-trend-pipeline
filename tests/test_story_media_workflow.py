"""StoryMediaWorkflow with the fake renderer in the time-skipping environment."""

import uuid
from pathlib import Path

import pytest
from temporalio.client import WorkflowHandle
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from ai_trend_pipeline.media.activities import StoryMediaActivities
from ai_trend_pipeline.media.briefs import (
    StoryMediaInput,
    StoryMediaResult,
    media_workflow_id_for,
)
from ai_trend_pipeline.media.fake import FakeMediaRenderer
from ai_trend_pipeline.media.models import MediaOutcome
from ai_trend_pipeline.story_media import STORY_MEDIA_TASK_QUEUE, StoryMediaWorkflow
from tests.story_generation_fixtures import make_facts, make_package

STORY = "story-1"


class CountingRenderer(FakeMediaRenderer):
    def __init__(self, **kwargs: int | bool) -> None:
        super().__init__(
            fail_static_times=int(kwargs.get("fail_static_times", 0)),
            fail_animated_times=int(kwargs.get("fail_animated_times", 0)),
            oversize=bool(kwargs.get("oversize", False)),
            wrong_signature=bool(kwargs.get("wrong_signature", False)),
        )

    @property
    def static_calls(self) -> int:
        return self._static_calls

    @property
    def animated_calls(self) -> int:
        return self._animated_calls


async def _run(
    tmp_path: Path,
    renderer: FakeMediaRenderer,
    *,
    include_animation: bool = True,
) -> tuple[StoryMediaResult, WorkflowHandle[StoryMediaWorkflow, StoryMediaResult]]:
    package = make_package(STORY)
    data = StoryMediaInput(
        story_id=STORY,
        package=package,
        facts=make_facts(STORY),
        include_animation=include_animation,
    )
    acts = StoryMediaActivities(tmp_path, renderer)
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        async with Worker(
            env.client,
            task_queue=STORY_MEDIA_TASK_QUEUE,
            workflows=[StoryMediaWorkflow],
            activities=[acts.render_static_media, acts.render_animated_media],
        ):
            handle = await env.client.start_workflow(
                StoryMediaWorkflow.run,
                data,
                id=f"{media_workflow_id_for(package)}-{uuid.uuid4()}",
                task_queue=STORY_MEDIA_TASK_QUEUE,
            )
            result = await handle.result()
            history = await handle.fetch_history()
            _HISTORY.append(history.to_json())
    return result, handle


_HISTORY: list[str] = []


@pytest.mark.asyncio
async def test_gif_outcome_has_static_and_animated_artifacts(tmp_path: Path) -> None:
    result, _ = await _run(tmp_path, CountingRenderer())
    manifest = result.manifest
    assert manifest.outcome is MediaOutcome.GIF
    assert manifest.static_artifact is not None and manifest.animated_artifact is not None
    assert manifest.story_id == STORY
    assert len(list(tmp_path.iterdir())) == 2


@pytest.mark.asyncio
async def test_static_outcome_when_animation_disabled(tmp_path: Path) -> None:
    renderer = CountingRenderer()
    result, _ = await _run(tmp_path, renderer, include_animation=False)
    assert result.manifest.outcome is MediaOutcome.STATIC
    assert result.manifest.animated_artifact is None
    assert renderer.animated_calls == 0


@pytest.mark.asyncio
async def test_static_fallback_when_animation_fails_after_retries(tmp_path: Path) -> None:
    renderer = CountingRenderer(fail_animated_times=10)
    result, _ = await _run(tmp_path, renderer)
    assert result.manifest.outcome is MediaOutcome.STATIC_FALLBACK
    assert result.manifest.static_artifact is not None
    assert result.manifest.animated_artifact is None
    assert renderer.animated_calls == 3


@pytest.mark.asyncio
async def test_text_only_when_static_fails_and_animation_not_attempted(tmp_path: Path) -> None:
    renderer = CountingRenderer(fail_static_times=10)
    result, _ = await _run(tmp_path, renderer)
    assert result.manifest.outcome is MediaOutcome.TEXT_ONLY
    assert result.manifest.static_artifact is None
    assert renderer.static_calls == 3
    assert renderer.animated_calls == 0


@pytest.mark.asyncio
async def test_transient_failures_recover_within_three_attempts(tmp_path: Path) -> None:
    renderer = CountingRenderer(fail_static_times=2, fail_animated_times=2)
    result, _ = await _run(tmp_path, renderer)
    assert result.manifest.outcome is MediaOutcome.GIF
    assert renderer.static_calls == 3 and renderer.animated_calls == 3


@pytest.mark.asyncio
async def test_non_retryable_render_errors_are_attempted_once(tmp_path: Path) -> None:
    renderer = CountingRenderer(oversize=True)
    result, _ = await _run(tmp_path, renderer)
    assert result.manifest.outcome is MediaOutcome.TEXT_ONLY
    assert renderer.static_calls == 1
    wrong = CountingRenderer(wrong_signature=True)
    result, _ = await _run(tmp_path, wrong)
    assert result.manifest.outcome is MediaOutcome.TEXT_ONLY
    assert wrong.static_calls == 1


@pytest.mark.asyncio
async def test_history_contains_no_media_bytes(tmp_path: Path) -> None:
    _HISTORY.clear()
    await _run(tmp_path, CountingRenderer())
    text = _HISTORY[-1]
    assert "iVBOR" not in text  # base64 of the PNG signature
    assert "R0lGODlh" not in text  # base64 of GIF89a
    assert "GIF89a" not in text
    assert "\\u0089PNG" not in text and "\x89PNG" not in text


@pytest.mark.asyncio
async def test_same_package_produces_same_manifest(tmp_path: Path) -> None:
    first, _ = await _run(tmp_path / "a", CountingRenderer())
    second, _ = await _run(tmp_path / "b", CountingRenderer())
    assert first.manifest == second.manifest
