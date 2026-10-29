"""Start a run and show a minimal run page."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.post_options import options_from_form
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
from ai_trend_pipeline.services.styles import StyleStoreUnavailable
from ai_trend_pipeline.web.dependencies import (
    CurrentUser,
    current_user,
    get_chart_service,
    get_settings,
    get_templates,
)
from ai_trend_pipeline.web.view_models import (
    MAX_TOPIC_LENGTH,
    STYLE_NOTICE_APPLIED,
    STYLE_UNAVAILABLE_ERROR,
    OptionsFormValues,
    build_home_view,
    build_run_view,
    build_style_step_view,
    options_error_message,
)

router = APIRouter()


@router.post("/runs", response_model=None)
async def create_run(
    request: Request,
    topic: Annotated[str, Form()] = "",
    chart_type: Annotated[str, Form()] = "",
    time_preset: Annotated[str, Form()] = "",
    from_year: Annotated[str, Form()] = "",
    to_year: Annotated[str, Form()] = "",
    countries: Annotated[str, Form()] = "",
    tone: Annotated[str, Form()] = "",
    instructions: Annotated[str, Form()] = "",
    user: CurrentUser = Depends(current_user),
    service: ChartService = Depends(get_chart_service),
    templates: Jinja2Templates = Depends(get_templates),
) -> RedirectResponse | HTMLResponse:
    topic = topic.strip()
    typed = OptionsFormValues(
        chart_type=chart_type,
        time_preset=time_preset,
        from_year=from_year,
        to_year=to_year,
        countries=countries,
        tone=tone,
        instructions=instructions,
    )

    def step_one(error: str, status_code: int) -> HTMLResponse:
        view = build_home_view(topic, typed, error)
        return templates.TemplateResponse(
            request, "home.html", {"view": view}, status_code=status_code
        )

    def step_two(error: str, status_code: int) -> HTMLResponse:
        view = build_style_step_view(topic, typed, notice=STYLE_NOTICE_APPLIED, error=error)
        return templates.TemplateResponse(
            request, "new_style.html", {"view": view}, status_code=status_code
        )

    if not topic or len(topic) > MAX_TOPIC_LENGTH:
        return step_one(f"Error: enter a topic of 1 to {MAX_TOPIC_LENGTH} characters.", 400)
    try:
        options = options_from_form(
            chart_type, time_preset, from_year, to_year, countries, tone, instructions
        )
    except ValidationError as exc:
        return step_two(options_error_message(exc), 400)
    try:
        result = await service.start(
            StartChartRequest(topic=topic, unique_id=True, requested_by=user.email, options=options)
        )
    except DailyCapReached as exc:
        return step_two(str(exc), 429)
    except RunAlreadyExists as exc:
        return step_two(f"Error: {exc}", 409)
    except StyleStoreUnavailable:
        return step_two(STYLE_UNAVAILABLE_ERROR, 503)
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
    fragment: bool = False,
) -> HTMLResponse:
    try:
        state = await service.show(ChartRunRef(wf_id=wf_id))
    except RunNotFound:
        return templates.TemplateResponse(request, "run_not_found.html", {}, status_code=404)
    view = build_run_view(
        wf_id, state, settings.temporal_ui_url, sent=sent, error=error, fragment=fragment
    )
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
        request,
        wf_id,
        service,
        settings,
        templates,
        "partials/run_status.html",
        sent=sent == "1",
        fragment=True,
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
    user: CurrentUser = Depends(current_user),
    service: ChartService = Depends(get_chart_service),
    settings: Settings = Depends(get_settings),
    templates: Jinja2Templates = Depends(get_templates),
) -> RedirectResponse | HTMLResponse:
    if approved not in ("true", "false"):
        raise HTTPException(status_code=400, detail="approved must be true or false")
    try:
        await service.submit_decision(
            DecideChartRequest(wf_id=wf_id, approved=approved == "true", decided_by=user.email)
        )
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
