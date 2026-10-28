"""Tests for ChartService with a fake gateway and no Temporal environment."""

from datetime import UTC, datetime
from typing import Literal

import pytest

from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.critique import ChartCritique
from ai_trend_pipeline.daily_chart import (
    ApprovalDecision,
    DailyChartInput,
    DailyChartResult,
    ReviewRequest,
    ReviewState,
)
from ai_trend_pipeline.services.charts import (
    ChartRunRef,
    ChartService,
    DecideChartRequest,
    NotAwaitingDecision,
    RunAlreadyExists,
    RunNotFound,
    StartChartRequest,
)

Status = Literal["drafting", "awaiting_decision", "approved", "rejected", "expired"]
NOW = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


REVIEW = ReviewRequest(
    topic="T",
    topic_reason="r",
    caption="c",
    png_path="p.png",
    spec_sha256="0" * 64,
    critique=ChartCritique(passed=True),
    attempts=1,
)


class FakeHandle:
    def __init__(self, wf_id: str, status: Status, missing: bool = False) -> None:
        self.id = wf_id
        self.status: Status = status
        self.missing = missing
        self.decisions: list[ApprovalDecision] = []

    async def review_state(self) -> ReviewState:
        if self.missing:
            raise RunNotFound(self.id)
        return ReviewState(status=self.status, review=REVIEW)

    async def send_decision(self, decision: ApprovalDecision) -> None:
        self.decisions.append(decision)

    async def result(self) -> DailyChartResult:
        return DailyChartResult(status="approved", review=REVIEW, note=self.decisions[-1].note)


class FakeGateway:
    def __init__(self, status: Status = "awaiting_decision") -> None:
        self.status: Status = status
        self.started: list[tuple[str, DailyChartInput]] = []
        self.handles: dict[str, FakeHandle] = {}
        self.exists = False

    async def start(self, wf_id: str, data: DailyChartInput) -> None:
        if self.exists:
            raise RunAlreadyExists(wf_id)
        self.started.append((wf_id, data))
        self.handles[wf_id] = FakeHandle(wf_id, self.status)

    def handle(self, wf_id: str) -> FakeHandle:
        return self.handles.get(wf_id) or FakeHandle(wf_id, self.status, missing=True)


def make_service(gateway: FakeGateway, output_dir: str | None = None) -> ChartService:
    return ChartService(gateway, Settings(chart_output_dir=output_dir), now=lambda: NOW)


@pytest.mark.asyncio
async def test_start_default_id_from_clock() -> None:
    gw = FakeGateway()
    result = await make_service(gw).start(StartChartRequest())
    assert result.wf_id == "daily-chart-2026-05-01"
    assert gw.started[0][0] == "daily-chart-2026-05-01"


@pytest.mark.asyncio
async def test_start_passes_topic_and_output_dir() -> None:
    gw = FakeGateway()
    await make_service(gw).start(StartChartRequest(wf_id="a", topic="T"))
    assert gw.started[0][1] == DailyChartInput(topic="T")
    await make_service(gw, "custom").start(StartChartRequest(wf_id="b"))
    assert gw.started[1][1] == DailyChartInput(output_dir="custom", topic=None)


@pytest.mark.asyncio
async def test_start_propagates_already_exists() -> None:
    gw = FakeGateway()
    gw.exists = True
    with pytest.raises(RunAlreadyExists) as exc:
        await make_service(gw).start(StartChartRequest(wf_id="x"))
    assert exc.value.wf_id == "x"


@pytest.mark.asyncio
async def test_show_returns_state_and_unknown_raises() -> None:
    gw = FakeGateway()
    service = make_service(gw)
    await service.start(StartChartRequest(wf_id="a"))
    state = await service.show(ChartRunRef(wf_id="a"))
    assert state.status == "awaiting_decision"
    with pytest.raises(RunNotFound):
        await service.show(ChartRunRef(wf_id="nope"))


@pytest.mark.asyncio
async def test_decide_while_awaiting() -> None:
    gw = FakeGateway()
    service = make_service(gw)
    await service.start(StartChartRequest(wf_id="a"))
    result = await service.decide(DecideChartRequest(wf_id="a", approved=True, note="ok"))
    assert result.status == "approved"
    assert gw.handles["a"].decisions == [ApprovalDecision(approved=True, note="ok")]


@pytest.mark.asyncio
async def test_decide_not_awaiting_raises_without_signal() -> None:
    gw = FakeGateway(status="approved")
    service = make_service(gw)
    await service.start(StartChartRequest(wf_id="a"))
    with pytest.raises(NotAwaitingDecision) as exc:
        await service.decide(DecideChartRequest(wf_id="a", approved=True))
    assert exc.value.status == "approved"
    assert gw.handles["a"].decisions == []


@pytest.mark.asyncio
async def test_unique_id_uses_suffix() -> None:
    service = ChartService(
        FakeGateway(), Settings(), now=lambda: NOW, new_suffix=lambda: "abc12345"
    )
    res = await service.start(StartChartRequest(topic="x", unique_id=True))
    assert res.wf_id == "chart-2026-05-01-abc12345"


@pytest.mark.asyncio
async def test_unique_ids_differ() -> None:
    counter = iter(range(10))
    service = ChartService(
        FakeGateway(), Settings(), now=lambda: NOW, new_suffix=lambda: f"s{next(counter)}"
    )
    a = await service.start(StartChartRequest(topic="x", unique_id=True))
    b = await service.start(StartChartRequest(topic="x", unique_id=True))
    assert a.wf_id != b.wf_id


@pytest.mark.asyncio
async def test_unique_id_keeps_explicit_wf_id() -> None:
    service = ChartService(FakeGateway(), Settings(), now=lambda: NOW, new_suffix=lambda: "zzz")
    res = await service.start(StartChartRequest(wf_id="mine", unique_id=True))
    assert res.wf_id == "mine"
