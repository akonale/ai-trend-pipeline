"""Route tests for the web skeleton, using the FastAPI test client and no Temporal."""

from datetime import datetime
from pathlib import Path
from typing import Literal

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

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
    ChartRunGateway,
    ChartRunHandle,
    ChartService,
    RunAlreadyExists,
    RunNotFound,
)
from ai_trend_pipeline.services.styles import StyleStoreUnavailable
from ai_trend_pipeline.web import dependencies
from ai_trend_pipeline.web.app import create_app
from ai_trend_pipeline.web.dependencies import get_chart_service, get_settings, get_style_store
from ai_trend_pipeline.web.view_models import RunView, build_run_view
from tests.test_web_settings import BrokenStore

ADDRESS = "temporal.example:7233"


def make_app(address: str = ADDRESS) -> FastAPI:
    return create_app(Settings(temporal_address=address))


def test_create_app_stores_settings() -> None:
    settings = Settings(temporal_address=ADDRESS)
    app = create_app(settings)
    assert isinstance(app, FastAPI)
    assert app.state.settings is settings  # pyright: ignore[reportAny]


def test_health_page() -> None:
    res = TestClient(make_app()).get("/health")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    assert "ok" in res.text
    assert ADDRESS in res.text


def test_health_uses_base_template() -> None:
    text = TestClient(make_app()).get("/health").text
    assert "/static/htmx.min.js" in text
    assert "/static/app.css" in text


def test_static_files_served() -> None:
    client = TestClient(make_app())
    assert client.get("/static/htmx.min.js").status_code == 200
    assert client.get("/static/app.css").status_code == 200


def test_settings_override_changes_health() -> None:
    app = make_app()
    app.dependency_overrides[get_settings] = lambda: Settings(temporal_address="other.host:1")
    text = TestClient(app).get("/health").text
    assert "other.host:1" in text
    assert ADDRESS not in text


def test_health_never_touches_chart_service() -> None:
    app = make_app()

    async def boom() -> ChartService:
        raise RuntimeError("temporal down")

    app.dependency_overrides[get_chart_service] = boom
    assert TestClient(app).get("/health").status_code == 200


class FakeGateway:
    async def count_started_since(self, requested_by: str, since: datetime) -> int:
        return 0

    async def start(self, wf_id: str, data: DailyChartInput) -> None:
        return None

    def handle(self, wf_id: str) -> ChartRunHandle:
        raise NotImplementedError


def test_chart_service_override_is_injected() -> None:
    app = make_app()
    gateway: ChartRunGateway = FakeGateway()
    fake = ChartService(gateway, Settings())

    async def fake_service() -> ChartService:
        return fake

    app.dependency_overrides[get_chart_service] = fake_service

    @app.get("/_probe")
    async def probe(service: ChartService = Depends(get_chart_service)) -> dict[str, bool]:  # pyright: ignore[reportUnusedFunction]
        return {"same": service is fake}

    assert TestClient(app).get("/_probe").json() == {"same": True}


REVIEW = ReviewRequest(
    topic="CO2 emissions",
    topic_reason="r",
    caption="c",
    png_path="p.png",
    spec_sha256="0" * 64,
    critique=ChartCritique(passed=True),
    attempts=1,
)


class RecHandle:
    def __init__(
        self, wf_id: str, state: ReviewState | None, decisions: list[ApprovalDecision]
    ) -> None:
        self.id = wf_id
        self.state = state
        self.decisions = decisions

    async def review_state(self) -> ReviewState:
        if self.state is None:
            raise RunNotFound(self.id)
        return self.state

    async def send_decision(self, decision: ApprovalDecision) -> None:
        self.decisions.append(decision)

    async def result(self) -> DailyChartResult:
        raise NotImplementedError


class RecGateway:
    def __init__(self, state: ReviewState | None = None, exists: bool = False) -> None:
        self.state = state
        self.exists = exists
        self.started: list[tuple[str, DailyChartInput]] = []
        self.decisions: list[ApprovalDecision] = []
        self.count = 0
        self.count_calls = 0

    async def count_started_since(self, requested_by: str, since: datetime) -> int:
        self.count_calls += 1
        return self.count

    async def start(self, wf_id: str, data: DailyChartInput) -> None:
        if self.exists:
            raise RunAlreadyExists(wf_id)
        self.started.append((wf_id, data))

    def handle(self, wf_id: str) -> ChartRunHandle:
        return RecHandle(wf_id, self.state, self.decisions)


