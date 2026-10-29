"""Pure checks on the Postgres store SQL. The live round trip is a manual check."""

import psycopg
import pytest

from ai_trend_pipeline.services.postgres_style_store import (
    CREATE_SCHEMA_SQL,
    CREATE_TABLE_SQL,
    UPSERT_SQL,
    PostgresStyleStore,
)
from ai_trend_pipeline.services.styles import StyleStoreUnavailable


def test_ddl_is_idempotent_and_namespaced() -> None:
    assert "CREATE SCHEMA IF NOT EXISTS topic_studio" in CREATE_SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS topic_studio.style_profiles" in CREATE_TABLE_SQL
    assert "email text PRIMARY KEY" in CREATE_TABLE_SQL
    assert "profile jsonb NOT NULL" in CREATE_TABLE_SQL


def test_upsert_uses_on_conflict() -> None:
    assert "ON CONFLICT (email) DO UPDATE" in UPSERT_SQL


class _Cursor:
    async def fetchone(self) -> None:
        return None


class _Conn:
    def __init__(self, log: list[str]) -> None:
        self.log = log

    async def __aenter__(self) -> "_Conn":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def execute(self, sql: str, params: object = None) -> _Cursor:
        self.log.append(sql.split()[0])
        return _Cursor()


@pytest.mark.asyncio
async def test_schema_failure_is_not_cached_and_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    log: list[str] = []
    attempts = 0

    async def connect(url: str) -> _Conn:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise psycopg.OperationalError("down")
        return _Conn(log)

    monkeypatch.setattr(psycopg.AsyncConnection, "connect", connect)
    store = PostgresStyleStore("postgresql://x")
    with pytest.raises(StyleStoreUnavailable):
        await store.get("a@x.com")
    assert await store.get("a@x.com") is None
    assert log == ["CREATE", "CREATE", "SELECT"]
    assert await store.get("a@x.com") is None
    assert log.count("CREATE") == 2
