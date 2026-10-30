"""Real workflow runs emit the fixed story event set without untrusted text."""

import logging
from typing import Any, cast

import pytest
from temporalio.exceptions import ApplicationError

from ai_trend_pipeline.story_drafting import CritiqueInput, CritiqueOutput, DraftInput, DraftOutput
from ai_trend_pipeline.story_generation import (
    FinalStoryDecision,
    StoryGenerationStatus,
    StoryGenerationWorkflow,
)
from ai_trend_pipeline.story_logging import StoryLogEvent
from tests.drafting_fixtures import make_failed_critique, make_passing_critique
from tests.story_generation_fixtures import make_draft_output
from tests.test_story_generation import (
    FakeStoryActivities,
    make_worker,
    start_env,
    start_story,
    wait_for_status,
)

WORKFLOW_LOGGER = "temporalio.workflow"
TITLE_SENTINEL = "Life expectancy keeps rising"
URL_SENTINEL = "https://news.example/story"
SUMMARY_SENTINEL = "Global life expectancy climbed again."
NOTE_SENTINEL = "SENTINEL-note-do-not-log"
SECRET_SENTINEL = "sk-ant-sentinel-do-not-log-me"


def records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if "event" in record.__dict__]


def event_names(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [cast(str, record.__dict__["event"]) for record in records(caplog)]


def event_records(
    caplog: pytest.LogCaptureFixture, event: StoryLogEvent
) -> list[logging.LogRecord]:
    return [record for record in records(caplog) if record.__dict__["event"] == event.value]


def log_text(record: logging.LogRecord) -> str:
    parts = [record.getMessage()]
    parts.extend(str(value) for value in record.__dict__.values())
    return " ".join(parts)


@pytest.mark.asyncio
async def test_success_run_emits_the_fixed_event_set(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=WORKFLOW_LOGGER)
    fakes = FakeStoryActivities()
    async with await start_env() as env, make_worker(env, fakes):
        handle = await start_story(env)
        await wait_for_status(handle, StoryGenerationStatus.REVIEW)
        await handle.signal(StoryGenerationWorkflow.approve, FinalStoryDecision(note=NOTE_SENTINEL))
        await handle.result()

    names = event_names(caplog)
    for event in (
        StoryLogEvent.STARTED,
        StoryLogEvent.STATUS,
        StoryLogEvent.RESEARCH_COMPLETED,
        StoryLogEvent.FACTS_COMPLETED,
        StoryLogEvent.DRAFT_COMPLETED,
        StoryLogEvent.CHECK_COMPLETED,
        StoryLogEvent.REVIEW_WAITING,
        StoryLogEvent.DECISION,
        StoryLogEvent.FINISHED,
    ):
        assert event.value in names, event.value

    started = event_records(caplog, StoryLogEvent.STARTED)[0]
    assert started.__dict__["status"] == StoryGenerationStatus.RESEARCHING.value
    research = event_records(caplog, StoryLogEvent.RESEARCH_COMPLETED)[0]
    assert research.__dict__["status"] == "supported"
    assert cast(int, research.__dict__["context_count"]) >= 1
    decision = event_records(caplog, StoryLogEvent.DECISION)[0]
    assert decision.__dict__["decision"] == "approved"
    assert decision.__dict__["has_note"] is True
    finished = event_records(caplog, StoryLogEvent.FINISHED)[0]
    assert finished.__dict__["status"] == "ready"

    for record in records(caplog):
        text = log_text(record)
        for sentinel in (TITLE_SENTINEL, URL_SENTINEL, SUMMARY_SENTINEL, NOTE_SENTINEL):
            assert sentinel not in text, sentinel
        for forbidden in ("title", "url", "summary", "note", "secret"):
            assert forbidden not in record.__dict__


@pytest.mark.asyncio
async def test_revision_run_emits_revision_and_two_draft_checks(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=WORKFLOW_LOGGER)

    def draft(index: int, data: Any) -> DraftOutput:
        return make_draft_output(
            cast(DraftInput, data).story.candidate_id,
            headline="First draft" if index == 0 else "Revised draft",
        )

    def critique(index: int, data: Any) -> CritiqueOutput:
        story_id = cast(CritiqueInput, data).draft.story_id
        if index == 0:
            return make_failed_critique(story_id=story_id)
        return make_passing_critique(story_id)

    async with (
        await start_env() as env,
        make_worker(env, FakeStoryActivities(draft=draft, critique=critique)),
    ):
        handle = await start_story(env)
        await wait_for_status(handle, StoryGenerationStatus.REVIEW)
        await handle.signal(StoryGenerationWorkflow.approve, FinalStoryDecision())
        await handle.result()

    revision = event_records(caplog, StoryLogEvent.REVISION)
    assert len(revision) == 1
    assert revision[0].levelno == logging.WARNING
    assert revision[0].__dict__["round"] == 1
    drafts = event_records(caplog, StoryLogEvent.DRAFT_COMPLETED)
    assert [record.__dict__["round"] for record in drafts] == [0, 1]
    checks = event_records(caplog, StoryLogEvent.CHECK_COMPLETED)
    assert [record.__dict__["passed"] for record in checks] == [False, True]


@pytest.mark.asyncio
async def test_activity_failure_logs_error_type_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=WORKFLOW_LOGGER)

    def failing(_index: int, _data: Any) -> Any:
        raise ApplicationError(f"retryable failure {SECRET_SENTINEL}")

    async with await start_env() as env, make_worker(env, FakeStoryActivities(draft=failing)):
        result = await (await start_story(env)).result()

    assert result.status == "failed"
    failures = event_records(caplog, StoryLogEvent.ACTIVITY_FAILED)
    assert len(failures) == 1
    failure = failures[0]
    assert failure.levelno == logging.ERROR
    assert failure.__dict__["activity"] == "draft_story"
    assert failure.__dict__["error_type"] == "ApplicationError"
    assert failure.__dict__["status"] == StoryGenerationStatus.WRITING.value
    for record in records(caplog):
        assert SECRET_SENTINEL not in log_text(record)


@pytest.mark.asyncio
async def test_ignored_signal_is_a_warning_without_note_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=WORKFLOW_LOGGER)
    fakes = FakeStoryActivities(gated=True)
    async with await start_env() as env, make_worker(env, fakes):
        handle = await start_story(env)
        await handle.signal(StoryGenerationWorkflow.approve, FinalStoryDecision(note=NOTE_SENTINEL))
        for name in ("research_story", "calculate_facts", "draft_story", "critique_story"):
            await fakes.wait_for_call(name, 1)
            await fakes.release(name)
        await wait_for_status(handle, StoryGenerationStatus.REVIEW)
        await handle.signal(StoryGenerationWorkflow.approve, FinalStoryDecision())
        await handle.result()

    ignored = event_records(caplog, StoryLogEvent.IGNORED_SIGNAL)
    assert len(ignored) >= 1
    assert ignored[0].levelno == logging.WARNING
    assert ignored[0].__dict__["signal"] == "approve"
    assert "status" in ignored[0].__dict__
    for record in records(caplog):
        assert NOTE_SENTINEL not in log_text(record)
