"""Health page."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.web.dependencies import get_settings, get_templates
from ai_trend_pipeline.web.view_models import HealthView

router = APIRouter()


@router.get("/health", response_class=HTMLResponse)
async def health(
    request: Request,
    settings: Settings = Depends(get_settings),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    view = HealthView(status="ok", temporal_address=settings.temporal_address)
    return templates.TemplateResponse(request, "health.html", {"view": view})
