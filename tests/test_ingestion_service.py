"""Tests for SourceIngestionService and its settings mapping (fake gateway, no Temporal)."""

from collections.abc import Sequence

import pytest

from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.services.ingestion import (
    SourceIngestionService,
    source_ingestion_input,
)
from ai_trend_pipeline.source_ingestion import SourceIngestionInput
from ai_trend_pipeline.sources import SourceName
from ai_trend_pipeline.story_selection import DEFAULT_MAX_CANDIDATES


class FakeGateway:
    def __init__(self, workflow_ids: Sequence[str] = ("ensured-id",)) -> None:
        self.workflow_ids = list(workflow_ids)
        self.calls: list[tuple[SourceName, SourceIngestionInput]] = []
        self.error: Exception | None = None

    async def start(self, source: SourceName, data: SourceIngestionInput) -> str:
        if self.error is not None:
            raise self.error
        self.calls.append((source, data))
        index = len(self.calls) - 1
        if index < len(self.workflow_ids):
            return self.workflow_ids[index]
        return f"ensured-{source.value}"


def test_source_ingestion_input_maps_settings() -> None:
    settings = Settings(
        source_poll_limit=7,
        source_poll_interval_s=45,
        candidate_review_timeout_s=120,
    )
    data = source_ingestion_input(settings, SourceName.HN)
    assert data.source is SourceName.HN
    assert data.poll_limit == 7
    assert data.poll_interval_seconds == 45
    assert data.review_timeout_seconds == 120
    assert data.max_candidates == DEFAULT_MAX_CANDIDATES
    assert data.cursor is None
    assert data.processed_ids == ()
    assert data.polls_in_run == 0
    assert data.total_polls == 0


@pytest.mark.asyncio
async def test_ensure_starts_one_workflow_per_enabled_source() -> None:
    gateway = FakeGateway(["source-ingestion-v1-hn"])
    settings = Settings(source_enabled=frozenset({SourceName.HN}))
    service = SourceIngestionService(gateway, settings)
    result = await service.ensure()
    assert len(gateway.calls) == 1
    source, data = gateway.calls[0]
    assert source is SourceName.HN
    assert data == source_ingestion_input(settings, SourceName.HN)
    assert result.workflow_ids == ("source-ingestion-v1-hn",)


@pytest.mark.asyncio
async def test_ensure_starts_none_when_no_sources_enabled() -> None:
    gateway = FakeGateway()
    service = SourceIngestionService(gateway, Settings(source_enabled=frozenset()))
    result = await service.ensure()
    assert gateway.calls == []
    assert result.workflow_ids == ()


@pytest.mark.asyncio
async def test_ensure_returns_ensured_ids_and_uses_configured_settings() -> None:
    gateway = FakeGateway(["custom-id"])
    settings = Settings(
        source_enabled=frozenset({SourceName.HN}),
        source_poll_limit=11,
        source_poll_interval_s=60,
        candidate_review_timeout_s=90,
    )
    service = SourceIngestionService(gateway, settings)
    result = await service.ensure()
    assert result.workflow_ids == ("custom-id",)
    assert gateway.calls[0][1].poll_limit == 11
    assert gateway.calls[0][1].poll_interval_seconds == 60
    assert gateway.calls[0][1].review_timeout_seconds == 90


@pytest.mark.asyncio
async def test_service_reports_gateway_errors_without_swallowing_them() -> None:
    gateway = FakeGateway()
    gateway.error = RuntimeError("temporal unavailable")
    service = SourceIngestionService(gateway, Settings())
    with pytest.raises(RuntimeError, match="temporal unavailable"):
        await service.ensure()