def client_for(gateway: RecGateway, output_dir: str | None = None) -> TestClient:
    app = make_app()
    service = ChartService(gateway, Settings(chart_output_dir=output_dir))

    async def fake_service() -> ChartService:
        return service

    app.dependency_overrides[get_chart_service] = fake_service
    return TestClient(app)


def test_home_page_has_form() -> None:
    res = client_for(RecGateway()).get("/new/style?topic=abc")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    assert 'action="/runs"' in res.text
    assert 'method="post"' in res.text
    assert 'name="topic"' in res.text
    assert "Generate" in res.text


def test_post_runs_redirects_and_starts() -> None:
    gateway = RecGateway()
    res = client_for(gateway).post(
        "/runs", data={"topic": "  CO2 emissions "}, follow_redirects=False
    )
    assert res.status_code == 303
    assert len(gateway.started) == 1
    wf_id, data = gateway.started[0]
    assert data.topic == "CO2 emissions"
    assert res.headers["location"] == f"/runs/{wf_id}"


def test_post_runs_blank_topic_is_400() -> None:
    gateway = RecGateway()
    res = client_for(gateway).post("/runs", data={"topic": "   "}, follow_redirects=False)
    assert res.status_code == 400
    assert "error" in res.text.lower()
    assert gateway.started == []


def test_post_runs_too_long_topic_is_400() -> None:
    gateway = RecGateway()
    res = client_for(gateway).post("/runs", data={"topic": "x" * 201}, follow_redirects=False)
    assert res.status_code == 400
    assert gateway.started == []


def test_post_runs_already_exists_is_409() -> None:
    res = client_for(RecGateway(exists=True)).post("/runs", data={"topic": "abc"})
    assert res.status_code == 409
    assert "already exists" in res.text


Status = Literal["drafting", "awaiting_decision", "posting", "approved", "rejected", "expired"]


def run_page(status: Status, review: ReviewRequest | None) -> tuple[int, str]:
    gateway = RecGateway(ReviewState(status=status, review=review))
    res = client_for(gateway).get("/runs/wf-1")
    return res.status_code, res.text


def test_run_page_awaiting_decision() -> None:
    code, text = run_page("awaiting_decision", REVIEW)
    assert code == 200
    assert "wf-1" in text
    assert "Waiting for your decision" in text
    assert "CO2 emissions" in text


def test_run_page_drafting() -> None:
    code, text = run_page("drafting", None)
    assert code == 200
    assert "wf-1" in text
    assert "Drafting the chart" in text


def test_run_page_not_found() -> None:
    res = client_for(RecGateway(None)).get("/runs/nope")
    assert res.status_code == 404
    assert "not found" in res.text.lower()


REVIEW_FAILED = REVIEW.model_copy(
    update={
        "caption": "Emissions rose",
        "critique": ChartCritique(passed=False, reasons=["axis unlabeled"]),
    }
)
POLL = 'hx-trigger="every 2s"'


def test_run_page_drafting_polls_without_buttons() -> None:
    _, text = run_page("drafting", None)
    assert 'hx-get="/runs/wf-1/status"' in text
    assert POLL in text
    assert 'hx-swap="outerHTML"' in text
    assert "<button" not in text


def test_run_page_awaiting_shows_review() -> None:
    _, text = run_page("awaiting_decision", REVIEW_FAILED)
    assert 'src="/runs/wf-1/chart.png"' in text
    assert "Emissions rose" in text
    assert "axis unlabeled" in text
    assert 'action="/runs/wf-1/decision"' in text
    assert "Approve" in text
    assert "Reject" in text
    assert "http://localhost:8233/namespaces/default/workflows/wf-1" in text
    assert 'target="_blank"' in text
    assert 'rel="noopener"' in text
    assert POLL not in text


