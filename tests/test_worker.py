"""Tests for worker wiring."""

import anthropic
import httpx

from ai_trend_pipeline.bluesky import BlueskyCredentials
from ai_trend_pipeline.story_candidate import CANDIDATE_TASK_QUEUE, StoryCandidateWorkflow
from ai_trend_pipeline.worker import CANDIDATE_WORKFLOWS, daily_chart_activities


def test_daily_chart_activities_names() -> None:
    hn = httpx.AsyncClient()
    owid = httpx.AsyncClient()
    bsky = httpx.AsyncClient()
    creds = BlueskyCredentials.model_validate(
        {"handle": "h.invalid", "app_password": "pw", "service": "https://x"}
    )
    activities = daily_chart_activities(
        anthropic.AsyncAnthropic(api_key="test"), hn, owid, bsky, creds
    )
    assert sorted(a.__name__ for a in activities) == sorted(
        [
            "fetch_hn_top_stories",
            "pick_topic",
            "fetch_owid_dataset",
            "write_chart_spec",
            "critique_chart",
            "render_chart_png",
            "post_chart_to_bluesky",
        ]
    )


def test_candidate_workflow_registered_on_candidate_queue() -> None:
    assert CANDIDATE_WORKFLOWS == [StoryCandidateWorkflow]
    assert CANDIDATE_TASK_QUEUE == "story-candidate-task-queue"
