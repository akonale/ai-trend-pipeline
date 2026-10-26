"""Settings: the only place environment variables are read."""

import os
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, SecretStr

DEFAULT_BLUESKY_SERVICE = "https://bsky.social"
DEFAULT_TEMPORAL_ADDRESS = "localhost:7233"


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    temporal_address: str = DEFAULT_TEMPORAL_ADDRESS
    chart_output_dir: str | None = None
    anthropic_api_key: SecretStr | None = None
    bluesky_handle: str | None = None
    bluesky_app_password: SecretStr | None = None
    bluesky_service: str = DEFAULT_BLUESKY_SERVICE


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if environ is None else environ
    api_key = env.get("ANTHROPIC_API_KEY")
    password = env.get("BLUESKY_APP_PASSWORD")
    return Settings(
        temporal_address=env.get("TEMPORAL_ADDRESS", DEFAULT_TEMPORAL_ADDRESS),
        chart_output_dir=env.get("CHART_OUTPUT_DIR") or None,
        anthropic_api_key=SecretStr(api_key) if api_key else None,
        bluesky_handle=env.get("BLUESKY_HANDLE") or None,
        bluesky_app_password=SecretStr(password) if password else None,
        bluesky_service=env.get("BLUESKY_SERVICE") or DEFAULT_BLUESKY_SERVICE,
    )
