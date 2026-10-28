"""Home page."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ai_trend_pipeline.web.dependencies import get_templates
from ai_trend_pipeline.web.view_models import HomeView

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def home(
    request: Request, templates: Jinja2Templates = Depends(get_templates)
) -> HTMLResponse:
    return templates.TemplateResponse(request, "home.html", {"view": HomeView()})
