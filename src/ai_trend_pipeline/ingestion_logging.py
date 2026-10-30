"""Fixed, replay-safe operational events for source ingestion.

Pure module: it imports only `sources.models`, the shared operational guard and the standard
library, so deterministic workflow code may import it inside
`workflow.unsafe.imports_passed_through()`. The field guard, not reviewer discipline, keeps source
text and secrets out of the operational log.
"""

from enum import StrEnum

from ai_trend_pipeline.operational_logging import (
    FORBIDDEN_LOG_FIELDS,
    MAX_LOG_VALUE_LENGTH,
    LogValue,
    StructuredLogger,
    check_log_field,
)
from ai_trend_pipeline.sources.models import SourceName

__all__ = [
    "FORBIDDEN_LOG_FIELDS",
    "MAX_LOG_VALUE_LENGTH",
    "IngestionLogEvent",
    "LogValue",
    "StructuredLogger",
    "log_event",
]


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


def log_event(
    logger: StructuredLogger,
    event: IngestionLogEvent,
    source: SourceName,
    **fields: str | int | float | bool | None,
) -> None:
    """Emit one event with `event` and `source` plus the caller's primitive fields."""
    payload: dict[str, object] = {"event": event.value, "source": source.value}
    for name, value in fields.items():
        payload[name] = check_log_field(name, value, forbidden=FORBIDDEN_LOG_FIELDS)
    if event in _ERROR_EVENTS:
        logger.error(event.value, extra=payload)
    elif event in _WARNING_EVENTS:
        logger.warning(event.value, extra=payload)
    else:
        logger.info(event.value, extra=payload)
