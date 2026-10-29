"""The test-only second source: fixed items, normalized like any real connector.

Registered as `Test source` and wired into the ingestion worker so pluggability can be proven
without network or model calls. It is never enabled by default and must not appear in `.env.prod`.
This is an activity module; workflow code must never import it.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from temporalio import activity

from ai_trend_pipeline.ingestion_logging import IngestionLogEvent, log_event
from ai_trend_pipeline.sources.models import (
    SourceName,
    SourcePollInput,
    SourcePollOutput,
)
from ai_trend_pipeline.sources.normalize import RawSourceItem, normalize_events

DEFAULT_FAKE_ITEMS: tuple[RawSourceItem, ...] = (
    RawSourceItem(
        external_id="fake-1001",
        title="Fake source: Transit pilot extends weekend service",
        url="https://example.com/fake-source/1001",
        published_at=None,
        metrics={"score": 42, "comments": 7},
    ),
    RawSourceItem(
        external_id="fake-1002",
        title="Fake source: Library adds evening hours",
        url="https://example.com/fake-source/1002",
        published_at=None,
        metrics={"score": 18, "comments": 2},
    ),
)


class FakeSourceConnector:
    def __init__(
        self,
        items: Sequence[RawSourceItem],
        *,
        observed_at: datetime | None = None,
    ) -> None:
        self._items = tuple(items)
        self._observed_at = observed_at

    async def poll(self, data: SourcePollInput) -> SourcePollOutput:
        if data.source is not SourceName.FAKE:
            raise ValueError(f"FakeSourceConnector cannot poll source {data.source.value!r}")
        observed_at = self._observed_at if self._observed_at is not None else datetime.now(UTC)
        return normalize_events(
            source=SourceName.FAKE,
            items=self._items,
            limit=data.limit,
            observed_at=observed_at,
        )


class FakeSourceActivities:
    """Activity wrapper for the fake source; the worker constructs it without arguments."""

    def __init__(self, items: Sequence[RawSourceItem] | None = None) -> None:
        self._items = DEFAULT_FAKE_ITEMS if items is None else tuple(items)

    @activity.defn
    async def poll_fake_source(self, data: SourcePollInput) -> SourcePollOutput:
        if data.source is not SourceName.FAKE:
            raise ValueError(f"FakeSourceActivities cannot poll source {data.source.value!r}")
        output = normalize_events(
            source=SourceName.FAKE,
            items=self._items,
            limit=data.limit,
            observed_at=datetime.now(UTC),
        )
        log_event(
            activity.logger,
            IngestionLogEvent.EVENTS_NORMALIZED,
            SourceName.FAKE,
            requested_limit=data.limit,
            event_count=len(output.events),
        )
        return output
