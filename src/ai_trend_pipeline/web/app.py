"""Application factory."""

from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.services.candidates import CandidateUnavailable
from ai_trend_pipeline.services.stories import StoryStatusUnavailable, story_filter
from ai_trend_pipeline.web.dependencies import get_templates, shell_context
from ai_trend_pipeline.web.routes import candidates, health, home, runs, stories
from ai_trend_pipeline.web.routes import settings as settings_routes
from ai_trend_pipeline.web.view_models import (
    build_candidate_unavailable_view,
    build_story_unavailable_view,
)

WEB_DIR = Path(__file__).parent


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="Topic Studio")
    app.state.settings = settings
    app.state.templates = Jinja2Templates(
        directory=WEB_DIR / "templates", context_processors=[shell_context]
    )
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    app.include_router(health.router)
    app.include_router(home.router)
    app.include_router(runs.router)
    app.include_router(settings_routes.router)
    app.include_router(candidates.router)
    app.include_router(stories.router)

    @app.exception_handler(CandidateUnavailable)
    async def candidate_unavailable_handler(request: Request, exc: Exception) -> HTMLResponse:
        view = build_candidate_unavailable_view(datetime.now(UTC))
        return get_templates(request).TemplateResponse(
            request, "candidates.html", {"view": view}, status_code=503
        )

    @app.exception_handler(StoryStatusUnavailable)
    async def story_status_unavailable_handler(request: Request, exc: Exception) -> HTMLResponse:
        view = build_story_unavailable_view(
            now=datetime.now(UTC),
            filter=story_filter(request.query_params.get("filter", "")),
        )
        templates = get_templates(request)
        if request.url.path == "/stories/rows":
            # htmx only swaps successful responses; a 200 without hx-get stops the poll.
            return templates.TemplateResponse(
                request,
                "partials/story_results.html",
                {"view": view},
                status_code=200,
            )
        return templates.TemplateResponse(request, "stories.html", {"view": view}, status_code=503)

    return app
