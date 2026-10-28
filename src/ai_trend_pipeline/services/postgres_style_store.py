"""Postgres-backed style store (psycopg 3, one short connection per call)."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

import psycopg
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from ai_trend_pipeline.services.styles import StyleStoreUnavailable
from ai_trend_pipeline.style import ChartStyle

logger = logging.getLogger(__name__)

CREATE_SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS topic_studio"
CREATE_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS topic_studio.style_profiles ("
    "email text PRIMARY KEY, "
    "profile jsonb NOT NULL, "
    "updated_at timestamptz NOT NULL DEFAULT now())"
)
SELECT_SQL = "SELECT profile FROM topic_studio.style_profiles WHERE email = %s"
UPSERT_SQL = (
    "INSERT INTO topic_studio.style_profiles (email, profile) VALUES (%s, %s) "
    "ON CONFLICT (email) DO UPDATE SET profile = EXCLUDED.profile, updated_at = now()"
)


class PostgresStyleStore:
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
            logger.warning("Style store unavailable: %s", type(exc).__name__)
            raise StyleStoreUnavailable("style store unavailable") from exc

    async def get(self, email: str) -> ChartStyle | None:
        return await self._guarded(lambda: self._get(email))

    async def save(self, email: str, style: ChartStyle) -> None:
        await self._guarded(lambda: self._save(email, style))

    async def _get(self, email: str) -> ChartStyle | None:
        async with await psycopg.AsyncConnection.connect(self._url) as conn:
            cur = await conn.execute(SELECT_SQL, (email,))
            row = await cur.fetchone()
        if row is None:
            return None
        try:
            return ChartStyle.model_validate(row[0])
        except ValidationError:
            logger.warning("Stored style for a user failed validation; using defaults")
            return None

    async def _save(self, email: str, style: ChartStyle) -> None:
        async with await psycopg.AsyncConnection.connect(self._url) as conn:
            await conn.execute(UPSERT_SQL, (email, Jsonb(style.model_dump())))