def test_run_page_uses_temporal_ui_url_setting() -> None:
    app = create_app(Settings(temporal_ui_url="http://ui.example:9"))
    service = ChartService(
        RecGateway(ReviewState(status="awaiting_decision", review=REVIEW)), Settings()
    )

    async def fake_service() -> ChartService:
        return service

    app.dependency_overrides[get_chart_service] = fake_service
    text = TestClient(app).get("/runs/wf-1").text
    assert "http://ui.example:9/namespaces/default/workflows/wf-1" in text


def test_run_page_terminal_states() -> None:
    for status in ("approved", "rejected", "expired"):
        _, text = run_page(status, REVIEW)
        assert status in text.lower()
        assert POLL not in text
        assert "<button" not in text
        assert 'action="/runs/wf-1/decision"' not in text


def test_run_page_posting_polls() -> None:
    _, text = run_page("posting", REVIEW)
    assert POLL in text
    assert "<button" not in text


def test_status_fragment_only() -> None:
    gateway = RecGateway(ReviewState(status="drafting"))
    res = client_for(gateway).get("/runs/wf-1/status")
    assert res.status_code == 200
    assert "<html" not in res.text
    assert POLL in res.text


def test_status_fragment_keeps_sent_in_poll_url() -> None:
    gateway = RecGateway(ReviewState(status="awaiting_decision", review=REVIEW))
    text = client_for(gateway).get("/runs/wf-1/status?sent=1").text
    assert 'hx-get="/runs/wf-1/status?sent=1"' in text
    assert "<button" not in text


def test_run_page_sent_hides_buttons_and_polls() -> None:
    gateway = RecGateway(ReviewState(status="awaiting_decision", review=REVIEW))
    text = client_for(gateway).get("/runs/wf-1?sent=1").text
    assert "<button" not in text
    assert POLL in text


def test_chart_png_served(tmp_path: Path) -> None:
    png = tmp_path / "c.png"
    png.write_bytes(b"\x89PNGdata")
    review = REVIEW.model_copy(update={"png_path": str(png)})
    gateway = RecGateway(ReviewState(status="awaiting_decision", review=review))
    res = client_for(gateway, str(tmp_path)).get("/runs/wf-1/chart.png")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/png"
    assert res.content == b"\x89PNGdata"


def test_chart_png_missing_is_404(tmp_path: Path) -> None:
    review = REVIEW.model_copy(update={"png_path": str(tmp_path / "gone.png")})
    gateway = RecGateway(ReviewState(status="awaiting_decision", review=review))
    assert client_for(gateway, str(tmp_path)).get("/runs/wf-1/chart.png").status_code == 404


def test_decision_approve_redirects_and_signals() -> None:
    gateway = RecGateway(ReviewState(status="awaiting_decision", review=REVIEW))
    res = client_for(gateway).post(
        "/runs/wf-1/decision", data={"approved": "true"}, follow_redirects=False
    )
    assert res.status_code == 303
    assert res.headers["location"] == "/runs/wf-1?sent=1"
    assert [d.approved for d in gateway.decisions] == [True]


def test_decision_reject_signals() -> None:
    gateway = RecGateway(ReviewState(status="awaiting_decision", review=REVIEW))
    res = client_for(gateway).post(
        "/runs/wf-1/decision", data={"approved": "false"}, follow_redirects=False
    )
    assert res.status_code == 303
    assert [d.approved for d in gateway.decisions] == [False]


def test_decision_invalid_value_is_400() -> None:
    gateway = RecGateway(ReviewState(status="awaiting_decision", review=REVIEW))
    res = client_for(gateway).post("/runs/wf-1/decision", data={"approved": "maybe"})
    assert res.status_code == 400
    assert gateway.decisions == []


def test_decision_not_awaiting_is_409_with_error() -> None:
    gateway = RecGateway(ReviewState(status="approved", review=REVIEW))
    res = client_for(gateway).post("/runs/wf-1/decision", data={"approved": "true"})
    assert res.status_code == 409
    assert "Error" in res.text
    assert gateway.decisions == []


def test_every_run_route_404_when_not_found() -> None:
    client = client_for(RecGateway(None))
    assert client.get("/runs/x").status_code == 404
    assert client.get("/runs/x/status").status_code == 404
    assert client.get("/runs/x/chart.png").status_code == 404
    assert client.post("/runs/x/decision", data={"approved": "true"}).status_code == 404


