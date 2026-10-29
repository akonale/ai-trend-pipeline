"""Story candidate inbox: the pending review queue."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ai_trend_pipeline.services.candidates import (
    CandidatePage,
    CandidateService,
    InvalidPageToken,
    ListCandidatesRequest,
)
from ai_trend_pipeline.web.dependencies import get_candidate_service, get_templates
from ai_trend_pipeline.web.view_models import (
    INVALID_PAGE_NOTICE,
    build_candidate_inbox_view,
)

router = APIRouter()


@router.get("/candidates", response_class=HTMLResponse)
async def candidate_inbox(
    request: Request,
    page: str = "",
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
    view = build_candidate_inbox_view(candidates, now=now, page_token=token)
    return templates.TemplateResponse(request, "candidates.html", {"view": view})
