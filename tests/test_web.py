"""Route tests for the web skeleton, using the FastAPI test client and no Temporal."""

from typing import Literal

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
from ai_trend_pipeline.web.app import create_app
from ai_trend_pipeline.web.dependencies import get_chart_service, get_settings

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
    def __init__(self, wf_id: str, state: ReviewState | None) -> None:
        self.id = wf_id
        self.state = state

    async def review_state(self) -> ReviewState:
        if self.state is None:
            raise RunNotFound(self.id)
        return self.state

    async def send_decision(self, decision: ApprovalDecision) -> None:
        return None

    async def result(self) -> DailyChartResult:
        raise NotImplementedError


class RecGateway:
    def __init__(self, state: ReviewState | None = None, exists: bool = False) -> None:
        self.state = state
        self.exists = exists
        self.started: list[tuple[str, DailyChartInput]] = []

    async def start(self, wf_id: str, data: DailyChartInput) -> None:
        if self.exists:
            raise RunAlreadyExists(wf_id)
        self.started.append((wf_id, data))

    def handle(self, wf_id: str) -> ChartRunHandle:
        return RecHandle(wf_id, self.state)


def client_for(gateway: RecGateway) -> TestClient:
    app = make_app()
    service = ChartService(gateway, Settings())

    async def fake_service() -> ChartService:
        return service

    app.dependency_overrides[get_chart_service] = fake_service
    return TestClient(app)


def test_home_page_has_form() -> None:
    res = client_for(RecGateway()).get("/")
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


def run_page(
    status: Literal["drafting", "awaiting_decision"], review: ReviewRequest | None
) -> tuple[int, str]:
    gateway = RecGateway(ReviewState(status=status, review=review))
    res = client_for(gateway).get("/runs/wf-1")
    return res.status_code, res.text


def test_run_page_awaiting_decision() -> None:
    code, text = run_page("awaiting_decision", REVIEW)
    assert code == 200
    assert "wf-1" in text
    assert "awaiting_decision" in text
    assert "CO2 emissions" in text


def test_run_page_drafting() -> None:
    code, text = run_page("drafting", None)
    assert code == 200
    assert "wf-1" in text
    assert "drafting" in text


def test_run_page_not_found() -> None:
    res = client_for(RecGateway(None)).get("/runs/nope")
    assert res.status_code == 404
    assert "not found" in res.text.lower()
