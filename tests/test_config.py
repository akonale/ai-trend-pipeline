"""Tests for Settings and load_settings."""

import pytest

from ai_trend_pipeline.config import DEFAULT_BLUESKY_SERVICE, Settings, load_settings


def test_defaults() -> None:
    s = load_settings({})
    assert s.temporal_address == "localhost:7233"
    assert s.bluesky_service == "https://bsky.social" == DEFAULT_BLUESKY_SERVICE
    assert s.chart_output_dir is None
    assert s.anthropic_api_key is None
    assert s.bluesky_handle is None
    assert s.bluesky_app_password is None


def test_temporal_ui_url_default_and_trailing_slash() -> None:
    assert load_settings({}).temporal_ui_url == "http://localhost:8233"
    assert load_settings({"TEMPORAL_UI_URL": ""}).temporal_ui_url == "http://localhost:8233"
    assert load_settings({"TEMPORAL_UI_URL": "http://x:1/"}).temporal_ui_url == "http://x:1"


def test_maps_every_env_var_and_hides_secrets() -> None:
    s = load_settings(
        {
            "TEMPORAL_ADDRESS": "example:1234",
            "CHART_OUTPUT_DIR": "out",
            "ANTHROPIC_API_KEY": "key-secret",
            "BLUESKY_HANDLE": "me.example.invalid",
            "BLUESKY_APP_PASSWORD": "pw-secret",
            "BLUESKY_SERVICE": "https://pds.example.invalid",
        }
    )
    assert s.temporal_address == "example:1234"
    assert s.chart_output_dir == "out"
    assert s.anthropic_api_key is not None
    assert s.anthropic_api_key.get_secret_value() == "key-secret"
    assert s.bluesky_handle == "me.example.invalid"
    assert s.bluesky_app_password is not None
    assert s.bluesky_app_password.get_secret_value() == "pw-secret"
    assert s.bluesky_service == "https://pds.example.invalid"
    assert "key-secret" not in repr(s)
    assert "pw-secret" not in repr(s)


def test_empty_values_count_as_unset() -> None:
    s = load_settings(
        {
            "CHART_OUTPUT_DIR": "",
            "ANTHROPIC_API_KEY": "",
            "BLUESKY_SERVICE": "",
            "BLUESKY_HANDLE": "",
            "BLUESKY_APP_PASSWORD": "",
        }
    )
    assert s == Settings()


def test_reads_os_environ_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEMPORAL_ADDRESS", raising=False)
    assert load_settings().temporal_address == "localhost:7233"
    monkeypatch.setenv("TEMPORAL_ADDRESS", "example:1234")
    assert load_settings().temporal_address == "example:1234"


def test_web_host_read_and_defaults() -> None:
    assert load_settings({"WEB_HOST": "0.0.0.0"}).web_host == "0.0.0.0"
    assert load_settings({}).web_host == "127.0.0.1"


def test_auth_mode() -> None:
    assert load_settings({}).auth_mode == "local"
    assert load_settings({"AUTH_MODE": ""}).auth_mode == "local"
    assert load_settings({"AUTH_MODE": "cloudflare"}).auth_mode == "cloudflare"
    with pytest.raises(ValueError):
        load_settings({"AUTH_MODE": "bogus"})


def test_daily_chart_cap() -> None:
    assert Settings().daily_chart_cap == 10
    assert load_settings({}).daily_chart_cap == 10
    assert load_settings({"DAILY_CHART_CAP": ""}).daily_chart_cap == 10
    assert load_settings({"DAILY_CHART_CAP": "3"}).daily_chart_cap == 3
    for bad in ("0", "-1", "abc", "2.5"):
        with pytest.raises(ValueError):
            load_settings({"DAILY_CHART_CAP": bad})


def test_database_url() -> None:
    dev = "postgresql://postgres:postgres@localhost:5432/postgres"
    assert load_settings({}).database_url.get_secret_value() == dev
    assert load_settings({"DATABASE_URL": ""}).database_url.get_secret_value() == dev
    custom = load_settings({"DATABASE_URL": "host=h password=hunter2"})
    assert custom.database_url.get_secret_value() == "host=h password=hunter2"
    assert "hunter2" not in repr(custom)
