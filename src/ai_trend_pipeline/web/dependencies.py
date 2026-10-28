"""FastAPI dependencies for settings, templates and the chart service."""

from fastapi import Depends, Request
from fastapi.templating import Jinja2Templates
from temporalio.client import Client

from ai_trend_pipeline import temporal_client
from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.services.charts import ChartService
from ai_trend_pipeline.services.temporal_gateway import TemporalChartRunGateway


def get_settings(request: Request) -> Settings:
    settings = request.app.state.settings  # pyright: ignore[reportAny]
    if not isinstance(settings, Settings):
        raise RuntimeError("app.state.settings is not set")
    return settings


def get_templates(request: Request) -> Jinja2Templates:
    templates = request.app.state.templates  # pyright: ignore[reportAny]
    if not isinstance(templates, Jinja2Templates):
        raise RuntimeError("app.state.templates is not set")
    return templates


async def get_chart_service(
    request: Request, settings: Settings = Depends(get_settings)
) -> ChartService:
    client = getattr(request.app.state, "temporal_client", None)
    if not isinstance(client, Client):
        client = await temporal_client.connect(settings)
        request.app.state.temporal_client = client
    return ChartService(TemporalChartRunGateway(client), settings)
