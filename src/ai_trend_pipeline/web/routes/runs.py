"""Start a run and show a minimal run page."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ai_trend_pipeline.services.charts import (
    ChartRunRef,
    ChartService,
    RunAlreadyExists,
    RunNotFound,
    StartChartRequest,
)
from ai_trend_pipeline.web.dependencies import get_chart_service, get_templates
from ai_trend_pipeline.web.view_models import HomeView, RunSummaryView

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


@router.get("/runs/{wf_id}", response_class=HTMLResponse)
async def show_run(
    request: Request,
    wf_id: str,
    service: ChartService = Depends(get_chart_service),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    try:
        state = await service.show(ChartRunRef(wf_id=wf_id))
    except RunNotFound:
        return templates.TemplateResponse(request, "run_not_found.html", {}, status_code=404)
    view = RunSummaryView(
        wf_id=wf_id,
        status=state.status,
        topic=state.review.topic if state.review else None,
    )
    return templates.TemplateResponse(request, "run.html", {"view": view})
