"""Unified story status page, story detail, htmx fragment and final decision routes."""

from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ai_trend_pipeline.services.stories import (
    DecideStoryRequest,
    InvalidStoryPageToken,
    ListStoriesRequest,
    StoryDetailExpired,
    StoryDetailMalformed,
    StoryDetailNotFound,
    StoryDetailService,
    StoryDetailUnavailable,
    StoryFilter,
    StoryListPage,
    StoryNotReviewable,
    StoryStatusService,
    story_filter,
)
from ai_trend_pipeline.web.dependencies import (
    CurrentUser,
    current_user,
    get_story_detail_service,
    get_story_status_service,
    get_templates,
)
from ai_trend_pipeline.web.view_models import (
    INVALID_DECISION_ERROR,
    INVALID_PAGE_NOTICE,
    STORY_DETAIL_STATUS_UNAVAILABLE_MESSAGE,
    STORY_DETAIL_STOPPED_MESSAGE,
    NotAvailableKind,
    build_story_detail_problem_view,
    build_story_detail_view,
    build_story_list_view,
    build_story_not_available_view,
    story_decision_notice,
)

router = APIRouter()

FULL_TEMPLATE = "stories.html"
FRAGMENT_TEMPLATE = "partials/story_results.html"
DETAIL_TEMPLATE = "story_detail.html"
DETAIL_FRAGMENT_TEMPLATE = "partials/story_detail_body.html"
NOT_AVAILABLE_TEMPLATE = "story_not_available.html"


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


def _not_available(
    request: Request,
    templates: Jinja2Templates,
    kind: NotAvailableKind,
    detail_id: str,
    status_code: int,
) -> HTMLResponse:
    view = build_story_not_available_view(kind, detail_id=detail_id)
    return templates.TemplateResponse(
        request, NOT_AVAILABLE_TEMPLATE, {"view": view}, status_code=status_code
    )


async def _render_detail(
    request: Request,
    detail_id: str,
    service: StoryDetailService,
    templates: Jinja2Templates,
    *,
    notice: str = "",
    error: str = "",
    status_code: int = 200,
) -> HTMLResponse:
    now = datetime.now(UTC)
    try:
        detail = await service.show(detail_id, now=now)
    except StoryDetailNotFound:
        return _not_available(request, templates, "missing", detail_id, 404)
    except StoryDetailExpired:
        return _not_available(request, templates, "expired", detail_id, 410)
    except StoryDetailMalformed:
        return _not_available(request, templates, "malformed", detail_id, 502)
    except StoryDetailUnavailable:
        return _not_available(request, templates, "unavailable", detail_id, 503)
    view = build_story_detail_view(detail, now=now, notice=notice, error=error)
    return templates.TemplateResponse(
        request, DETAIL_TEMPLATE, {"view": view}, status_code=status_code
    )


@router.get("/stories/{detail_id}", response_class=HTMLResponse)
async def story_detail(
    request: Request,
    detail_id: str,
    decided: str = "",
    service: StoryDetailService = Depends(get_story_detail_service),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    return await _render_detail(
        request,
        detail_id,
        service,
        templates,
        notice=story_decision_notice(decided),
    )


@router.get("/stories/{detail_id}/status", response_class=HTMLResponse)
async def story_detail_status(
    request: Request,
    detail_id: str,
    service: StoryDetailService = Depends(get_story_detail_service),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    now = datetime.now(UTC)
    try:
        detail = await service.show(detail_id, now=now)
    except StoryDetailUnavailable:
        view = build_story_detail_problem_view(
            detail_id, message=STORY_DETAIL_STATUS_UNAVAILABLE_MESSAGE
        )
    except (StoryDetailNotFound, StoryDetailExpired, StoryDetailMalformed):
        view = build_story_detail_problem_view(detail_id, message=STORY_DETAIL_STOPPED_MESSAGE)
    else:
        view = build_story_detail_view(detail, now=now, refresh_id=detail_id)
    return templates.TemplateResponse(
        request, DETAIL_FRAGMENT_TEMPLATE, {"view": view}, status_code=200
    )


@router.post("/stories/{detail_id}/decide", response_model=None)
async def decide_story(
    request: Request,
    detail_id: str,
    decision: Annotated[str, Form()] = "",
    user: CurrentUser = Depends(current_user),
    service: StoryDetailService = Depends(get_story_detail_service),
    templates: Jinja2Templates = Depends(get_templates),
) -> RedirectResponse | HTMLResponse:
    if decision not in ("approve", "reject"):
        return await _render_detail(
            request,
            detail_id,
            service,
            templates,
            error=INVALID_DECISION_ERROR,
            status_code=400,
        )
    kind: Literal["approve", "reject"] = "approve" if decision == "approve" else "reject"
    redirect = f"/stories/{quote(detail_id, safe='')}"
    try:
        result = await service.decide(
            DecideStoryRequest(detail_id=detail_id, decision=kind, decided_by=user.email),
            now=datetime.now(UTC),
        )
    except StoryNotReviewable:
        return RedirectResponse(f"{redirect}?decided=closed#story-notice", status_code=303)
    except StoryDetailNotFound:
        return _not_available(request, templates, "missing", detail_id, 404)
    except StoryDetailExpired:
        return _not_available(request, templates, "expired", detail_id, 410)
    except StoryDetailMalformed:
        return _not_available(request, templates, "malformed", detail_id, 502)
    except StoryDetailUnavailable:
        return _not_available(request, templates, "unavailable", detail_id, 503)
    token = "closed"
    if result.status == "ready" and kind == "approve":
        token = "approved"
    elif result.status == "rejected" and kind == "reject":
        token = "rejected"
    return RedirectResponse(f"{redirect}?decided={token}#story-notice", status_code=303)