def view(status: Status, sent: bool = False, with_review: bool = True) -> RunView:
    state = ReviewState(status=status, review=REVIEW if with_review else None)
    return build_run_view("wf-1", state, "http://t", sent=sent)


def test_build_run_view_flags() -> None:
    cases: list[tuple[Status, bool, bool, bool]] = [
        # status, sent, can_decide, polling
        ("drafting", False, False, True),
        ("awaiting_decision", False, True, False),
        ("awaiting_decision", True, False, True),
        ("posting", False, False, True),
        ("approved", False, False, False),
        ("rejected", False, False, False),
        ("expired", False, False, False),
    ]
    for status, sent, can_decide, polling in cases:
        v = view(status, sent)
        assert (v.can_decide, v.polling) == (can_decide, polling), (status, sent)


def test_build_run_view_image_and_label() -> None:
    assert view("awaiting_decision").image_url == "/runs/wf-1/chart.png"
    assert view("drafting", with_review=False).image_url is None
    assert view("drafting", with_review=False).status_label


def test_build_run_view_temporal_url_quotes_id() -> None:
    state = ReviewState(status="drafting")
    v = build_run_view("a b/c", state, "http://t:1")
    assert v.temporal_url == "http://t:1/namespaces/default/workflows/a%20b%2Fc"


CF_HEADER = "Cf-Access-Authenticated-User-Email"


def auth_client(
    gateway: RecGateway, auth_mode: Literal["local", "cloudflare"] = "local"
) -> TestClient:
    settings = Settings(auth_mode=auth_mode)
    app = create_app(settings)
    service = ChartService(gateway, settings)

    async def fake_service() -> ChartService:
        return service

    app.dependency_overrides[get_chart_service] = fake_service
    return TestClient(app)


def test_local_mode_uses_dev_user() -> None:
    gateway = RecGateway()
    auth_client(gateway).post("/runs", data={"topic": "abc"}, follow_redirects=False)
    assert gateway.started[0][1].requested_by == "dev@localhost"


def test_local_mode_ignores_header() -> None:
    gateway = RecGateway()
    auth_client(gateway).post(
        "/runs", data={"topic": "abc"}, headers={CF_HEADER: "x@example.com"}, follow_redirects=False
    )
    assert gateway.started[0][1].requested_by == "dev@localhost"


def test_cloudflare_mode_normalizes_header() -> None:
    gateway = RecGateway()
    auth_client(gateway, "cloudflare").post(
        "/runs",
        data={"topic": "abc"},
        headers={CF_HEADER: " Friend@Example.com "},
        follow_redirects=False,
    )
    assert gateway.started[0][1].requested_by == "friend@example.com"


def test_cloudflare_mode_missing_or_blank_header_is_401() -> None:
    gateway = RecGateway()
    client = auth_client(gateway, "cloudflare")
    assert client.post("/runs", data={"topic": "abc"}).status_code == 401
    res = client.post("/runs", data={"topic": "abc"}, headers={CF_HEADER: "   "})
    assert res.status_code == 401
    assert gateway.started == []


def test_cloudflare_mode_decision_without_header_is_401() -> None:
    gateway = RecGateway(ReviewState(status="awaiting_decision", review=REVIEW))
    res = auth_client(gateway, "cloudflare").post("/runs/wf-1/decision", data={"approved": "true"})
    assert res.status_code == 401
    assert gateway.decisions == []


def test_decision_records_decided_by() -> None:
    gateway = RecGateway(ReviewState(status="awaiting_decision", review=REVIEW))
    auth_client(gateway, "cloudflare").post(
        "/runs/wf-1/decision",
        data={"approved": "true"},
        headers={CF_HEADER: "B@example.com"},
        follow_redirects=False,
    )
    assert [d.decided_by for d in gateway.decisions] == ["b@example.com"]


def test_get_run_not_gated_in_cloudflare_mode() -> None:
    gateway = RecGateway(ReviewState(status="drafting"))
    assert auth_client(gateway, "cloudflare").get("/runs/wf-1").status_code == 200


