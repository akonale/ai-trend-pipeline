"""Tests for the operator-facing ensure command and the real ingestion gateway."""

from typing import cast

import pytest
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment

import ai_trend_pipeline.ingestion_starter as ingestion_starter
from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.services.ingestion import SourceIngestionService
from ai_trend_pipeline.services.temporal_gateway import TemporalSourceIngestionGateway
from ai_trend_pipeline.source_ingestion import (
    SOURCE_INGESTION_TASK_QUEUE,
    source_ingestion_workflow_id,
)
from ai_trend_pipeline.sources import SourceName

INGESTION_QUERY = "WorkflowType = 'SourceIngestionWorkflow'"


async def start_env() -> WorkflowEnvironment:
    # The SDK default for search_attributes contains an unresolved generic; no attributes here.
    return await WorkflowEnvironment.start_local(  # pyright: ignore[reportUnknownMemberType]
        data_converter=pydantic_data_converter
    )


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def start_workflow(
        self,
        workflow: object,
        arg: object = None,
        *,
        id: str,
        task_queue: str,
        id_conflict_policy: object = None,
        id_reuse_policy: object = None,
    ) -> None:
        self.calls.append((id, task_queue))


@pytest.mark.asyncio
async def test_gateway_starts_one_workflow_per_enabled_source() -> None:
    async with await start_env() as env:
        service = SourceIngestionService(TemporalSourceIngestionGateway(env.client), Settings())
        result = await service.ensure()
        executions = [execution async for execution in env.client.list_workflows(INGESTION_QUERY)]
    assert result.workflow_ids == (source_ingestion_workflow_id(SourceName.HN),)
    assert len(executions) == 1
    execution = executions[0]
    assert execution.id == source_ingestion_workflow_id(SourceName.HN)
    assert execution.workflow_type == "SourceIngestionWorkflow"
    assert execution.task_queue == SOURCE_INGESTION_TASK_QUEUE


@pytest.mark.asyncio
async def test_gateway_is_idempotent() -> None:
    async with await start_env() as env:
        service = SourceIngestionService(TemporalSourceIngestionGateway(env.client), Settings())
        first = await service.ensure()
        handle = env.client.get_workflow_handle(first.workflow_ids[0])
        first_run_id = (await handle.describe()).run_id
        second = await service.ensure()
        second_run_id = (await handle.describe()).run_id
        executions = [execution async for execution in env.client.list_workflows(INGESTION_QUERY)]
    expected = (source_ingestion_workflow_id(SourceName.HN),)
    assert first.workflow_ids == second.workflow_ids == expected
    assert first_run_id == second_run_id
    assert len(executions) == 1


@pytest.mark.asyncio
async def test_gateway_starts_none_when_no_sources_enabled() -> None:
    async with await start_env() as env:
        service = SourceIngestionService(
            TemporalSourceIngestionGateway(env.client),
            Settings(source_enabled=frozenset()),
        )
        result = await service.ensure()
        executions = [execution async for execution in env.client.list_workflows(INGESTION_QUERY)]
    assert result.workflow_ids == ()
    assert executions == []


@pytest.mark.asyncio
async def test_starter_main_prints_only_ensured_ids_and_raises_on_connect_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake_connect(settings: Settings) -> Client:
        return cast(Client, FakeClient())

    monkeypatch.setattr(ingestion_starter, "connect", fake_connect)
    monkeypatch.setattr(ingestion_starter, "load_settings", lambda: Settings())
    await ingestion_starter.main()
    captured = capsys.readouterr()
    assert captured.out == "source-ingestion-v1-hn\n"
    assert captured.err == ""
    assert "Show HN" not in captured.out
    assert "test-key" not in captured.out

    async def failing_connect(settings: Settings) -> Client:
        raise RuntimeError("connect failed")

    monkeypatch.setattr(ingestion_starter, "connect", failing_connect)
    with pytest.raises(RuntimeError, match="connect failed"):
        await ingestion_starter.main()
    assert capsys.readouterr().out == ""
