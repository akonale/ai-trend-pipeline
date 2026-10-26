"""Chart use cases (start, show, decide) behind a run-gateway protocol."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel

from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.daily_chart import (
    ApprovalDecision,
    DailyChartInput,
    DailyChartResult,
    ReviewState,
)


class StartChartRequest(BaseModel):
    wf_id: str | None = None
    topic: str | None = None


class StartChartResult(BaseModel):
    wf_id: str


class ChartRunRef(BaseModel):
    wf_id: str


class DecideChartRequest(BaseModel):
    wf_id: str
    approved: bool
    note: str = ""


class ChartServiceError(Exception):
    """Base class for chart service errors."""


class RunNotFound(ChartServiceError):
    def __init__(self, wf_id: str) -> None:
        super().__init__(f"workflow {wf_id} not found")
        self.wf_id = wf_id


class RunAlreadyExists(ChartServiceError):
    def __init__(self, wf_id: str) -> None:
        super().__init__(f"a run with id {wf_id} already exists")
        self.wf_id = wf_id


class NotAwaitingDecision(ChartServiceError):
    def __init__(self, wf_id: str, status: str) -> None:
        super().__init__(f"cannot decide {wf_id}: status is {status}")
        self.wf_id = wf_id
        self.status = status


class ChartRunHandle(Protocol):
    @property
    def id(self) -> str: ...

    async def review_state(self) -> ReviewState: ...

    async def send_decision(self, decision: ApprovalDecision) -> None: ...

    async def result(self) -> DailyChartResult: ...


class ChartRunGateway(Protocol):
    async def start(self, wf_id: str, data: DailyChartInput) -> None: ...

    def handle(self, wf_id: str) -> ChartRunHandle: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ChartService:
    def __init__(
        self,
        gateway: ChartRunGateway,
        settings: Settings,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._gateway = gateway
        self._settings = settings
        self._now = now

    async def start(self, req: StartChartRequest) -> StartChartResult:
        wf_id = req.wf_id or f"daily-chart-{self._now().date().isoformat()}"
        output_dir = self._settings.chart_output_dir
        if output_dir:
            data = DailyChartInput(output_dir=output_dir, topic=req.topic)
        else:
            data = DailyChartInput(topic=req.topic)
        await self._gateway.start(wf_id, data)
        return StartChartResult(wf_id=wf_id)

    async def show(self, ref: ChartRunRef) -> ReviewState:
        return await self._gateway.handle(ref.wf_id).review_state()

    async def decide(self, req: DecideChartRequest) -> DailyChartResult:
        handle = self._gateway.handle(req.wf_id)
        state = await handle.review_state()
        if state.status != "awaiting_decision":
            raise NotAwaitingDecision(req.wf_id, state.status)
        await handle.send_decision(ApprovalDecision(approved=req.approved, note=req.note))
        return await handle.result()
