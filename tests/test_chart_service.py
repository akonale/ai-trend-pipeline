"""Tests for ChartService with a fake gateway and no Temporal environment."""

from datetime import UTC, datetime, timedelta, timezone
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
    DailyCapReached,
    DecideChartRequest,
    NotAwaitingDecision,
    RunAlreadyExists,
    RunNotFound,
    StartChartRequest,
)
from ai_trend_pipeline.style import ChartStyle
from tests.style_fakes import InMemoryStyleStore

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
        self.counts: dict[str, int] = {}
        self.count_calls: list[tuple[str, datetime]] = []

    async def count_started_since(self, requested_by: str, since: datetime) -> int:
        self.count_calls.append((requested_by, since))
        return self.counts.get(requested_by, 0)

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


def cap_service(gateway: FakeGateway, cap: int = 10, now: datetime = NOW) -> ChartService:
    return ChartService(gateway, Settings(daily_chart_cap=cap), now=lambda: now)


@pytest.mark.asyncio
async def test_under_cap_starts_and_passes_since() -> None:
    gw = FakeGateway()
    gw.counts["a@x.com"] = 9
    await cap_service(gw).start(StartChartRequest(unique_id=True, requested_by="a@x.com"))
    assert len(gw.started) == 1
    assert gw.count_calls == [("a@x.com", datetime(2026, 5, 1, tzinfo=UTC))]


@pytest.mark.asyncio
async def test_at_cap_raises_and_does_not_start() -> None:
    gw = FakeGateway()
    gw.counts["a@x.com"] = 10
    with pytest.raises(DailyCapReached) as info:
        await cap_service(gw).start(StartChartRequest(requested_by="a@x.com"))
    assert info.value.cap == 10
    assert str(info.value) == "You've reached today's limit of 10 charts. It resets at 00:00 UTC."
    assert gw.started == []


@pytest.mark.asyncio
async def test_other_person_unaffected() -> None:
    gw = FakeGateway()
    gw.counts["a@x.com"] = 10
    svc = cap_service(gw)
    with pytest.raises(DailyCapReached):
        await svc.start(StartChartRequest(requested_by="a@x.com"))
    await svc.start(StartChartRequest(requested_by="b@x.com", unique_id=True))
    assert len(gw.started) == 1


class CountingGateway(FakeGateway):
    def __init__(self) -> None:
        super().__init__()
        self.times: list[tuple[str, datetime]] = []
        self.clock = NOW

    async def count_started_since(self, requested_by: str, since: datetime) -> int:
        self.count_calls.append((requested_by, since))
        return sum(1 for who, t in self.times if who == requested_by and t >= since)

    async def start(self, wf_id: str, data: DailyChartInput) -> None:
        await super().start(wf_id, data)
        assert data.requested_by is not None
        self.times.append((data.requested_by, self.clock))


@pytest.mark.asyncio
async def test_day_rollover_resets_count() -> None:
    gw = CountingGateway()
    clock = {"now": datetime(2026, 5, 1, 23, 58, tzinfo=UTC)}
    svc = ChartService(gw, Settings(daily_chart_cap=2), now=lambda: clock["now"])
    for i in range(2):
        gw.clock = clock["now"] + timedelta(seconds=i)
        await svc.start(StartChartRequest(requested_by="a@x.com", wf_id=f"w{i}"))
    clock["now"] = datetime(2026, 5, 1, 23, 59, tzinfo=UTC)
    with pytest.raises(DailyCapReached):
        await svc.start(StartChartRequest(requested_by="a@x.com", wf_id="w2"))
    clock["now"] = datetime(2026, 5, 2, 0, 0, 1, tzinfo=UTC)
    gw.clock = clock["now"]
    await svc.start(StartChartRequest(requested_by="a@x.com", wf_id="w3"))
    assert gw.count_calls[-1][1] == datetime(2026, 5, 2, tzinfo=UTC)


@pytest.mark.asyncio
async def test_non_utc_now_uses_utc_midnight() -> None:
    gw = FakeGateway()
    now = datetime(2026, 5, 1, 1, 30, tzinfo=timezone(timedelta(hours=2)))
    await cap_service(gw, now=now).start(StartChartRequest(requested_by="a@x.com"))
    assert gw.count_calls[0][1] == datetime(2026, 4, 30, tzinfo=UTC)


@pytest.mark.asyncio
async def test_no_requested_by_is_never_counted() -> None:
    gw = FakeGateway()
    await cap_service(gw, cap=1).start(StartChartRequest())
    assert gw.count_calls == []
    assert len(gw.started) == 1


def _styled_service(gw: FakeGateway, store: InMemoryStyleStore | None) -> ChartService:
    return ChartService(gw, Settings(daily_chart_cap=2), now=lambda: NOW, styles=store)


@pytest.mark.asyncio
async def test_start_uses_requesters_saved_style() -> None:
    style = ChartStyle(palette="ocean", hashtags=["ai"])
    store = InMemoryStyleStore({"a@x.com": style})
    gw = FakeGateway()
    service = _styled_service(gw, store)
    await service.start(StartChartRequest(topic="t", unique_id=True, requested_by="a@x.com"))
    await service.start(StartChartRequest(topic="t", unique_id=True, requested_by="b@x.com"))
    assert gw.started[0][1].style == style
    assert gw.started[1][1].style is None


@pytest.mark.asyncio
async def test_store_not_called_without_requester_or_store() -> None:
    store = InMemoryStyleStore({"a@x.com": ChartStyle()})
    gw = FakeGateway()
    await _styled_service(gw, store).start(StartChartRequest(topic="t"))
    assert store.get_calls == []
    await _styled_service(gw, None).start(StartChartRequest(topic="t", requested_by="a@x.com"))
    assert gw.started[1][1].style is None


@pytest.mark.asyncio
async def test_store_not_called_when_cap_reached() -> None:
    store = InMemoryStyleStore({"a@x.com": ChartStyle()})
    gw = FakeGateway()
    gw.counts["a@x.com"] = 2
    with pytest.raises(DailyCapReached):
        await _styled_service(gw, store).start(StartChartRequest(topic="t", requested_by="a@x.com"))
    assert store.get_calls == []
