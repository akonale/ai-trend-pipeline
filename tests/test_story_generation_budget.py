"""The bounded story budgets: exact constants, settings bounds and attempt caps."""

from datetime import timedelta
from typing import Any

import pytest
from pydantic import ValidationError
from temporalio.exceptions import ApplicationError

from ai_trend_pipeline.analysis.models import MAX_FACTS
from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.research.models import (
    MAX_CONTEXT_TEXT_CHARS,
    MAX_CONTEXTS,
    MAX_DATASET_COUNTRIES,
    MAX_DATASET_ROWS,
    MAX_EVIDENCE,
    MAX_FAILURES,
    MAX_NOTES,
    MAX_TOTAL_CONTEXT_CHARS,
)
from ai_trend_pipeline.story_candidate import (
    DEFAULT_CANDIDATE_REVIEW_TIMEOUT_S,
    STORY_HANDOFF_PATCH,
)
from ai_trend_pipeline.story_drafting import CritiqueInput, CritiqueOutput, DraftInput, DraftOutput
from ai_trend_pipeline.story_drafting.models import (
    MAX_BODY_CHARS,
    MAX_CAVEATS,
    MAX_CRITIQUE_TOKENS,
    MAX_DRAFT_PROMPT_CHARS,
    MAX_DRAFT_TOKENS,
    MAX_HEADLINE_CHARS,
    MAX_KEY_POINTS,
    MAX_POST_TEXT_CHARS,
    MAX_REVISION_ROUNDS,
    MAX_SOURCE_LINKS,
    MAX_SUMMARY_CHARS,
)
from ai_trend_pipeline.story_generation import (
    ACTIVITY_MAX_ATTEMPTS,
    CRITIQUE_ACTIVITY_TIMEOUT,
    DRAFT_ACTIVITY_TIMEOUT,
    FACTS_ACTIVITY_TIMEOUT,
    MAX_REVIEW_TIMEOUT_S,
    MIN_REVIEW_TIMEOUT_S,
    RESEARCH_ACTIVITY_TIMEOUT,
    STORY_DRAFT_MAX_FACTS,
    STORY_ID_PREFIX,
    FinalStoryDecision,
    StoryGenerationStatus,
    StoryGenerationWorkflow,
)
from ai_trend_pipeline.story_status import DEFAULT_STORY_REVIEW_TIMEOUT_S, STORY_UI_RETENTION
from tests.drafting_fixtures import make_failed_critique, make_passing_critique
from tests.story_generation_fixtures import make_draft_output
from tests.test_story_generation import (
    FakeStoryActivities,
    make_worker,
    start_env,
    start_story,
    wait_for_status,
)


def test_candidate_budget_and_story_identity_are_bounded() -> None:
    settings = Settings()
    assert settings.source_candidate_budget == 20
    for invalid in (0, 501):
        with pytest.raises(ValidationError):
            Settings(source_candidate_budget=invalid)
    assert STORY_ID_PREFIX == "story-v1-"
    assert STORY_HANDOFF_PATCH == "story-generation-handoff-v1"


def test_research_http_and_context_bounds() -> None:
    settings = Settings()
    assert settings.research_fetch_timeout_s == 10.0
    assert settings.research_max_response_bytes == 524288
    assert settings.research_max_redirects == 3
    for payload in (
        {"research_fetch_timeout_s": 0},
        {"research_fetch_timeout_s": 61},
        {"research_max_response_bytes": 1023},
        {"research_max_response_bytes": 4194305},
        {"research_max_redirects": -1},
        {"research_max_redirects": 6},
    ):
        with pytest.raises(ValidationError):
            Settings.model_validate(payload)

    assert (MAX_CONTEXTS, MAX_CONTEXT_TEXT_CHARS, MAX_TOTAL_CONTEXT_CHARS) == (3, 8_000, 16_000)
    assert (MAX_EVIDENCE, MAX_DATASET_ROWS, MAX_DATASET_COUNTRIES) == (4, 300, 20)
    assert (MAX_FAILURES, MAX_NOTES) == (8, 8)


