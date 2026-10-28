"""Style profile use cases behind a store protocol."""

from typing import Protocol

from ai_trend_pipeline.style import ChartStyle


class StyleStoreUnavailable(Exception):
    """The style store could not be reached; callers show a friendly retry message."""


class StyleStore(Protocol):
    async def get(self, email: str) -> ChartStyle | None: ...

    async def save(self, email: str, style: ChartStyle) -> None: ...


class StyleService:
    def __init__(self, store: StyleStore) -> None:
        self._store = store

    async def get(self, email: str) -> ChartStyle:
        return await self._store.get(email) or ChartStyle()

    async def save(self, email: str, style: ChartStyle) -> None:
        await self._store.save(email, style)
