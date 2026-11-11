"""The global News filter page: choose how strictly new source events are classified."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ai_trend_pipeline.news_filter import NewsFilterStoreError, NewsFilterUnavailable
from ai_trend_pipeline.services.news_filter import NewsFilterService
from ai_trend_pipeline.story_classification import NewsPolicyMode
from ai_trend_pipeline.web.dependencies import (
    CurrentUser,
    current_user,
    get_news_filter_service,
    get_templates,
)
from ai_trend_pipeline.web.view_models import (
    NEWS_FILTER_INVALID_ERROR,
    NEWS_FILTER_INVALID_STORED_ERROR,
    NEWS_FILTER_UNAVAILABLE_ERROR,
    NewsFilterView,
    build_news_filter_view,
)

router = APIRouter()


def _page(
    request: Request, templates: Jinja2Templates, view: NewsFilterView, status_code: int = 200
) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "news_filter.html", {"view": view}, status_code=status_code
    )


def _failed(request: Request, templates: Jinja2Templates, exc: Exception) -> HTMLResponse:
    message = (
        NEWS_FILTER_INVALID_STORED_ERROR
        if isinstance(exc, NewsFilterStoreError)
        else NEWS_FILTER_UNAVAILABLE_ERROR
    )
    return _page(request, templates, build_news_filter_view(None, error=message), 503)


@router.get("/news-filter", response_class=HTMLResponse)
async def show_news_filter(
    request: Request,
    saved: str = "",
    user: CurrentUser = Depends(current_user),
    service: NewsFilterService = Depends(get_news_filter_service),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    _ = user
    try:
        settings = await service.get()
    except (NewsFilterUnavailable, NewsFilterStoreError) as exc:
        return _failed(request, templates, exc)
    return _page(request, templates, build_news_filter_view(settings, saved=saved == "1"))


@router.post("/news-filter", response_model=None)
async def save_news_filter(
    request: Request,
    mode: Annotated[str, Form()] = "",
    user: CurrentUser = Depends(current_user),
    service: NewsFilterService = Depends(get_news_filter_service),
    templates: Jinja2Templates = Depends(get_templates),
) -> RedirectResponse | HTMLResponse:
    try:
        chosen = NewsPolicyMode(mode)
    except ValueError:
        try:
            current = await service.get()
        except (NewsFilterUnavailable, NewsFilterStoreError) as exc:
            return _failed(request, templates, exc)
        view = build_news_filter_view(current, error=NEWS_FILTER_INVALID_ERROR)
        return _page(request, templates, view, 400)
    try:
        await service.update(chosen, updated_by=user.email)
    except (NewsFilterUnavailable, NewsFilterStoreError) as exc:
        return _failed(request, templates, exc)
    return RedirectResponse("/news-filter?saved=1", status_code=303)
