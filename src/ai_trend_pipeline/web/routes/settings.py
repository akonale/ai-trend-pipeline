"""Per-person style settings page."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from ai_trend_pipeline.services.styles import StyleService
from ai_trend_pipeline.style import ChartStyle, parse_hashtags
from ai_trend_pipeline.web.dependencies import (
    CurrentUser,
    current_user,
    get_style_service,
    get_templates,
)
from ai_trend_pipeline.web.view_models import build_settings_view, style_error_message

router = APIRouter()


@router.get("/settings", response_class=HTMLResponse)
async def show_settings(
    request: Request,
    saved: str = "",
    user: CurrentUser = Depends(current_user),
    service: StyleService = Depends(get_style_service),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    style = await service.get(user.email)
    view = build_settings_view(user.email, style, saved=saved == "1")
    return templates.TemplateResponse(request, "settings.html", {"view": view})


@router.post("/settings", response_model=None)
async def save_settings(
    request: Request,
    palette: Annotated[str, Form()] = "",
    font: Annotated[str, Form()] = "",
    tone: Annotated[str, Form()] = "",
    hashtags: Annotated[str, Form()] = "",
    signature: Annotated[str, Form()] = "",
    user: CurrentUser = Depends(current_user),
    service: StyleService = Depends(get_style_service),
    templates: Jinja2Templates = Depends(get_templates),
) -> RedirectResponse | HTMLResponse:
    try:
        style = ChartStyle(
            palette=palette,
            font=font,
            tone=tone,
            hashtags=parse_hashtags(hashtags),
            signature=signature,
        )
    except ValidationError as exc:
        shown = ChartStyle.model_construct(
            palette=palette, font=font, tone=tone, hashtags=[], signature=signature
        )
        view = build_settings_view(
            user.email, shown, error=style_error_message(exc), hashtags_text=hashtags
        )
        return templates.TemplateResponse(request, "settings.html", {"view": view}, status_code=400)
    await service.save(user.email, style)
    return RedirectResponse("/settings?saved=1", status_code=303)
