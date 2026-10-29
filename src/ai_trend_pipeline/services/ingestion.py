"""Source ingestion use case: ensure one long-running workflow per enabled source."""

from typing import Protocol

from pydantic import BaseModel, ConfigDict

from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.source_ingestion import SourceIngestionInput
from ai_trend_pipeline.sources import SourceName
from ai_trend_pipeline.sources.registry import enabled_sources


class SourceIngestionGateway(Protocol):
    async def start(self, source: SourceName, data: SourceIngestionInput) -> str: ...


class SourceIngestionEnsureResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    workflow_ids: tuple[str, ...] = ()


def source_ingestion_input(settings: Settings, source: SourceName) -> SourceIngestionInput:
    """Map settings to the workflow input; max_candidates keeps the model default."""
    return SourceIngestionInput(
        source=source,
        poll_limit=settings.source_poll_limit,
        poll_interval_seconds=settings.source_poll_interval_s,
        review_timeout_seconds=settings.candidate_review_timeout_s,
        candidate_budget=settings.source_candidate_budget,
    )


class SourceIngestionService:
    def __init__(self, gateway: SourceIngestionGateway, settings: Settings) -> None:
        self._gateway = gateway
        self._settings = settings

    async def ensure(self) -> SourceIngestionEnsureResult:
        workflow_ids: list[str] = []
        for definition in enabled_sources(self._settings.source_enabled):
            data = source_ingestion_input(self._settings, definition.name)
            workflow_ids.append(await self._gateway.start(definition.name, data))
        return SourceIngestionEnsureResult(workflow_ids=tuple(workflow_ids))
