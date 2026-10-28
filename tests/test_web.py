"""Route tests for the web skeleton, using the FastAPI test client and no Temporal."""

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.daily_chart import DailyChartInput
from ai_trend_pipeline.services.charts import ChartRunGateway, ChartRunHandle, ChartService
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


def test_root_is_not_routed() -> None:
    assert TestClient(make_app()).get("/").status_code == 404


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
