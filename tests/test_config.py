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
