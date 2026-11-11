"""News filter settings: service defaults, Postgres store behaviour and the load activity."""

from datetime import UTC, datetime

import psycopg
import pytest
from pydantic import ValidationError
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from ai_trend_pipeline.news_filter import (
    LoadNewsFilterSettingsInput,
    LoadNewsFilterSettingsOutput,
    NewsFilterActivities,
    NewsFilterSettings,
    NewsFilterStoreError,
    NewsFilterUnavailable,
)
from ai_trend_pipeline.services.news_filter import NewsFilterService
from ai_trend_pipeline.services.postgres_news_filter_store import (
    CREATE_SCHEMA_SQL,
    CREATE_TABLE_SQL,
    UPSERT_SQL,
    PostgresNewsFilterStore,
)
from ai_trend_pipeline.story_classification import DEFAULT_NEWS_POLICY_MODE, NewsPolicyMode

WHEN = datetime(2026, 11, 11, 9, 30, tzinfo=UTC)


class InMemoryNewsFilterStore:
    def __init__(self, stored: NewsFilterSettings | None = None) -> None:
        self.stored = stored
        self.error: Exception | None = None

    async def get(self) -> NewsFilterSettings | None:
        if self.error is not None:
            raise self.error
        return self.stored

    async def save(self, settings: NewsFilterSettings) -> None:
        if self.error is not None:
            raise self.error
        self.stored = settings


@pytest.mark.asyncio
async def test_missing_settings_row_defaults_to_strict() -> None:
    service = NewsFilterService(InMemoryNewsFilterStore())
    settings = await service.get()
    assert settings.mode is NewsPolicyMode.STRICT
    assert settings.mode is DEFAULT_NEWS_POLICY_MODE
    assert settings.updated_at is None
    assert settings.updated_by is None


@pytest.mark.asyncio
async def test_service_update_stamps_audit_metadata() -> None:
    store = InMemoryNewsFilterStore()
    service = NewsFilterService(store, clock=lambda: WHEN)
    saved = await service.update(NewsPolicyMode.RELAXED, updated_by="ash@example.com")
    assert saved == NewsFilterSettings(
        mode=NewsPolicyMode.RELAXED, updated_at=WHEN, updated_by="ash@example.com"
    )
    assert store.stored == saved
    assert (await service.get()).mode is NewsPolicyMode.RELAXED


def test_settings_model_is_bounded() -> None:
    with pytest.raises(ValidationError):
        NewsFilterSettings(mode="loose")  # pyright: ignore[reportArgumentType]
    with pytest.raises(ValidationError):
        NewsFilterSettings(updated_by="x" * 400)


class _Cursor:
    def __init__(self, row: tuple[str, datetime, str | None] | None) -> None:
        self._row = row

    async def fetchone(self) -> tuple[str, datetime, str | None] | None:
        return self._row


class FakeDatabase:
    """Stands in for `psycopg.AsyncConnection.connect`: one settings row, recorded SQL."""

    def __init__(self) -> None:
        self.row: tuple[str, datetime, str | None] | None = None
        self.statements: list[str] = []
        self.error: Exception | None = None

    async def connect(self, url: str) -> "FakeDatabase":
        if self.error is not None:
            raise self.error
        return self

    async def __aenter__(self) -> "FakeDatabase":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def execute(self, sql: str, params: tuple[object, ...] | None = None) -> _Cursor:
        self.statements.append(sql)
        if sql == UPSERT_SQL:
            assert params is not None
            mode, updated_at, updated_by = params
            assert isinstance(mode, str)
            assert isinstance(updated_at, datetime)
            assert updated_by is None or isinstance(updated_by, str)
            self.row = (mode, updated_at, updated_by)
        return _Cursor(self.row)


def store_with(monkeypatch: pytest.MonkeyPatch, db: FakeDatabase) -> PostgresNewsFilterStore:
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", db.connect)
    return PostgresNewsFilterStore("postgresql://x")


