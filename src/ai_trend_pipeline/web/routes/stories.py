"""Unified story status page and its htmx results fragment."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ai_trend_pipeline.services.stories import (
    InvalidStoryPageToken,
    ListStoriesRequest,
    StoryFilter,
    StoryListPage,
    StoryStatusService,
    story_filter,
)
from ai_trend_pipeline.web.dependencies import get_story_status_service, get_templates
from ai_trend_pipeline.web.view_models import (
    INVALID_PAGE_NOTICE,
    build_story_list_view,
)

router = APIRouter()

FULL_TEMPLATE = "stories.html"
FRAGMENT_TEMPLATE = "partials/story_results.html"


async def _render_stories(
    request: Request,
    *,
    filter: str,
    page: str,
    service: StoryStatusService,
    templates: Jinja2Templates,
    fragment: bool,
) -> HTMLResponse:
    now = datetime.now(UTC)
    selected: StoryFilter = story_filter(filter)
    token = page.strip() or None
    template = FRAGMENT_TEMPLATE if fragment else FULL_TEMPLATE
    try:
        result = await service.list_stories(
            ListStoriesRequest(filter=selected, page_token=token), now=now
        )
    except InvalidStoryPageToken:
        view = build_story_list_view(
            StoryListPage(),
            now=now,
            filter=selected,
            page_token=token,
            notice=INVALID_PAGE_NOTICE,
        )
        return templates.TemplateResponse(
            request,
            template,
            {"view": view},
            status_code=200 if fragment else 400,
        )
    view = build_story_list_view(result, now=now, filter=selected, page_token=token)
    return templates.TemplateResponse(request, template, {"view": view})


@router.get("/stories", response_class=HTMLResponse)
async def stories_page(
    request: Request,
    filter: str = "",
    page: str = "",
    service: StoryStatusService = Depends(get_story_status_service),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    return await _render_stories(
        request,
        filter=filter,
        page=page,
        service=service,
        templates=templates,
        fragment=False,
    )


@router.get("/stories/rows", response_class=HTMLResponse)
async def story_rows(
    request: Request,
    filter: str = "",
    page: str = "",
    service: StoryStatusService = Depends(get_story_status_service),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    return await _render_stories(
        request,
        filter=filter,
        page=page,
        service=service,
        templates=templates,
        fragment=True,
    )
