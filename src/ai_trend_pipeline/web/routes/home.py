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
    SAVE_DEFAULT_SIGN_IN,
    SAVE_DEFAULT_UNAVAILABLE,
    STYLE_NOTICE_SIGN_IN,
    STYLE_NOTICE_UNAVAILABLE,
    TOPIC_TOO_LONG_ERROR,
    OptionsFormValues,
    build_home_view,
    build_style_step_view,
)

router = APIRouter()


@router.get("/", response_class=RedirectResponse, status_code=307)
async def root() -> RedirectResponse:
    """The stories inbox is the home page."""
    return RedirectResponse("/stories", status_code=307)


@router.get("/new", response_class=HTMLResponse)
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
    palette: str = "",
    font: str = "",
    hashtags: str = "",
    signature: str = "",
    hashtags_none: str = "",
    signature_none: str = "",
    save_default: str = "",
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
        palette=palette,
        font=font,
        hashtags=hashtags,
        signature=signature,
        hashtags_none=hashtags_none,
        signature_none=signature_none,
        save_default=save_default,
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
    palette: str = "",
    font: str = "",
    hashtags: str = "",
    signature: str = "",
    hashtags_none: str = "",
    signature_none: str = "",
    save_default: str = "",
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
        palette=palette,
        font=font,
        hashtags=hashtags,
        signature=signature,
        hashtags_none=hashtags_none,
        signature_none=signature_none,
        save_default=save_default,
    )
    clean = (topic or "").strip()
    if not clean:
        return RedirectResponse("/new", status_code=303)
    if len(clean) > MAX_TOPIC_LENGTH:
        view = build_home_view(clean, options, TOPIC_TOO_LONG_ERROR)
        return templates.TemplateResponse(request, "home.html", {"view": view}, status_code=400)
    style: ChartStyle | None = None
    notice = ""
    save_note = ""
    if user is None:
        notice, save_note = STYLE_NOTICE_SIGN_IN, SAVE_DEFAULT_SIGN_IN
    else:
        try:
            style = await service.get(user.email)
        except StyleStoreUnavailable:
            notice, save_note = STYLE_NOTICE_UNAVAILABLE, SAVE_DEFAULT_UNAVAILABLE
    view = build_style_step_view(clean, options, style, notice, save_note=save_note)
    return templates.TemplateResponse(request, "new_style.html", {"view": view})
