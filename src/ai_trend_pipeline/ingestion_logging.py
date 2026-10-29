"""Fixed, replay-safe operational events for source ingestion.

Pure module: it imports only `sources.models` and the standard library, so deterministic workflow
code may import it inside `workflow.unsafe.imports_passed_through()`. The field guard, not reviewer
discipline, keeps source text and secrets out of the operational log.
"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Protocol

from ai_trend_pipeline.sources.models import SourceName

MAX_LOG_VALUE_LENGTH = 256

FORBIDDEN_LOG_FIELDS = frozenset(
    {
        "title",
        "url",
        "summary",
        "proposed_angle",
        "selection_reason",
        "instructions",
        "prompt",
        "response",
        "payload",
        "body",
        "text",
        "api_key",
        "password",
        "secret",
    }
)

LogValue = str | int | float | bool | None


class IngestionLogEvent(StrEnum):
    """The fixed operational event set; values are the log messages."""

    STARTED = "candidate_ingestion.started"
    POLL = "candidate_ingestion.poll"
    POLL_FAILED = "candidate_ingestion.poll_failed"
    EVENTS_NORMALIZED = "candidate_ingestion.events_normalized"
    SELECTION = "candidate_ingestion.selection"
    SELECTION_FAILED = "candidate_ingestion.selection_failed"
    SELECTION_OUTSIDE_BATCH = "candidate_ingestion.selection_outside_batch"
    CANDIDATE_STARTED = "candidate_ingestion.candidate_started"
    CANDIDATE_DUPLICATE = "candidate_ingestion.candidate_duplicate"
    BUDGET_EXHAUSTED = "candidate_ingestion.budget_exhausted"
    CONTINUE_AS_NEW = "candidate_ingestion.continue_as_new"
    STOPPED = "candidate_ingestion.stopped"


_ERROR_EVENTS = frozenset({IngestionLogEvent.POLL_FAILED, IngestionLogEvent.SELECTION_FAILED})
_WARNING_EVENTS = frozenset(
    {
        IngestionLogEvent.SELECTION_OUTSIDE_BATCH,
        IngestionLogEvent.CANDIDATE_DUPLICATE,
        IngestionLogEvent.BUDGET_EXHAUSTED,
    }
)


class StructuredLogger(Protocol):
    """The subset of the SDK workflow/activity logger that ingestion needs."""

    def info(self, msg: str, *, extra: Mapping[str, object]) -> None: ...

    def warning(self, msg: str, *, extra: Mapping[str, object]) -> None: ...

    def error(self, msg: str, *, extra: Mapping[str, object]) -> None: ...


def _checked_field(name: str, value: object) -> object:
    if name.casefold() in FORBIDDEN_LOG_FIELDS:
        raise ValueError(f"forbidden log field: {name}")
    if isinstance(value, str):
        return value[:MAX_LOG_VALUE_LENGTH]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise ValueError(f"log field {name!r} must be a primitive, got {type(value).__name__}")


def log_event(
    logger: StructuredLogger,
    event: IngestionLogEvent,
    source: SourceName,
    **fields: str | int | float | bool | None,
) -> None:
    """Emit one event with `event` and `source` plus the caller's primitive fields."""
    payload: dict[str, object] = {"event": event.value, "source": source.value}
    for name, value in fields.items():
        payload[name] = _checked_field(name, value)
    if event in _ERROR_EVENTS:
        logger.error(event.value, extra=payload)
    elif event in _WARNING_EVENTS:
        logger.warning(event.value, extra=payload)
    else:
        logger.info(event.value, extra=payload)
