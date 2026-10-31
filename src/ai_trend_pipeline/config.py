"""Settings: the only place environment variables are read."""

import os
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, TypeAdapter, field_validator

from ai_trend_pipeline.media.providers import MEDIA_RENDERERS, MediaProviderName
from ai_trend_pipeline.research import HostName
from ai_trend_pipeline.sources import SourceName
from ai_trend_pipeline.sources.registry import (
    REGISTRY,
    UnknownSourceError,
    enabled_sources,
)
from ai_trend_pipeline.story_candidate import DEFAULT_CANDIDATE_REVIEW_TIMEOUT_S

DEFAULT_BLUESKY_SERVICE = "https://bsky.social"
DEFAULT_TEMPORAL_ADDRESS = "localhost:7233"
DEFAULT_TEMPORAL_UI_URL = "http://localhost:8233"
DEFAULT_WEB_HOST = "127.0.0.1"
DEFAULT_MEDIA_DIR = "media"
DEFAULT_DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/postgres"
DEFAULT_RESEARCH_FETCH_TIMEOUT_S = 10.0
DEFAULT_RESEARCH_MAX_RESPONSE_BYTES = 524288
DEFAULT_RESEARCH_MAX_REDIRECTS = 3
DEFAULT_RESEARCH_USER_AGENT = "ai-trend-pipeline-research/0.1"


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    temporal_address: str = DEFAULT_TEMPORAL_ADDRESS
    temporal_ui_url: str = DEFAULT_TEMPORAL_UI_URL
    web_host: str = DEFAULT_WEB_HOST
    chart_output_dir: str | None = None
    media_dir: str = Field(default=DEFAULT_MEDIA_DIR, min_length=1)
    media_provider: MediaProviderName = MediaProviderName.STORY_CARD
    anthropic_api_key: SecretStr | None = None
    bluesky_handle: str | None = None
    bluesky_app_password: SecretStr | None = None
    bluesky_service: str = DEFAULT_BLUESKY_SERVICE
    auth_mode: Literal["local", "cloudflare"] = "local"
    daily_chart_cap: int = Field(default=10, ge=1)
    database_url: SecretStr = SecretStr(DEFAULT_DATABASE_URL)
    source_enabled: frozenset[SourceName] = frozenset({SourceName.HN})
    source_poll_limit: int = Field(default=30, ge=1, le=100)
    source_poll_interval_s: int = Field(default=300, ge=30, le=3600)
    source_poll_timeout_s: float = Field(default=15.0, gt=0, le=60)
    candidate_review_timeout_s: int = Field(
        default=DEFAULT_CANDIDATE_REVIEW_TIMEOUT_S, ge=1, le=604800
    )
    source_candidate_budget: int = Field(default=20, ge=1, le=500)
    research_fetch_timeout_s: float = Field(default=DEFAULT_RESEARCH_FETCH_TIMEOUT_S, gt=0, le=60)
    research_max_response_bytes: int = Field(
        default=DEFAULT_RESEARCH_MAX_RESPONSE_BYTES, ge=1024, le=4194304
    )
    research_max_redirects: int = Field(default=DEFAULT_RESEARCH_MAX_REDIRECTS, ge=0, le=5)
    research_user_agent: str = Field(
        default=DEFAULT_RESEARCH_USER_AGENT, min_length=1, max_length=200
    )
    research_extra_context_hosts: frozenset[HostName] = frozenset()

    @field_validator("source_enabled")
    @classmethod
    def _validate_sources(cls, value: frozenset[SourceName]) -> frozenset[SourceName]:
        try:
            enabled_sources(value)
        except UnknownSourceError as exc:
            raise ValueError(str(exc)) from exc
        return value

    @field_validator("research_user_agent", mode="before")
    @classmethod
    def _default_user_agent(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or DEFAULT_RESEARCH_USER_AGENT
        return value


def _registered_names() -> str:
    return ", ".join(name.value for name in REGISTRY)


def _parse_source_enabled(raw: str | None) -> frozenset[SourceName]:
    if raw is None or not raw.strip():
        return frozenset({SourceName.HN})
    if raw.strip().lower() == "none":
        return frozenset()
    names: set[SourceName] = set()
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        try:
            names.add(SourceName(token.lower()))
        except ValueError:
            raise ValueError(
                f"Unknown source {token!r}. Registered sources: {_registered_names()}"
            ) from None
    try:
        enabled_sources(names)
    except UnknownSourceError as exc:
        raise ValueError(str(exc)) from exc
    return frozenset(names)


def _parse_media_provider(raw: str | None) -> MediaProviderName:
    token = (raw or "").strip().lower()
    if not token:
        return MediaProviderName.STORY_CARD
    try:
        return MediaProviderName(token)
    except ValueError:
        names = ", ".join(name.value for name in MEDIA_RENDERERS)
        raise ValueError(
            f"Unknown media provider {token!r}. Registered providers: {names}"
        ) from None


def _parse_extra_context_hosts(raw: str | None) -> frozenset[HostName]:
    if raw is None or not raw.strip() or raw.strip().lower() == "none":
        return frozenset()
    tokens = [part.strip() for part in raw.split(",") if part.strip()]
    return TypeAdapter(frozenset[HostName]).validate_python(tokens)


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if environ is None else environ
    api_key = env.get("ANTHROPIC_API_KEY")
    password = env.get("BLUESKY_APP_PASSWORD")
    return Settings(
        temporal_address=env.get("TEMPORAL_ADDRESS", DEFAULT_TEMPORAL_ADDRESS),
        temporal_ui_url=(env.get("TEMPORAL_UI_URL") or DEFAULT_TEMPORAL_UI_URL).rstrip("/"),
        web_host=env.get("WEB_HOST") or DEFAULT_WEB_HOST,
        chart_output_dir=env.get("CHART_OUTPUT_DIR") or None,
        media_dir=(env.get("MEDIA_DIR") or "").strip() or DEFAULT_MEDIA_DIR,
        media_provider=_parse_media_provider(env.get("MEDIA_PROVIDER")),
        anthropic_api_key=SecretStr(api_key) if api_key else None,
        bluesky_handle=env.get("BLUESKY_HANDLE") or None,
        bluesky_app_password=SecretStr(password) if password else None,
        bluesky_service=env.get("BLUESKY_SERVICE") or DEFAULT_BLUESKY_SERVICE,
        auth_mode=env.get("AUTH_MODE") or "local",  # pyright: ignore[reportArgumentType]  # validated by Pydantic
        daily_chart_cap=int(env.get("DAILY_CHART_CAP") or 10),
        database_url=SecretStr(env.get("DATABASE_URL") or DEFAULT_DATABASE_URL),
        source_enabled=_parse_source_enabled(env.get("SOURCE_ENABLED")),
        source_poll_limit=int(env.get("SOURCE_POLL_LIMIT") or 30),
        source_poll_interval_s=int(env.get("SOURCE_POLL_INTERVAL_S") or 300),
        source_poll_timeout_s=float(env.get("SOURCE_POLL_TIMEOUT_S") or 15.0),
        candidate_review_timeout_s=int(
            env.get("CANDIDATE_REVIEW_TIMEOUT_S") or DEFAULT_CANDIDATE_REVIEW_TIMEOUT_S
        ),
        source_candidate_budget=int(env.get("SOURCE_CANDIDATE_BUDGET") or 20),
        research_fetch_timeout_s=float(
            env.get("RESEARCH_FETCH_TIMEOUT_S") or DEFAULT_RESEARCH_FETCH_TIMEOUT_S
        ),
        research_max_response_bytes=int(
            env.get("RESEARCH_MAX_RESPONSE_BYTES") or DEFAULT_RESEARCH_MAX_RESPONSE_BYTES
        ),
        research_max_redirects=int(
            env.get("RESEARCH_MAX_REDIRECTS") or DEFAULT_RESEARCH_MAX_REDIRECTS
        ),
        research_user_agent=env.get("RESEARCH_USER_AGENT") or DEFAULT_RESEARCH_USER_AGENT,
        research_extra_context_hosts=_parse_extra_context_hosts(
            env.get("RESEARCH_EXTRA_CONTEXT_HOSTS")
        ),
    )
