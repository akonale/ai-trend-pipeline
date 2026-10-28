"""Chart use cases (start, show, decide) behind a run-gateway protocol."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

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
    unique_id: bool = False


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


class ChartImageNotFound(ChartServiceError):
    def __init__(self, wf_id: str) -> None:
        super().__init__(f"no chart image for {wf_id}")
        self.wf_id = wf_id


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


def _new_suffix() -> str:
    return uuid4().hex[:8]


class ChartService:
    def __init__(
        self,
        gateway: ChartRunGateway,
        settings: Settings,
        now: Callable[[], datetime] = _utc_now,
        new_suffix: Callable[[], str] = _new_suffix,
    ) -> None:
        self._gateway = gateway
        self._settings = settings
        self._now = now
        self._new_suffix = new_suffix

    async def start(self, req: StartChartRequest) -> StartChartResult:
        date = self._now().date().isoformat()
        if req.wf_id:
            wf_id = req.wf_id
        elif req.unique_id:
            wf_id = f"chart-{date}-{self._new_suffix()}"
        else:
            wf_id = f"daily-chart-{date}"
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

    async def submit_decision(self, req: DecideChartRequest) -> None:
        handle = self._gateway.handle(req.wf_id)
        state = await handle.review_state()
        if state.status != "awaiting_decision":
            raise NotAwaitingDecision(req.wf_id, state.status)
        await handle.send_decision(ApprovalDecision(approved=req.approved, note=req.note))

    async def chart_image(self, ref: ChartRunRef) -> Path:
        state = await self._gateway.handle(ref.wf_id).review_state()
        if state.review is None:
            raise ChartImageNotFound(ref.wf_id)
        path = Path(state.review.png_path).resolve()
        output_dir = Path(
            self._settings.chart_output_dir or DailyChartInput.model_fields["output_dir"].default
        ).resolve()
        if not path.is_relative_to(output_dir) or not path.is_file():
            raise ChartImageNotFound(ref.wf_id)
        return path
