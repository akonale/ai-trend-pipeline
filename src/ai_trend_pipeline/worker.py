"""Run the hello worker: `python -m ai_trend_pipeline.worker`."""

import asyncio
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import anthropic
import httpx
from temporalio.client import Client
from temporalio.worker import Worker

from ai_trend_pipeline.analysis.activities import FactActivities
from ai_trend_pipeline.bluesky import (
    BlueskyActivities,
    BlueskyCredentials,
    load_bluesky_credentials,
    make_bluesky_client,
)
from ai_trend_pipeline.chart_spec import ChartSpecActivities
from ai_trend_pipeline.config import Settings, load_settings
from ai_trend_pipeline.critique import CritiqueActivities
from ai_trend_pipeline.daily_chart import DAILY_CHART_TASK_QUEUE, DailyChartWorkflow
from ai_trend_pipeline.hello import TASK_QUEUE, HelloWorkflow, say_hello
from ai_trend_pipeline.hn import HNActivities, make_hn_client
from ai_trend_pipeline.owid import OwidActivities, make_owid_client
from ai_trend_pipeline.render import render_chart_png
from ai_trend_pipeline.research.activities import (
    StoryResearchActivities,
    make_research_client,
)
from ai_trend_pipeline.source_ingestion import (
    SOURCE_INGESTION_TASK_QUEUE,
    SourceIngestionWorkflow,
)
from ai_trend_pipeline.sources.fake import FakeSourceActivities
from ai_trend_pipeline.sources.hn import HNSourceActivities, make_source_hn_client
from ai_trend_pipeline.story_candidate import CANDIDATE_TASK_QUEUE, StoryCandidateWorkflow
from ai_trend_pipeline.story_drafting.critique import StoryCritiqueActivities
from ai_trend_pipeline.story_drafting.writing import StoryDraftActivities
from ai_trend_pipeline.story_generation import (
    STORY_TASK_QUEUE,
    StoryGenerationWorkflow,
)
from ai_trend_pipeline.story_selection import StorySelectionActivities
from ai_trend_pipeline.temporal_client import connect, ensure_story_search_attributes
from ai_trend_pipeline.topic import TopicActivities, make_anthropic_client

CONNECT_TIMEOUT_S = 30.0
RETRY_DELAY_S = 1.0
CANDIDATE_WORKFLOWS = [StoryCandidateWorkflow]
INGESTION_WORKFLOWS = [SourceIngestionWorkflow]
STORY_WORKFLOWS = [StoryGenerationWorkflow]


async def connect_with_retry(settings: Settings) -> Client:
    deadline = time.monotonic() + CONNECT_TIMEOUT_S
    while True:
        try:
            return await connect(settings)
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise
            await asyncio.sleep(RETRY_DELAY_S)


# Any matches the type of Temporal's `Worker(activities=...)` parameter.
def daily_chart_activities(
    anthropic_client: anthropic.AsyncAnthropic,
    hn_client: httpx.AsyncClient,
    owid_client: httpx.AsyncClient,
    bluesky_client: httpx.AsyncClient,
    bluesky_credentials: BlueskyCredentials,
) -> list[Callable[..., Any]]:
    hn = HNActivities(hn_client)
    topic = TopicActivities(anthropic_client)
    owid = OwidActivities(owid_client)
    chart_spec = ChartSpecActivities(anthropic_client)
    critique = CritiqueActivities(anthropic_client)
    bluesky = BlueskyActivities(bluesky_client, bluesky_credentials)
    return [
        hn.fetch_hn_top_stories,
        topic.pick_topic,
        owid.fetch_owid_dataset,
        chart_spec.write_chart_spec,
        critique.critique_chart,
        render_chart_png,
        bluesky.post_chart_to_bluesky,
    ]


# Any matches the type of Temporal's `Worker(activities=...)` parameter.
def ingestion_activities(
    anthropic_client: anthropic.AsyncAnthropic,
    source_hn_client: httpx.AsyncClient,
) -> list[Callable[..., Any]]:
    source_hn = HNSourceActivities(source_hn_client)
    source_fake = FakeSourceActivities()
    selection = StorySelectionActivities(anthropic_client)
    return [
        source_hn.poll_hn_source,
        source_fake.poll_fake_source,
        selection.select_story_candidates,
    ]


# Any matches the type of Temporal's `Worker(activities=...)` parameter.
def story_activities(
    anthropic_client: anthropic.AsyncAnthropic,
    research_client: httpx.AsyncClient,
    owid_client: httpx.AsyncClient,
    settings: Settings,
) -> list[Callable[..., Any]]:
    research = StoryResearchActivities(research_client, owid_client, settings)
    facts = FactActivities()
    drafting = StoryDraftActivities(anthropic_client)
    critique = StoryCritiqueActivities(anthropic_client)
    return [
        research.research_story,
        facts.calculate_facts,
        drafting.draft_story,
        critique.critique_story,
    ]


async def main() -> None:
    settings = load_settings()
    client = await connect_with_retry(settings)
    await ensure_story_search_attributes(client)
    anthropic_client = make_anthropic_client(settings)
    credentials = load_bluesky_credentials(settings)
    async with (
        make_hn_client() as hn_client,
        make_owid_client() as owid_client,
        make_bluesky_client(credentials.service) as bluesky_client,
        make_source_hn_client(settings) as source_hn_client,
        make_research_client(settings) as research_client,
        make_owid_client() as research_owid_client,
    ):
        hello_worker = Worker(
            client, task_queue=TASK_QUEUE, workflows=[HelloWorkflow], activities=[say_hello]
        )
        chart_worker = Worker(
            client,
            task_queue=DAILY_CHART_TASK_QUEUE,
            workflows=[DailyChartWorkflow],
            activities=daily_chart_activities(
                anthropic_client, hn_client, owid_client, bluesky_client, credentials
            ),
        )
        candidate_worker = Worker(
            client,
            task_queue=CANDIDATE_TASK_QUEUE,
            workflows=CANDIDATE_WORKFLOWS,
        )
        ingestion_worker = Worker(
            client,
            task_queue=SOURCE_INGESTION_TASK_QUEUE,
            workflows=INGESTION_WORKFLOWS,
            activities=ingestion_activities(anthropic_client, source_hn_client),
        )
        story_worker = Worker(
            client,
            task_queue=STORY_TASK_QUEUE,
            workflows=STORY_WORKFLOWS,
            activities=story_activities(
                anthropic_client, research_client, research_owid_client, settings
            ),
            activity_executor=ThreadPoolExecutor(max_workers=4),
        )
        await asyncio.gather(
            hello_worker.run(),
            chart_worker.run(),
            candidate_worker.run(),
            ingestion_worker.run(),
            story_worker.run(),
        )


if __name__ == "__main__":
    asyncio.run(main())
