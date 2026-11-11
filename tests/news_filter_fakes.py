"""Fake News filter policy loader for ingestion workflow tests."""

from collections.abc import Sequence

from temporalio import activity

from ai_trend_pipeline.news_filter import (
    LoadNewsFilterSettingsInput,
    LoadNewsFilterSettingsOutput,
)
from ai_trend_pipeline.story_classification import DEFAULT_NEWS_POLICY_MODE, NewsPolicyMode


class ScriptedPolicyLoader:
    """Stands in for the Postgres-backed activity; tests change `mode` between polls."""

    def __init__(
        self,
        mode: NewsPolicyMode = DEFAULT_NEWS_POLICY_MODE,
        *,
        errors: Sequence[BaseException] = (),
    ) -> None:
        self.mode = mode
        self.errors = list(errors)
        self.calls = 0

    @activity.defn(name="load_news_filter_settings")
    async def load_news_filter_settings(
        self, data: LoadNewsFilterSettingsInput
    ) -> LoadNewsFilterSettingsOutput:
        _ = data
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return LoadNewsFilterSettingsOutput(mode=self.mode)
