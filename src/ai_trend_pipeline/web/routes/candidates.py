"""Story candidate inbox, detail and decision routes."""

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ai_trend_pipeline.services.candidates import (
    CandidateClosed,
    CandidateNotFound,
    CandidatePage,
    CandidateService,
    DecideCandidateRequest,
    InvalidPageToken,
    ListCandidatesRequest,
)
from ai_trend_pipeline.web.dependencies import (
    CurrentUser,
    build_request_shell,
    current_user,
    get_candidate_service,
    get_templates,
)
from ai_trend_pipeline.web.view_models import (
    INVALID_DECISION_ERROR,
    INVALID_PAGE_NOTICE,
    build_candidate_detail_view,
    build_candidate_inbox_view,
    build_candidate_not_available_view,
    decision_notice,
    pending_nav_badge,
)

router = APIRouter()


@router.get("/candidates", response_class=HTMLResponse)
async def candidate_inbox(
    request: Request,
    page: str = "",
    notice: str = "",
    service: CandidateService = Depends(get_candidate_service),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    now = datetime.now(UTC)
    token = page.strip() or None
    try:
        candidates = await service.list_pending(ListCandidatesRequest(page_token=token))
    except InvalidPageToken:
        view = build_candidate_inbox_view(
            CandidatePage(), now=now, page_token=token, notice=INVALID_PAGE_NOTICE
        )
        return templates.TemplateResponse(
            request, "candidates.html", {"view": view}, status_code=400
        )
    view = build_candidate_inbox_view(
        candidates, now=now, page_token=token, notice=decision_notice(notice)
    )
    pending = pending_nav_badge(candidates, first_page=token is None)
    # This Starlette applies context processors after the route context, so store the
    # badge for the processor as well; `build_request_shell` reads it back.
    request.state.candidate_pending = pending
    return templates.TemplateResponse(
        request,
        "candidates.html",
        {"view": view, "shell": build_request_shell(request, pending=pending)},
    )


async def _detail_or_not_available(
    request: Request,
    candidate_id: str,
    service: CandidateService,
    templates: Jinja2Templates,
    *,
    now: datetime,
    error: str = "",
    status_code: int = 200,
) -> HTMLResponse:
    try:
        state = await service.show(candidate_id)
    except CandidateNotFound:
        view = build_candidate_not_available_view(None)
        return templates.TemplateResponse(
            request, "candidate_not_available.html", {"view": view}, status_code=404
        )
    except CandidateClosed as exc:
        view = build_candidate_not_available_view(exc.status)
        return templates.TemplateResponse(
            request, "candidate_not_available.html", {"view": view}, status_code=410
        )
    view = build_candidate_detail_view(state, now=now, error=error)
    return templates.TemplateResponse(
        request, "candidate_detail.html", {"view": view}, status_code=status_code
    )


@router.get("/candidates/{candidate_id}", response_class=HTMLResponse)
async def candidate_detail(
    request: Request,
    candidate_id: str,
    service: CandidateService = Depends(get_candidate_service),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    return await _detail_or_not_available(
        request, candidate_id, service, templates, now=datetime.now(UTC)
    )


@router.post("/candidates/{candidate_id}/decide", response_model=None)
async def decide_candidate(
    request: Request,
    candidate_id: str,
    decision: Annotated[str, Form()] = "",
    user: CurrentUser = Depends(current_user),
    service: CandidateService = Depends(get_candidate_service),
    templates: Jinja2Templates = Depends(get_templates),
) -> RedirectResponse | HTMLResponse:
    if decision not in ("approve", "reject"):
        return await _detail_or_not_available(
            request,
            candidate_id,
            service,
            templates,
            now=datetime.now(UTC),
            error=INVALID_DECISION_ERROR,
            status_code=400,
        )
    kind: Literal["approve", "reject"] = "approve" if decision == "approve" else "reject"
    try:
        result = await service.decide(
            DecideCandidateRequest(candidate_id=candidate_id, decision=kind, decided_by=user.email)
        )
    except CandidateNotFound:
        view = build_candidate_not_available_view(None)
        return templates.TemplateResponse(
            request, "candidate_not_available.html", {"view": view}, status_code=404
        )
    except CandidateClosed:
        return RedirectResponse("/candidates?notice=closed#candidate-notice", status_code=303)
    token = "approved" if result.status == "approved" else "rejected"
    if result.status == "expired":
        token = "closed"
    return RedirectResponse(f"/candidates?notice={token}#candidate-notice", status_code=303)
