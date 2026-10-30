"""Story log events, level mapping, and the forbidden-field guard."""

import logging
from typing import cast

import pytest
from pydantic import SecretStr

from ai_trend_pipeline.ingestion_logging import (
    FORBIDDEN_LOG_FIELDS as INGESTION_FORBIDDEN,
)
from ai_trend_pipeline.ingestion_logging import (
    MAX_LOG_VALUE_LENGTH as INGESTION_MAX_LENGTH,
)
from ai_trend_pipeline.operational_logging import (
    FORBIDDEN_LOG_FIELDS,
    MAX_LOG_VALUE_LENGTH,
    StructuredLogger,
    check_log_field,
)
from ai_trend_pipeline.story_logging import (
    FORBIDDEN_STORY_LOG_FIELDS,
    StoryLogEvent,
    log_story_event,
)

LOGGER_NAME = "tests.story_logging"


def _logger() -> StructuredLogger:
    return cast(StructuredLogger, logging.getLogger(LOGGER_NAME))


def record_field(record: logging.LogRecord, name: str) -> object:
    return record.__dict__.get(name)


def test_events_are_namespaced_and_unique() -> None:
    values = [event.value for event in StoryLogEvent]
    assert all(value.startswith("story_generation.") for value in values)
    assert len(values) == len(set(values))
    assert len(values) == 12


def test_shared_primitives_are_reexported_by_ingestion_logging() -> None:
    assert MAX_LOG_VALUE_LENGTH == 256
    assert INGESTION_MAX_LENGTH == MAX_LOG_VALUE_LENGTH
    assert INGESTION_FORBIDDEN is FORBIDDEN_LOG_FIELDS
    assert FORBIDDEN_LOG_FIELDS <= FORBIDDEN_STORY_LOG_FIELDS


@pytest.mark.parametrize(
    ("event", "level"),
    [
        (StoryLogEvent.STARTED, logging.INFO),
        (StoryLogEvent.STATUS, logging.INFO),
        (StoryLogEvent.RESEARCH_COMPLETED, logging.INFO),
        (StoryLogEvent.FACTS_COMPLETED, logging.INFO),
        (StoryLogEvent.DRAFT_COMPLETED, logging.INFO),
        (StoryLogEvent.CHECK_COMPLETED, logging.INFO),
        (StoryLogEvent.REVISION, logging.WARNING),
        (StoryLogEvent.ACTIVITY_FAILED, logging.ERROR),
        (StoryLogEvent.REVIEW_WAITING, logging.INFO),
        (StoryLogEvent.DECISION, logging.INFO),
        (StoryLogEvent.IGNORED_SIGNAL, logging.WARNING),
        (StoryLogEvent.FINISHED, logging.INFO),
    ],
)
def test_log_story_event_sets_message_event_and_level(
    caplog: pytest.LogCaptureFixture, event: StoryLogEvent, level: int
) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    log_story_event(_logger(), event, status="ok", count=1)

    record = caplog.records[-1]
    assert record.getMessage() == event.value
    assert record.levelno == level
    assert record_field(record, "event") == event.value
    assert record_field(record, "status") == "ok"
    assert record_field(record, "count") == 1


def test_forbidden_names_are_rejected_case_insensitively(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    logger = _logger()
    for name in FORBIDDEN_STORY_LOG_FIELDS:
        with pytest.raises(ValueError):
            log_story_event(logger, StoryLogEvent.STATUS, **{name: "value"})
    for name in ("Title", "EVIDENCE", "Draft", "Note", "Authorization"):
        with pytest.raises(ValueError):
            log_story_event(logger, StoryLogEvent.STATUS, **{name: "value"})


def test_non_primitives_raise_and_strings_truncate(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    logger = _logger()
    for value in ({"a": 1}, [1], SecretStr("secret")):
        with pytest.raises(ValueError):
            log_story_event(
                logger,
                StoryLogEvent.STATUS,
                status=cast(str | int | float | bool | None, value),
            )

    log_story_event(logger, StoryLogEvent.STATUS, status="x" * 400)
    record = caplog.records[-1]
    assert record_field(record, "status") == "x" * MAX_LOG_VALUE_LENGTH


def test_check_log_field_is_the_shared_guard() -> None:
    assert check_log_field("count", 3, forbidden=FORBIDDEN_STORY_LOG_FIELDS) == 3
    with pytest.raises(ValueError):
        check_log_field("note", "text", forbidden=FORBIDDEN_STORY_LOG_FIELDS)
    with pytest.raises(ValueError):
        check_log_field("count", object(), forbidden=FORBIDDEN_STORY_LOG_FIELDS)