def test_ddl_is_idempotent_namespaced_and_a_singleton() -> None:
    assert "CREATE SCHEMA IF NOT EXISTS topic_studio" in CREATE_SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS topic_studio.news_filter_settings" in CREATE_TABLE_SQL
    assert "CHECK (id = 1)" in CREATE_TABLE_SQL
    assert "ON CONFLICT (id) DO UPDATE" in UPSERT_SQL


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", list(NewsPolicyMode))
async def test_store_round_trips_each_mode_and_audit_metadata(
    monkeypatch: pytest.MonkeyPatch, mode: NewsPolicyMode
) -> None:
    db = FakeDatabase()
    store = store_with(monkeypatch, db)
    assert await store.get() is None
    settings = NewsFilterSettings(mode=mode, updated_at=WHEN, updated_by="ash@example.com")
    await store.save(settings)
    assert await store.get() == settings
    assert db.statements[0] == CREATE_SCHEMA_SQL
    assert db.statements.count(CREATE_TABLE_SQL) == 1  # schema setup runs once


@pytest.mark.asyncio
async def test_invalid_stored_mode_and_database_errors_are_typed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDatabase()
    store = store_with(monkeypatch, db)
    db.row = ("anything-goes", WHEN, None)
    with pytest.raises(NewsFilterStoreError):
        await store.get()

    db.error = psycopg.OperationalError("down")
    with pytest.raises(NewsFilterUnavailable):
        await store.get()
    with pytest.raises(NewsFilterUnavailable):
        await store.save(NewsFilterSettings(mode=NewsPolicyMode.BALANCED, updated_at=WHEN))
    db.error = OSError("refused")
    with pytest.raises(NewsFilterUnavailable):
        await store.get()


@pytest.mark.asyncio
async def test_failed_schema_setup_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeDatabase()
    store = store_with(monkeypatch, db)
    db.error = psycopg.OperationalError("down")
    with pytest.raises(NewsFilterUnavailable):
        await store.get()
    db.error = None
    assert await store.get() is None
    assert CREATE_TABLE_SQL in db.statements


@pytest.mark.asyncio
async def test_load_activity_returns_only_the_mode() -> None:
    store = InMemoryNewsFilterStore(
        NewsFilterSettings(
            mode=NewsPolicyMode.BALANCED, updated_at=WHEN, updated_by="ash@example.com"
        )
    )
    activities = NewsFilterActivities(store)
    result = await ActivityEnvironment().run(
        activities.load_news_filter_settings, LoadNewsFilterSettingsInput()
    )
    assert result == LoadNewsFilterSettingsOutput(mode=NewsPolicyMode.BALANCED)
    assert "ash@example.com" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_load_activity_defaults_to_strict_when_nothing_is_stored() -> None:
    activities = NewsFilterActivities(InMemoryNewsFilterStore())
    result = await ActivityEnvironment().run(
        activities.load_news_filter_settings, LoadNewsFilterSettingsInput()
    )
    assert result.mode is NewsPolicyMode.STRICT


@pytest.mark.asyncio
async def test_load_activity_failures_are_fixed_and_never_relax() -> None:
    store = InMemoryNewsFilterStore()
    activities = NewsFilterActivities(store)
    env = ActivityEnvironment()

    store.error = NewsFilterStoreError("row says ash@example.com loose")
    with pytest.raises(ApplicationError) as invalid:
        await env.run(activities.load_news_filter_settings, LoadNewsFilterSettingsInput())
    assert invalid.value.non_retryable
    assert "ash@example.com" not in str(invalid.value)

    store.error = NewsFilterUnavailable("postgresql://user:secret@host")
    with pytest.raises(ApplicationError) as down:
        await env.run(activities.load_news_filter_settings, LoadNewsFilterSettingsInput())
    assert not down.value.non_retryable
    assert "secret" not in str(down.value)
