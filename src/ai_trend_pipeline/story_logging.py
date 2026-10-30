"""Fixed, replay-safe operational events for story generation.

Pure stdlib module: `StoryGenerationWorkflow` imports it inside
`workflow.unsafe.imports_passed_through()`, and it is the only writer of `story_generation.*`
records. Activities stay log-free, so no untrusted text can enter a record.
"""

from enum import StrEnum

from ai_trend_pipeline.operational_logging import (
    FORBIDDEN_LOG_FIELDS,
    LogValue,
    StructuredLogger,
    check_log_field,
)

FORBIDDEN_STORY_LOG_FIELDS = FORBIDDEN_LOG_FIELDS | frozenset(
    {
        "excerpt",
        "evidence",
        "draft",
        "critique",
        "package",
        "note",
        "decided_by",
        "email",
        "token",
        "authorization",
        "cookie",
        "context",
        "fact",
        "story_text",
        "status_detail",
        "reason",
    }
)

__all__ = [
    "FORBIDDEN_STORY_LOG_FIELDS",
    "StoryLogEvent",
    "log_story_event",
]


class StoryLogEvent(StrEnum):
    """The fixed story event set; values are the log messages."""

    STARTED = "story_generation.started"
    STATUS = "story_generation.status"
    RESEARCH_COMPLETED = "story_generation.research_completed"
    FACTS_COMPLETED = "story_generation.facts_completed"
    DRAFT_COMPLETED = "story_generation.draft_completed"
    CHECK_COMPLETED = "story_generation.check_completed"
    REVISION = "story_generation.revision"
    ACTIVITY_FAILED = "story_generation.activity_failed"
    REVIEW_WAITING = "story_generation.review_waiting"
    DECISION = "story_generation.decision"
    IGNORED_SIGNAL = "story_generation.ignored_signal"
    FINISHED = "story_generation.finished"


_ERROR_EVENTS = frozenset({StoryLogEvent.ACTIVITY_FAILED})
_WARNING_EVENTS = frozenset({StoryLogEvent.REVISION, StoryLogEvent.IGNORED_SIGNAL})


def log_story_event(
    logger: StructuredLogger,
    event: StoryLogEvent,
    **fields: LogValue,
) -> None:
    """Emit one event whose message is the event value; reject untrusted or non-primitive data."""
    payload: dict[str, object] = {"event": event.value}
    for name, value in fields.items():
        payload[name] = check_log_field(name, value, forbidden=FORBIDDEN_STORY_LOG_FIELDS)
    if event in _ERROR_EVENTS:
        logger.error(event.value, extra=payload)
    elif event in _WARNING_EVENTS:
        logger.warning(event.value, extra=payload)
    else:
        logger.info(event.value, extra=payload)
