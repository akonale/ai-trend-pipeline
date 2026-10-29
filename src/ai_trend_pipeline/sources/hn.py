"""Hacker News source connector and its thin Temporal activity wrapper."""

from datetime import UTC, datetime

import httpx
from temporalio import activity

from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.hn import HN_BASE_URL, HNActivities, HNTopStoriesInput
from ai_trend_pipeline.sources.models import (
    SourceName,
    SourcePollInput,
    SourcePollOutput,
)
from ai_trend_pipeline.sources.normalize import RawSourceItem, normalize_events


class HNSourceConnector:
    """Reuses the existing HN HTTP activity, then normalizes its stories."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def poll(self, data: SourcePollInput) -> SourcePollOutput:
        if data.source is not SourceName.HN:
            raise ValueError(f"HNSourceConnector cannot poll source {data.source.value!r}")
        output = await HNActivities(self._client).fetch_hn_top_stories(
            HNTopStoriesInput(limit=data.limit)
        )
        observed_at = datetime.now(UTC)
        items = [
            RawSourceItem(
                external_id=str(story.id),
                title=story.title,
                url=story.url,
                published_at=story.time if story.time.timestamp() > 0 else None,
                metrics={"score": story.score, "comments": story.comments},
            )
            for story in output.stories
        ]
        return normalize_events(
            source=SourceName.HN,
            items=items,
            limit=data.limit,
            observed_at=observed_at,
        )


class HNSourceActivities:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._connector = HNSourceConnector(client)

    @activity.defn
    async def poll_hn_source(self, data: SourcePollInput) -> SourcePollOutput:
        return await self._connector.poll(data)


def make_source_hn_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=HN_BASE_URL,
        timeout=settings.source_poll_timeout_s,
    )
