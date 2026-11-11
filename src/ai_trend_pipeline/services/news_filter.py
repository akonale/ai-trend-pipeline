"""News filter use cases: read and change the one global classification mode."""

from collections.abc import Callable
from datetime import UTC, datetime

from ai_trend_pipeline.news_filter import (
    NewsFilterSettings,
    NewsFilterStore,
    NewsFilterStoreError,
    NewsFilterUnavailable,
    load_settings,
)
from ai_trend_pipeline.story_classification import NewsPolicyMode

__all__ = [
    "NewsFilterService",
    "NewsFilterStore",
    "NewsFilterStoreError",
    "NewsFilterUnavailable",
]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class NewsFilterService:
    def __init__(self, store: NewsFilterStore, clock: Callable[[], datetime] = _utc_now) -> None:
        self._store = store
        self._clock = clock

    async def get(self) -> NewsFilterSettings:
        return await load_settings(self._store)

    async def update(self, mode: NewsPolicyMode, *, updated_by: str | None) -> NewsFilterSettings:
        settings = NewsFilterSettings(mode=mode, updated_at=self._clock(), updated_by=updated_by)
        await self._store.save(settings)
        return settings
