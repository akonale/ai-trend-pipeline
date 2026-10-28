"""Tests for ChartService with a fake gateway and no Temporal environment."""

from datetime import UTC, datetime
from pathlib import Path
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
    ChartImageNotFound,
    ChartRunRef,
    ChartService,
    DecideChartRequest,
    NotAwaitingDecision,
    RunAlreadyExists,
    RunNotFound,
    StartChartRequest,
)

Status = Literal["drafting", "awaiting_decision", "posting", "approved", "rejected", "expired"]
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
    def __init__(
        self,
        wf_id: str,
        status: Status,
        missing: bool = False,
        review: ReviewRequest | None = REVIEW,
    ) -> None:
        self.id = wf_id
        self.review = review
        self.result_calls = 0
        self.status: Status = status
        self.missing = missing
        self.decisions: list[ApprovalDecision] = []

    async def review_state(self) -> ReviewState:
        if self.missing:
            raise RunNotFound(self.id)
        return ReviewState(status=self.status, review=self.review)

    async def send_decision(self, decision: ApprovalDecision) -> None:
        self.decisions.append(decision)

    async def result(self) -> DailyChartResult:
        self.result_calls += 1
        return DailyChartResult(status="approved", review=REVIEW, note=self.decisions[-1].note)


class FakeGateway:
    def __init__(self, status: Status = "awaiting_decision") -> None:
        self.status: Status = status
        self.started: list[tuple[str, DailyChartInput]] = []
        self.handles: dict[str, FakeHandle] = {}
        self.exists = False
        self.review: ReviewRequest | None = REVIEW

    async def start(self, wf_id: str, data: DailyChartInput) -> None:
        if self.exists:
            raise RunAlreadyExists(wf_id)
        self.started.append((wf_id, data))
        self.handles[wf_id] = FakeHandle(wf_id, self.status, review=self.review)

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


@pytest.mark.asyncio
async def test_submit_decision_sends_one_signal_without_waiting() -> None:
    gw = FakeGateway()
    service = make_service(gw)
    await service.start(StartChartRequest(wf_id="a"))
    await service.submit_decision(DecideChartRequest(wf_id="a", approved=False, note="n"))
    assert gw.handles["a"].decisions == [ApprovalDecision(approved=False, note="n")]
    assert gw.handles["a"].result_calls == 0


@pytest.mark.asyncio
async def test_submit_decision_not_awaiting_raises_without_signal() -> None:
    gw = FakeGateway(status="posting")
    service = make_service(gw)
    await service.start(StartChartRequest(wf_id="a"))
    with pytest.raises(NotAwaitingDecision):
        await service.submit_decision(DecideChartRequest(wf_id="a", approved=True))
    assert gw.handles["a"].decisions == []


def review_with_path(path: str) -> ReviewRequest:
    return REVIEW.model_copy(update={"png_path": path})


@pytest.mark.asyncio
async def test_chart_image_returns_path_inside_output_dir(tmp_path: Path) -> None:
    png = tmp_path / "a.png"
    png.write_bytes(b"png")
    gw = FakeGateway()
    gw.review = review_with_path(str(png))
    service = make_service(gw, str(tmp_path))
    await service.start(StartChartRequest(wf_id="a"))
    assert await service.chart_image(ChartRunRef(wf_id="a")) == png.resolve()


@pytest.mark.asyncio
async def test_chart_image_not_found_cases(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"x")
    for review in (
        None,
        review_with_path(str(out / "missing.png")),
        review_with_path(str(outside)),
    ):
        gw = FakeGateway()
        gw.review = review
        service = make_service(gw, str(out))
        await service.start(StartChartRequest(wf_id="a"))
        with pytest.raises(ChartImageNotFound):
            await service.chart_image(ChartRunRef(wf_id="a"))


@pytest.mark.asyncio
async def test_start_puts_requested_by_into_input() -> None:
    gw = FakeGateway()
    await make_service(gw).start(StartChartRequest(wf_id="a", requested_by="a@example.com"))
    assert gw.started[0][1].requested_by == "a@example.com"


@pytest.mark.asyncio
async def test_decisions_carry_decided_by() -> None:
    gw = FakeGateway()
    service = make_service(gw)
    await service.start(StartChartRequest(wf_id="a"))
    await service.submit_decision(
        DecideChartRequest(wf_id="a", approved=True, decided_by="b@example.com")
    )
    await service.decide(DecideChartRequest(wf_id="a", approved=True, decided_by="c@example.com"))
    assert [d.decided_by for d in gw.handles["a"].decisions] == ["b@example.com", "c@example.com"]


@pytest.mark.asyncio
async def test_requests_without_user_give_none() -> None:
    gw = FakeGateway()
    service = make_service(gw)
    await service.start(StartChartRequest(wf_id="a"))
    await service.submit_decision(DecideChartRequest(wf_id="a", approved=True))
    assert gw.started[0][1].requested_by is None
    assert gw.handles["a"].decisions[0].decided_by is None
