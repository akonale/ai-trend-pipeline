"""Pre-053 candidate history replay and old-input compatibility."""

import asyncio
import uuid
from pathlib import Path

import pytest
from temporalio.client import WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from ai_trend_pipeline.story_candidate import (
    CANDIDATE_TASK_QUEUE,
    CandidateDecision,
    CandidateStatus,
    StoryCandidateInput,
    StoryCandidateResult,
    StoryCandidateWorkflow,
)
from ai_trend_pipeline.story_status import DEFAULT_STORY_REVIEW_TIMEOUT_S
from ai_trend_pipeline.temporal_client import ensure_story_search_attributes
from tests.story_generation_fixtures import make_candidate

HISTORY = (
    Path(__file__).resolve().parent / "fixtures" / "histories" / "story_candidate_v1_approved.json"
)


@pytest.mark.asyncio
async def test_pre_053_approved_candidate_history_replays() -> None:
    history = WorkflowHistory.from_json(
        "candidate-v1-pre053-replay", HISTORY.read_text(encoding="utf-8")
    )
    replayer = Replayer(
        workflows=[StoryCandidateWorkflow],
        data_converter=pydantic_data_converter,
    )
    result = await replayer.replay_workflow(history)
    assert result.replay_failure is None


async def start_env() -> WorkflowEnvironment:
    env = await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)
    await ensure_story_search_attributes(env.client)
    return env


@pytest.mark.asyncio
async def test_old_candidate_input_without_new_fields_still_runs() -> None:
    async with await start_env() as env:
        now = await env.get_current_time()
        candidate = make_candidate(observed_at=now)
        old_payload = {"candidate": candidate.model_dump(mode="json")}
        data = StoryCandidateInput.model_validate(old_payload)
        assert data.story_review_timeout_seconds == DEFAULT_STORY_REVIEW_TIMEOUT_S
        async with Worker(
            env.client,
            task_queue=CANDIDATE_TASK_QUEUE,
            workflows=[StoryCandidateWorkflow],
        ):
            handle = await env.client.start_workflow(
                StoryCandidateWorkflow.run,
                data,
                id=f"candidate-old-{uuid.uuid4()}",
                task_queue=CANDIDATE_TASK_QUEUE,
            )
            for _ in range(500):
                state = await handle.query(StoryCandidateWorkflow.review_state)
                if state.status is CandidateStatus.AWAITING_REVIEW:
                    break
                await asyncio.sleep(0.01)
            await handle.signal(StoryCandidateWorkflow.approve, CandidateDecision(note="ok"))
            result: StoryCandidateResult = await handle.result()
    assert result.status == "approved"
    assert result.story_workflow_id is not None
    assert result.story_workflow_id.startswith("story-v1-")


GENERATION_HISTORY = (
    Path(__file__).resolve().parent / "fixtures" / "histories" / "story_generation_v1_ready.json"
)


@pytest.mark.asyncio
async def test_pre_media_story_generation_history_replays() -> None:
    from ai_trend_pipeline.story_generation import StoryGenerationWorkflow

    history = WorkflowHistory.from_json(
        "story-v1-pre058-replay", GENERATION_HISTORY.read_text(encoding="utf-8")
    )
    replayer = Replayer(
        workflows=[StoryGenerationWorkflow],
        data_converter=pydantic_data_converter,
    )
    result = await replayer.replay_workflow(history)
    assert result.replay_failure is None


MEDIA_GENERATION_HISTORY = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "histories"
    / "story_generation_v2_media_ready.json"
)


@pytest.mark.asyncio
async def test_pre_media_history_still_replays_without_child() -> None:
    await test_pre_media_story_generation_history_replays()


@pytest.mark.asyncio
async def test_media_history_replays() -> None:
    from ai_trend_pipeline.story_generation import StoryGenerationWorkflow

    history = WorkflowHistory.from_json(
        "story-v2-media-replay", MEDIA_GENERATION_HISTORY.read_text(encoding="utf-8")
    )
    replayer = Replayer(
        workflows=[StoryGenerationWorkflow],
        data_converter=pydantic_data_converter,
    )
    result = await replayer.replay_workflow(history)
    assert result.replay_failure is None
