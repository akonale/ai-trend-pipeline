"""Tests for worker wiring."""

import inspect
from pathlib import Path

import anthropic
import httpx

from ai_trend_pipeline.bluesky import BlueskyCredentials
from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.media.providers import MediaProviderName
from ai_trend_pipeline.source_ingestion import (
    SOURCE_INGESTION_TASK_QUEUE,
    SourceIngestionWorkflow,
)
from ai_trend_pipeline.story_candidate import CANDIDATE_TASK_QUEUE, StoryCandidateWorkflow
from ai_trend_pipeline.story_generation import (
    STORY_TASK_QUEUE,
    StoryGenerationWorkflow,
)
from ai_trend_pipeline.story_media import STORY_MEDIA_TASK_QUEUE, StoryMediaWorkflow
from ai_trend_pipeline.story_publishing import (
    STORY_PUBLISHING_TASK_QUEUE,
    StoryPublishingWorkflow,
)
from ai_trend_pipeline.worker import (
    CANDIDATE_WORKFLOWS,
    INGESTION_WORKFLOWS,
    MEDIA_WORKER_MAX_CONCURRENT_ACTIVITIES,
    MEDIA_WORKFLOWS,
    PUBLISHING_WORKER_MAX_CONCURRENT_ACTIVITIES,
    PUBLISHING_WORKFLOWS,
    STORY_WORKFLOWS,
    daily_chart_activities,
    ingestion_activities,
    media_activities,
    publishing_activities,
    story_activities,
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


def test_story_workflow_and_activities_registered_on_story_queue() -> None:
    assert STORY_WORKFLOWS == [StoryGenerationWorkflow]
    assert STORY_TASK_QUEUE == "story-generation-task-queue"
    activities = story_activities(
        anthropic.AsyncAnthropic(api_key="test"),
        httpx.AsyncClient(),
        httpx.AsyncClient(),
        Settings(),
    )
    assert sorted(activity.__name__ for activity in activities) == [
        "calculate_facts",
        "critique_story",
        "draft_story",
        "research_story",
    ]


def test_story_worker_uses_an_activity_executor_and_no_publishing_activity() -> None:
    source = Path(__file__).resolve().parent.parent / "src" / "ai_trend_pipeline" / "worker.py"
    text = source.read_text(encoding="utf-8")
    worker_block = text[text.index("story_worker = Worker(") : text.index("await asyncio.gather")]
    assert "activity_executor=ThreadPoolExecutor" in worker_block
    assert "post_chart_to_bluesky" not in worker_block
    assert "render_chart_png" not in worker_block
    story_source = inspect.getsource(story_activities)
    assert "post_chart_to_bluesky" not in story_source
    assert "render_chart_png" not in story_source


def test_media_worker_registers_workflow_activities_and_concurrency() -> None:
    assert MEDIA_WORKFLOWS == [StoryMediaWorkflow]
    assert STORY_MEDIA_TASK_QUEUE == "story-media-task-queue"
    assert MEDIA_WORKER_MAX_CONCURRENT_ACTIVITIES == 2
    activities = media_activities(Settings(media_provider=MediaProviderName.FAKE))
    assert sorted(activity.__name__ for activity in activities) == [
        "render_animated_media",
        "render_static_media",
    ]
    source = Path(__file__).resolve().parent.parent / "src" / "ai_trend_pipeline" / "worker.py"
    text = source.read_text(encoding="utf-8")
    block = text[text.index("media_worker = Worker(") : text.index("await asyncio.gather")]
    assert "task_queue=STORY_MEDIA_TASK_QUEUE" in block
    assert "max_concurrent_activities=MEDIA_WORKER_MAX_CONCURRENT_ACTIVITIES" in block
    assert "media_worker.run()" in text
    assert "max_concurrent_animations=MAX_CONCURRENT_ANIMATIONS" in inspect.getsource(
        media_activities
    )
    assert "MEDIA_PROVIDER" not in text


def test_publishing_worker_registers_workflow_and_story_activity() -> None:
    assert PUBLISHING_WORKFLOWS == [StoryPublishingWorkflow]
    assert STORY_PUBLISHING_TASK_QUEUE == "story-publishing-task-queue"
    assert PUBLISHING_WORKER_MAX_CONCURRENT_ACTIVITIES == 2
    creds = BlueskyCredentials.model_validate(
        {"handle": "h.invalid", "app_password": "pw", "service": "https://x"}
    )
    activities = publishing_activities(httpx.AsyncClient(), creds, Settings())
    assert [activity.__name__ for activity in activities] == ["publish_story_to_bluesky"]
    source = Path(__file__).resolve().parent.parent / "src" / "ai_trend_pipeline" / "worker.py"
    text = source.read_text(encoding="utf-8")
    block = text[text.index("publishing_worker = Worker(") : text.index("await asyncio.gather")]
    assert "task_queue=STORY_PUBLISHING_TASK_QUEUE" in block
    assert "workflows=PUBLISHING_WORKFLOWS" in block
    assert "publishing_activities(bluesky_client, credentials, settings)" in block
    assert "max_concurrent_activities=PUBLISHING_WORKER_MAX_CONCURRENT_ACTIVITIES" in block
    assert "publishing_worker.run()" in text
    assert "publish_story_to_bluesky" not in inspect.getsource(daily_chart_activities)
