"""Tests for worker wiring."""

from pathlib import Path

import anthropic
import httpx

from ai_trend_pipeline.bluesky import BlueskyCredentials
from ai_trend_pipeline.source_ingestion import (
    SOURCE_INGESTION_TASK_QUEUE,
    SourceIngestionWorkflow,
)
from ai_trend_pipeline.story_candidate import CANDIDATE_TASK_QUEUE, StoryCandidateWorkflow
from ai_trend_pipeline.worker import (
    CANDIDATE_WORKFLOWS,
    INGESTION_WORKFLOWS,
    daily_chart_activities,
    ingestion_activities,
)


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


def test_ingestion_workflow_and_activities_registered_on_ingestion_queue() -> None:
    assert INGESTION_WORKFLOWS == [SourceIngestionWorkflow]
    assert SOURCE_INGESTION_TASK_QUEUE == "source-ingestion-task-queue"
    activities = ingestion_activities(anthropic.AsyncAnthropic(api_key="test"), httpx.AsyncClient())
    assert sorted(activity.__name__ for activity in activities) == [
        "poll_fake_source",
        "poll_hn_source",
        "select_story_candidates",
    ]


def test_worker_startup_does_not_start_ingestion() -> None:
    source = Path(__file__).resolve().parent.parent / "src" / "ai_trend_pipeline" / "worker.py"
    text = source.read_text(encoding="utf-8")
    for forbidden in (
        "SourceIngestionService",
        "TemporalSourceIngestionGateway",
        "ingestion_starter",
        "ensure_ingestion",
    ):
        assert forbidden not in text, forbidden
