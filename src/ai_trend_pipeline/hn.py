"""Activity that fetches current Hacker News top stories."""

import asyncio
from datetime import UTC, datetime

import httpx
from pydantic import BaseModel, Field, TypeAdapter
from temporalio import activity

HN_BASE_URL = "https://hacker-news.firebaseio.com/v0/"

_IDS_ADAPTER = TypeAdapter(list[int])


class HNTopStoriesInput(BaseModel):
    limit: int = Field(default=30, ge=1, le=100)


class HNStory(BaseModel):
    id: int
    title: str
    url: str | None
    score: int
    by: str
    time: datetime
    comments: int


class HNTopStoriesOutput(BaseModel):
    stories: list[HNStory]


class _RawItem(BaseModel):
    id: int
    type: str | None = None
    title: str | None = None
    url: str | None = None
    score: int = 0
    by: str = ""
    time: int = 0
    descendants: int = 0
    dead: bool = False
    deleted: bool = False


_RAW_ITEM_ADAPTER = TypeAdapter(_RawItem | None)


def make_hn_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=HN_BASE_URL, timeout=10.0)


class HNActivities:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def _fetch_item(self, item_id: int) -> _RawItem | None:
        response = await self._client.get(f"item/{item_id}.json")
        response.raise_for_status()
        return _RAW_ITEM_ADAPTER.validate_json(response.content)

    @activity.defn
    async def fetch_hn_top_stories(self, data: HNTopStoriesInput) -> HNTopStoriesOutput:
        response = await self._client.get("topstories.json")
        response.raise_for_status()
        ids = _IDS_ADAPTER.validate_json(response.content)[: data.limit]
        items = await asyncio.gather(*(self._fetch_item(i) for i in ids))
        stories = [
            HNStory(
                id=item.id,
                title=item.title,
                url=item.url,
                score=item.score,
                by=item.by,
                time=datetime.fromtimestamp(item.time, tz=UTC),
                comments=item.descendants,
            )
            for item in items
            if item is not None
            and item.type == "story"
            and not item.dead
            and not item.deleted
            and item.title
        ]
        return HNTopStoriesOutput(stories=stories)
