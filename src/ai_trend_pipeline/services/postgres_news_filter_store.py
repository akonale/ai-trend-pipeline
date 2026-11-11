"""Postgres-backed News filter store: one singleton row, one short connection per call."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

import psycopg
from pydantic import ValidationError

from ai_trend_pipeline.news_filter import (
    NewsFilterSettings,
    NewsFilterStoreError,
    NewsFilterUnavailable,
)

logger = logging.getLogger(__name__)

CREATE_SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS topic_studio"
CREATE_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS topic_studio.news_filter_settings ("
    "id smallint PRIMARY KEY CHECK (id = 1), "
    "mode text NOT NULL, "
    "updated_at timestamptz NOT NULL, "
    "updated_by text)"
)
SELECT_SQL = (
    "SELECT mode, updated_at, updated_by FROM topic_studio.news_filter_settings WHERE id = 1"
)
UPSERT_SQL = (
    "INSERT INTO topic_studio.news_filter_settings (id, mode, updated_at, updated_by) "
    "VALUES (1, %s, %s, %s) "
    "ON CONFLICT (id) DO UPDATE SET mode = EXCLUDED.mode, "
    "updated_at = EXCLUDED.updated_at, updated_by = EXCLUDED.updated_by"
)


class PostgresNewsFilterStore:
    """Connects only on first real use; a failed schema setup is retried on the next call."""

    def __init__(self, database_url: str) -> None:
        self._url = database_url
        self._ready = False
        self._lock = asyncio.Lock()

    async def ensure_schema(self) -> None:
        async with await psycopg.AsyncConnection.connect(self._url) as conn:
            await conn.execute(CREATE_SCHEMA_SQL)
            await conn.execute(CREATE_TABLE_SQL)

    async def _ready_once(self) -> None:
        if self._ready:
            return
        async with self._lock:
            if not self._ready:
                await self.ensure_schema()
                self._ready = True

    async def _guarded[T](self, op: Callable[[], Awaitable[T]]) -> T:
        try:
            await self._ready_once()
            return await op()
        except (psycopg.Error, OSError) as exc:
            logger.warning("News filter store unavailable: %s", type(exc).__name__)
            raise NewsFilterUnavailable("news filter store unavailable") from exc

    async def get(self) -> NewsFilterSettings | None:
        return await self._guarded(self._get)

    async def save(self, settings: NewsFilterSettings) -> None:
        await self._guarded(lambda: self._save(settings))

    async def _get(self) -> NewsFilterSettings | None:
        async with await psycopg.AsyncConnection.connect(self._url) as conn:
            cur = await conn.execute(SELECT_SQL)
            row = await cur.fetchone()
        if row is None:
            return None
        try:
            return NewsFilterSettings.model_validate(
                {"mode": row[0], "updated_at": row[1], "updated_by": row[2]}
            )
        except ValidationError:
            logger.error("Stored news filter setting failed validation")
            raise NewsFilterStoreError("stored news filter setting is invalid") from None

    async def _save(self, settings: NewsFilterSettings) -> None:
        async with await psycopg.AsyncConnection.connect(self._url) as conn:
            await conn.execute(
                UPSERT_SQL, (settings.mode.value, settings.updated_at, settings.updated_by)
            )
