"""Flow steps 1 (topic) and 2 (style options)."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ai_trend_pipeline.services.styles import StyleService, StyleStoreUnavailable
from ai_trend_pipeline.style import ChartStyle
from ai_trend_pipeline.web.dependencies import (
    CurrentUser,
    get_style_service,
    get_templates,
    optional_user,
)
from ai_trend_pipeline.web.view_models import (
    MAX_TOPIC_LENGTH,
    STYLE_NOTICE_SIGN_IN,
    STYLE_NOTICE_UNAVAILABLE,
    TOPIC_TOO_LONG_ERROR,
    OptionsFormValues,
    build_home_view,
    build_style_step_view,
)

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    topic: str = "",
    chart_type: str = "",
    time_preset: str = "",
    from_year: str = "",
    to_year: str = "",
    countries: str = "",
    tone: str = "",
    instructions: str = "",
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    options = OptionsFormValues(
        chart_type=chart_type,
        time_preset=time_preset,
        from_year=from_year,
        to_year=to_year,
        countries=countries,
        tone=tone,
        instructions=instructions,
    )
    return templates.TemplateResponse(
        request, "home.html", {"view": build_home_view(topic, options)}
    )


@router.get("/new/style", response_model=None)
async def new_style(
    request: Request,
    topic: str | None = None,
    chart_type: str = "",
    time_preset: str = "",
    from_year: str = "",
    to_year: str = "",
    countries: str = "",
    tone: str = "",
    instructions: str = "",
    user: CurrentUser | None = Depends(optional_user),
    service: StyleService = Depends(get_style_service),
    templates: Jinja2Templates = Depends(get_templates),
) -> RedirectResponse | HTMLResponse:
    options = OptionsFormValues(
        chart_type=chart_type,
        time_preset=time_preset,
        from_year=from_year,
        to_year=to_year,
        countries=countries,
        tone=tone,
        instructions=instructions,
    )
    clean = (topic or "").strip()
    if not clean:
        return RedirectResponse("/", status_code=303)
    if len(clean) > MAX_TOPIC_LENGTH:
        view = build_home_view(clean, options, TOPIC_TOO_LONG_ERROR)
        return templates.TemplateResponse(request, "home.html", {"view": view}, status_code=400)
    style: ChartStyle | None = None
    notice = ""
    if user is None:
        notice = STYLE_NOTICE_SIGN_IN
    else:
        try:
            style = await service.get(user.email)
        except StyleStoreUnavailable:
            notice = STYLE_NOTICE_UNAVAILABLE
    view = build_style_step_view(clean, options, style, notice)
    return templates.TemplateResponse(request, "new_style.html", {"view": view})