def test_fact_and_text_bounds() -> None:
    assert (STORY_DRAFT_MAX_FACTS, MAX_FACTS) == (16, 32)
    assert MAX_DRAFT_PROMPT_CHARS == 100_000
    assert (MAX_DRAFT_TOKENS, MAX_CRITIQUE_TOKENS) == (4_096, 2_048)
    assert (MAX_HEADLINE_CHARS, MAX_SUMMARY_CHARS, MAX_BODY_CHARS) == (160, 600, 6_000)
    assert (MAX_KEY_POINTS, MAX_SOURCE_LINKS, MAX_CAVEATS, MAX_POST_TEXT_CHARS) == (
        5,
        4,
        5,
        300,
    )
    assert MAX_REVISION_ROUNDS == 1


def test_activity_attempts_timeouts_and_review_caps() -> None:
    assert ACTIVITY_MAX_ATTEMPTS == 3
    assert RESEARCH_ACTIVITY_TIMEOUT == timedelta(seconds=120)
    assert FACTS_ACTIVITY_TIMEOUT == timedelta(seconds=30)
    assert DRAFT_ACTIVITY_TIMEOUT == timedelta(seconds=120)
    assert CRITIQUE_ACTIVITY_TIMEOUT == timedelta(seconds=120)
    assert (MIN_REVIEW_TIMEOUT_S, MAX_REVIEW_TIMEOUT_S) == (1, 604_800)
    assert DEFAULT_STORY_REVIEW_TIMEOUT_S == 172_800
    assert DEFAULT_CANDIDATE_REVIEW_TIMEOUT_S == 172_800
    assert STORY_UI_RETENTION == timedelta(hours=48)
    for invalid in (0, 604_801):
        with pytest.raises(ValidationError):
            Settings(candidate_review_timeout_s=invalid)


@pytest.mark.asyncio
async def test_retryable_draft_failure_is_attempted_exactly_three_times() -> None:
    def failing(_index: int, _data: Any) -> Any:
        raise ApplicationError("transient writer failure")

    fakes = FakeStoryActivities(draft=failing)
    async with await start_env() as env, make_worker(env, fakes):
        result = await (await start_story(env)).result()
    assert result.status == "failed"
    assert fakes.calls["draft_story"] == ACTIVITY_MAX_ATTEMPTS == 3


@pytest.mark.asyncio
async def test_success_and_revision_paths_stay_within_call_caps() -> None:
    fakes = FakeStoryActivities()
    async with await start_env() as env, make_worker(env, fakes):
        handle = await start_story(env)
        await wait_for_status(handle, StoryGenerationStatus.REVIEW)
        await handle.signal(StoryGenerationWorkflow.approve, FinalStoryDecision())
        await handle.result()
    assert fakes.calls == {
        "research_story": 1,
        "calculate_facts": 1,
        "draft_story": 1,
        "critique_story": 1,
    }

    def draft(index: int, data: DraftInput) -> DraftOutput:
        return make_draft_output(
            data.story.candidate_id,
            headline="First draft" if index == 0 else "Revised draft",
        )

    def critique(index: int, data: CritiqueInput) -> CritiqueOutput:
        story_id = data.draft.story_id
        if index == 0:
            return make_failed_critique(story_id=story_id)
        return make_passing_critique(story_id)

    revision = FakeStoryActivities(draft=draft, critique=critique)
    async with await start_env() as env, make_worker(env, revision):
        handle = await start_story(env)
        await wait_for_status(handle, StoryGenerationStatus.REVIEW)
        await handle.signal(StoryGenerationWorkflow.approve, FinalStoryDecision())
        await handle.result()
    assert revision.calls["research_story"] == 1
    assert revision.calls["calculate_facts"] == 1
    assert revision.calls["draft_story"] == 2
    assert revision.calls["critique_story"] == 2