def test_run_page_shows_requester_and_approver() -> None:
    state = ReviewState(
        status="approved", review=REVIEW, requested_by="a@example.com", decided_by="b@example.com"
    )
    text = client_for(RecGateway(state)).get("/runs/wf-1").text
    assert "Requested by: a@example.com" in text
    assert "Approved by: b@example.com" in text


def test_run_page_shows_rejecter() -> None:
    state = ReviewState(
        status="rejected", review=REVIEW, requested_by="a@example.com", decided_by="b@example.com"
    )
    text = client_for(RecGateway(state)).get("/runs/wf-1").text
    assert "Rejected by: b@example.com" in text


def test_run_page_without_requester_has_no_line() -> None:
    _, text = run_page("drafting", None)
    assert "Requested by" not in text


def test_build_run_view_copies_users_and_label() -> None:
    state = ReviewState(
        status="posting", review=REVIEW, requested_by="a@example.com", decided_by="b@example.com"
    )
    v = build_run_view("wf-1", state, "http://t")
    assert (v.requested_by, v.decided_by, v.decided_label) == (
        "a@example.com",
        "b@example.com",
        "Approved",
    )
    state = state.model_copy(update={"status": "rejected"})
    assert build_run_view("wf-1", state, "http://t").decided_label == "Rejected"
    assert view("expired").decided_label == ""


def test_daily_cap_returns_429_and_keeps_topic() -> None:
    gateway = RecGateway()
    gateway.count = 10
    res = client_for(gateway).post("/runs", data={"topic": "my topic"})
    assert res.status_code == 429
    assert "reached today&#39;s limit" in res.text or "reached today's limit" in res.text
    assert "00:00 UTC" in res.text
    assert 'role="alert"' in res.text
    assert 'value="my topic"' in res.text or ">my topic<" in res.text
    assert gateway.started == []


def test_blank_topic_and_missing_header_do_not_count() -> None:
    gateway = RecGateway()
    assert client_for(gateway).post("/runs", data={"topic": " "}).status_code == 400
    res = auth_client(gateway, "cloudflare").post("/runs", data={"topic": "abc"})
    assert res.status_code == 401
    assert gateway.count_calls == 0


def lazy_store_client(
    monkeypatch: pytest.MonkeyPatch, gateway: RecGateway, store: object
) -> TestClient:
    """Real get_chart_service with a fake gateway and a store that fails on any use."""

    async def fake_connect(settings: Settings) -> object:
        return object()

    def make_gateway(client: object) -> RecGateway:
        return gateway

    monkeypatch.setattr(dependencies.temporal_client, "connect", fake_connect)
    monkeypatch.setattr(dependencies, "TemporalChartRunGateway", make_gateway)
    app = make_app()
    app.dependency_overrides[get_style_store] = lambda: store
    return TestClient(app)


def test_read_routes_never_touch_the_style_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    png = tmp_path / "c.png"
    png.write_bytes(b"png")
    review = ReviewRequest(
        topic="T",
        topic_reason="r",
        caption="c",
        png_path=str(png),
        spec_sha256="0" * 64,
        critique=ChartCritique(passed=True),
        attempts=1,
    )
    gateway = RecGateway(ReviewState(status="awaiting_decision", review=review))
    client = lazy_store_client(monkeypatch, gateway, BrokenStore())
    monkeypatch.chdir(tmp_path)
    assert client.get("/").status_code == 200
    assert client.get("/health").status_code == 200
    assert client.get("/runs/wf-1").status_code == 200
    assert client.get("/runs/wf-1/status").status_code == 200
    assert client.get("/runs/wf-1/chart.png").status_code in (200, 404)
    res = client.post("/runs/wf-1/decision", data={"approved": "true"}, follow_redirects=False)
    assert res.status_code == 303


def test_post_runs_style_lookup_failure_is_503_and_starts_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = RecGateway()
    client = lazy_store_client(monkeypatch, gateway, BrokenStore(StyleStoreUnavailable()))
    res = client.post("/runs", data={"topic": "Life expectancy"})
    assert res.status_code == 503
    assert "load your style settings right now. Please try again in a moment." in res.text
    assert 'role="alert"' in res.text
    assert 'value="Life expectancy"' in res.text
    assert gateway.started == []
