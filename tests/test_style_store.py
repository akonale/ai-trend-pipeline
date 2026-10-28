"""Pure checks on the Postgres store SQL. The live round trip is a manual check."""

from ai_trend_pipeline.services.postgres_style_store import (
    CREATE_SCHEMA_SQL,
    CREATE_TABLE_SQL,
    UPSERT_SQL,
)


def test_ddl_is_idempotent_and_namespaced() -> None:
    assert "CREATE SCHEMA IF NOT EXISTS topic_studio" in CREATE_SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS topic_studio.style_profiles" in CREATE_TABLE_SQL
    assert "email text PRIMARY KEY" in CREATE_TABLE_SQL
    assert "profile jsonb NOT NULL" in CREATE_TABLE_SQL


def test_upsert_uses_on_conflict() -> None:
    assert "ON CONFLICT (email) DO UPDATE" in UPSERT_SQL
