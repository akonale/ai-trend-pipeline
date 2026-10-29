"""FastAPI dependencies for settings, templates and the chart and candidate services."""

from fastapi import Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from temporalio.client import Client
from temporalio.service import RPCError

from ai_trend_pipeline import temporal_client
from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.services.candidates import CandidateService, CandidateUnavailable
from ai_trend_pipeline.services.charts import ChartService
from ai_trend_pipeline.services.postgres_style_store import PostgresStyleStore
from ai_trend_pipeline.services.styles import StyleService, StyleStore
from ai_trend_pipeline.services.temporal_gateway import (
    TemporalCandidateGateway,
    TemporalChartRunGateway,
)
from ai_trend_pipeline.web.view_models import ShellView, build_shell

DEV_USER_EMAIL = "dev@localhost"
CF_EMAIL_HEADER = "Cf-Access-Authenticated-User-Email"


class CurrentUser(BaseModel):
    email: str


def get_settings(request: Request) -> Settings:
    settings = request.app.state.settings  # pyright: ignore[reportAny]
    if not isinstance(settings, Settings):
        raise RuntimeError("app.state.settings is not set")
    return settings


def get_templates(request: Request) -> Jinja2Templates:
    templates = request.app.state.templates  # pyright: ignore[reportAny]
    if not isinstance(templates, Jinja2Templates):
        raise RuntimeError("app.state.templates is not set")
    return templates


async def get_style_store(
    request: Request, settings: Settings = Depends(get_settings)
) -> StyleStore:
    """Build the store once per process. It connects lazily, so this does no I/O."""
    cached = getattr(request.app.state, "style_store", None)
    if not isinstance(cached, PostgresStyleStore):
        cached = PostgresStyleStore(settings.database_url.get_secret_value())
        request.app.state.style_store = cached
    return cached


async def get_style_service(store: StyleStore = Depends(get_style_store)) -> StyleService:
    return StyleService(store)


async def get_chart_service(
    request: Request,
    settings: Settings = Depends(get_settings),
    styles: StyleStore = Depends(get_style_store),
) -> ChartService:
    client = getattr(request.app.state, "temporal_client", None)
    if not isinstance(client, Client):
        client = await temporal_client.connect(settings)
        request.app.state.temporal_client = client
    return ChartService(TemporalChartRunGateway(client), settings, styles=styles)


async def get_candidate_service(
    request: Request, settings: Settings = Depends(get_settings)
) -> CandidateService:
    client = getattr(request.app.state, "temporal_client", None)
    if not isinstance(client, Client):
        try:
            client = await temporal_client.connect(settings)
        except (RPCError, OSError, RuntimeError) as exc:
            raise CandidateUnavailable() from exc
        request.app.state.temporal_client = client
    return CandidateService(TemporalCandidateGateway(client), settings)


def optional_user(
    request: Request, settings: Settings = Depends(get_settings)
) -> CurrentUser | None:
    """The signed-in user, or None when Cloudflare sent no (or a blank) email header."""
    if settings.auth_mode == "local":
        return CurrentUser(email=DEV_USER_EMAIL)
    email = request.headers.get(CF_EMAIL_HEADER, "").strip().lower()
    return CurrentUser(email=email) if email else None


def current_user(user: CurrentUser | None = Depends(optional_user)) -> CurrentUser:
    if user is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return user


def shell_context(request: Request) -> dict[str, ShellView]:
    """Template context processor: the shell (nav, chip, footer) for every page. No I/O."""
    settings = get_settings(request)
    user = optional_user(request, settings)
    shell = build_shell(request.url.path, user.email if user else None, settings.auth_mode)
    return {"shell": shell}
