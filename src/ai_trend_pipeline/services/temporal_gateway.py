"""Temporal adapter for the chart run gateway."""

from collections.abc import Awaitable
from datetime import UTC, datetime

from temporalio.client import Client, WorkflowHandle
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from ai_trend_pipeline.daily_chart import (
    DAILY_CHART_TASK_QUEUE,
    ApprovalDecision,
    DailyChartInput,
    DailyChartResult,
    DailyChartWorkflow,
    ReviewState,
)
from ai_trend_pipeline.services.charts import RunAlreadyExists, RunNotFound


def started_since_query(since: datetime) -> str:
    stamp = since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"WorkflowType = 'DailyChartWorkflow' AND StartTime >= '{stamp}'"


async def _translate[T](wf_id: str, call: Awaitable[T]) -> T:
    try:
        return await call
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            raise RunNotFound(wf_id) from exc
        raise


class TemporalChartRunHandle:
    def __init__(self, handle: WorkflowHandle[DailyChartWorkflow, DailyChartResult]) -> None:
        self._handle = handle

    @property
    def id(self) -> str:
        return self._handle.id

    async def review_state(self) -> ReviewState:
        return await _translate(self.id, self._handle.query(DailyChartWorkflow.review_state))

    async def send_decision(self, decision: ApprovalDecision) -> None:
        await _translate(self.id, self._handle.signal(DailyChartWorkflow.decide, decision))

    async def result(self) -> DailyChartResult:
        return await _translate(self.id, self._handle.result())


class TemporalChartRunGateway:
    def __init__(self, client: Client) -> None:
        self._client = client

    async def start(self, wf_id: str, data: DailyChartInput) -> None:
        memo = {"requested_by": data.requested_by} if data.requested_by is not None else None
        try:
            await self._client.start_workflow(
                DailyChartWorkflow.run,
                data,
                id=wf_id,
                task_queue=DAILY_CHART_TASK_QUEUE,
                id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
                memo=memo,
            )
        except WorkflowAlreadyStartedError as exc:
            raise RunAlreadyExists(wf_id) from exc

    async def count_started_since(self, requested_by: str, since: datetime) -> int:
        count = 0
        async for wf in self._client.list_workflows(started_since_query(since)):
            if await wf.memo_value("requested_by", None, type_hint=str) == requested_by:
                count += 1
        return count

    def handle(self, wf_id: str) -> TemporalChartRunHandle:
        return TemporalChartRunHandle(
            self._client.get_workflow_handle_for(DailyChartWorkflow.run, wf_id)
        )
