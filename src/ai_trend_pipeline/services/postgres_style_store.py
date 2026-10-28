"""Postgres-backed style store (psycopg 3, one short connection per call)."""

import logging

import psycopg
from psycopg.types.json import Jsonb
from pydantic import ValidationError

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
    def __init__(self, database_url: str) -> None:
        self._url = database_url

    async def ensure_schema(self) -> None:
        async with await psycopg.AsyncConnection.connect(self._url) as conn:
            await conn.execute(CREATE_SCHEMA_SQL)
            await conn.execute(CREATE_TABLE_SQL)

    async def get(self, email: str) -> ChartStyle | None:
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

    async def save(self, email: str, style: ChartStyle) -> None:
        async with await psycopg.AsyncConnection.connect(self._url) as conn:
            await conn.execute(UPSERT_SQL, (email, Jsonb(style.model_dump())))
