"""Start a run and show a minimal run page."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ai_trend_pipeline.config import Settings
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
from ai_trend_pipeline.web.dependencies import get_chart_service, get_settings, get_templates
from ai_trend_pipeline.web.view_models import HomeView, build_run_view

router = APIRouter()

MAX_TOPIC_LENGTH = 200


@router.post("/runs", response_model=None)
async def create_run(
    request: Request,
    topic: Annotated[str, Form()] = "",
    service: ChartService = Depends(get_chart_service),
    templates: Jinja2Templates = Depends(get_templates),
) -> RedirectResponse | HTMLResponse:
    topic = topic.strip()
    if not topic or len(topic) > MAX_TOPIC_LENGTH:
        view = HomeView(
            error=f"Error: enter a topic of 1 to {MAX_TOPIC_LENGTH} characters.", topic=topic
        )
        return templates.TemplateResponse(request, "home.html", {"view": view}, status_code=400)
    try:
        result = await service.start(StartChartRequest(topic=topic, unique_id=True))
    except RunAlreadyExists as exc:
        view = HomeView(error=f"Error: {exc}", topic=topic)
        return templates.TemplateResponse(request, "home.html", {"view": view}, status_code=409)
    return RedirectResponse(f"/runs/{result.wf_id}", status_code=303)


async def _render_run(
    request: Request,
    wf_id: str,
    service: ChartService,
    settings: Settings,
    templates: Jinja2Templates,
    template: str,
    sent: bool = False,
    error: str = "",
    status_code: int = 200,
) -> HTMLResponse:
    try:
        state = await service.show(ChartRunRef(wf_id=wf_id))
    except RunNotFound:
        return templates.TemplateResponse(request, "run_not_found.html", {}, status_code=404)
    view = build_run_view(wf_id, state, settings.temporal_ui_url, sent=sent, error=error)
    return templates.TemplateResponse(request, template, {"view": view}, status_code=status_code)


@router.get("/runs/{wf_id}", response_class=HTMLResponse)
async def show_run(
    request: Request,
    wf_id: str,
    sent: str = "",
    service: ChartService = Depends(get_chart_service),
    settings: Settings = Depends(get_settings),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    return await _render_run(
        request, wf_id, service, settings, templates, "run.html", sent=sent == "1"
    )


@router.get("/runs/{wf_id}/status", response_class=HTMLResponse)
async def run_status(
    request: Request,
    wf_id: str,
    sent: str = "",
    service: ChartService = Depends(get_chart_service),
    settings: Settings = Depends(get_settings),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    return await _render_run(
        request, wf_id, service, settings, templates, "partials/run_status.html", sent=sent == "1"
    )


@router.get("/runs/{wf_id}/chart.png", response_model=None)
async def run_chart(wf_id: str, service: ChartService = Depends(get_chart_service)) -> FileResponse:
    try:
        path = await service.chart_image(ChartRunRef(wf_id=wf_id))
    except (ChartImageNotFound, RunNotFound):
        raise HTTPException(status_code=404, detail="chart not found") from None
    return FileResponse(path, media_type="image/png")


@router.post("/runs/{wf_id}/decision", response_model=None)
async def decide_run(
    request: Request,
    wf_id: str,
    approved: Annotated[str, Form()] = "",
    service: ChartService = Depends(get_chart_service),
    settings: Settings = Depends(get_settings),
    templates: Jinja2Templates = Depends(get_templates),
) -> RedirectResponse | HTMLResponse:
    if approved not in ("true", "false"):
        raise HTTPException(status_code=400, detail="approved must be true or false")
    try:
        await service.submit_decision(DecideChartRequest(wf_id=wf_id, approved=approved == "true"))
    except RunNotFound:
        return templates.TemplateResponse(request, "run_not_found.html", {}, status_code=404)
    except NotAwaitingDecision as exc:
        return await _render_run(
            request,
            wf_id,
            service,
            settings,
            templates,
            "run.html",
            error=f"Error: {exc}",
            status_code=409,
        )
    return RedirectResponse(f"/runs/{wf_id}?sent=1", status_code=303)
