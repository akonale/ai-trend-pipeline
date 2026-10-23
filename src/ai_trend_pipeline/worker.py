"""Run the hello worker: `python -m ai_trend_pipeline.worker`."""

import asyncio
import time
from collections.abc import Callable
from typing import Any

import anthropic
import httpx
from temporalio.client import Client
from temporalio.worker import Worker

from ai_trend_pipeline.chart_spec import ChartSpecActivities
from ai_trend_pipeline.critique import CritiqueActivities
from ai_trend_pipeline.daily_chart import DAILY_CHART_TASK_QUEUE, DailyChartWorkflow
from ai_trend_pipeline.hello import TASK_QUEUE, HelloWorkflow, say_hello
from ai_trend_pipeline.hn import HNActivities, make_hn_client
from ai_trend_pipeline.owid import OwidActivities, make_owid_client
from ai_trend_pipeline.render import render_chart_png
from ai_trend_pipeline.temporal_client import connect
from ai_trend_pipeline.topic import TopicActivities, make_anthropic_client

CONNECT_TIMEOUT_S = 30.0
RETRY_DELAY_S = 1.0


async def connect_with_retry() -> Client:
    deadline = time.monotonic() + CONNECT_TIMEOUT_S
    while True:
        try:
            return await connect()
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise
            await asyncio.sleep(RETRY_DELAY_S)


# Any matches the type of Temporal's `Worker(activities=...)` parameter.
def daily_chart_activities(
    anthropic_client: anthropic.AsyncAnthropic,
    hn_client: httpx.AsyncClient,
    owid_client: httpx.AsyncClient,
) -> list[Callable[..., Any]]:
    hn = HNActivities(hn_client)
    topic = TopicActivities(anthropic_client)
    owid = OwidActivities(owid_client)
    chart_spec = ChartSpecActivities(anthropic_client)
    critique = CritiqueActivities(anthropic_client)
    return [
        hn.fetch_hn_top_stories,
        topic.pick_topic,
        owid.fetch_owid_dataset,
        chart_spec.write_chart_spec,
        critique.critique_chart,
        render_chart_png,
    ]


async def main() -> None:
    client = await connect_with_retry()
    anthropic_client = make_anthropic_client()
    async with make_hn_client() as hn_client, make_owid_client() as owid_client:
        hello_worker = Worker(
            client, task_queue=TASK_QUEUE, workflows=[HelloWorkflow], activities=[say_hello]
        )
        chart_worker = Worker(
            client,
            task_queue=DAILY_CHART_TASK_QUEUE,
            workflows=[DailyChartWorkflow],
            activities=daily_chart_activities(anthropic_client, hn_client, owid_client),
        )
        await asyncio.gather(hello_worker.run(), chart_worker.run())


if __name__ == "__main__":
    asyncio.run(main())
