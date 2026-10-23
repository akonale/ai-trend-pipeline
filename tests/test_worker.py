"""Tests for worker wiring."""

import anthropic
import httpx

from ai_trend_pipeline.worker import daily_chart_activities


def test_daily_chart_activities_names() -> None:
    hn = httpx.AsyncClient()
    owid = httpx.AsyncClient()
    activities = daily_chart_activities(anthropic.AsyncAnthropic(api_key="test"), hn, owid)
    assert sorted(a.__name__ for a in activities) == sorted(
        [
            "fetch_hn_top_stories",
            "pick_topic",
            "fetch_owid_dataset",
            "write_chart_spec",
            "critique_chart",
            "render_chart_png",
        ]
    )
