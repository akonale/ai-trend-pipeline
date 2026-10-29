"""Deterministic fake connector for contract tests and the pluggability proof.

Deliberately not registered and not wired into a worker in this task.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from ai_trend_pipeline.sources.models import (
    SourceName,
    SourcePollInput,
    SourcePollOutput,
)
from ai_trend_pipeline.sources.normalize import RawSourceItem, normalize_events


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
