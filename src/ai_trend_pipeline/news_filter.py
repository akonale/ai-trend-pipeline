"""The global News filter setting: model, store contract and the activity that loads it.

Editorial configuration only. It lives in Postgres, outside workflow history, and the ingestion
worker reads it through `load_news_filter_settings` before each batch. Threshold logic stays in
`story_classification`; this module only names which of its modes is selected.
"""

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator
from temporalio import activity
from temporalio.exceptions import ApplicationError

from ai_trend_pipeline.sources.models import ensure_aware_utc
from ai_trend_pipeline.story_classification import DEFAULT_NEWS_POLICY_MODE, NewsPolicyMode

MAX_UPDATED_BY_LENGTH = 254
NEWS_FILTER_UNAVAILABLE_ERROR_TYPE = "NewsFilterUnavailable"
NEWS_FILTER_INVALID_ERROR_TYPE = "NewsFilterInvalid"
LOAD_NEWS_FILTER_SETTINGS_ACTIVITY = "load_news_filter_settings"


class NewsFilterUnavailable(Exception):
    """The settings database could not be reached."""


class NewsFilterStoreError(Exception):
    """The stored setting is unreadable; it fails closed instead of guessing a mode."""


class NewsFilterSettings(BaseModel):
    """The one global mode plus who changed it and when (both absent for the default)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: NewsPolicyMode = DEFAULT_NEWS_POLICY_MODE
    updated_at: datetime | None = None
    updated_by: str | None = Field(default=None, min_length=1, max_length=MAX_UPDATED_BY_LENGTH)

    @field_validator("updated_at", mode="after")
    @classmethod
    def _to_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else ensure_aware_utc(value)


class NewsFilterStore(Protocol):
    async def get(self) -> NewsFilterSettings | None: ...

    async def save(self, settings: NewsFilterSettings) -> None: ...


async def load_settings(store: NewsFilterStore) -> NewsFilterSettings:
    """The stored settings; a missing row means the default strict policy."""
    return await store.get() or NewsFilterSettings()


class LoadNewsFilterSettingsInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class LoadNewsFilterSettingsOutput(BaseModel):
    """Only the mode crosses into workflow history; audit details stay in Postgres."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: NewsPolicyMode


class NewsFilterActivities:
    def __init__(self, store: NewsFilterStore) -> None:
        self._store = store

    @activity.defn(name=LOAD_NEWS_FILTER_SETTINGS_ACTIVITY)
    async def load_news_filter_settings(
        self, data: LoadNewsFilterSettingsInput
    ) -> LoadNewsFilterSettingsOutput:
        """Read-only, so safe to repeat. Failures carry fixed text, never storage details."""
        _ = data
        try:
            settings = await load_settings(self._store)
        except NewsFilterStoreError:
            raise ApplicationError(
                "stored news filter setting is invalid",
                type=NEWS_FILTER_INVALID_ERROR_TYPE,
                non_retryable=True,
            ) from None
        except NewsFilterUnavailable:
            raise ApplicationError(
                "news filter settings are unavailable",
                type=NEWS_FILTER_UNAVAILABLE_ERROR_TYPE,
            ) from None
        return LoadNewsFilterSettingsOutput(mode=settings.mode)
