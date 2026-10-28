"""In-memory style store for tests."""

from ai_trend_pipeline.style import ChartStyle


class InMemoryStyleStore:
    def __init__(self, styles: dict[str, ChartStyle] | None = None) -> None:
        self.styles: dict[str, ChartStyle] = dict(styles or {})
        self.get_calls: list[str] = []

    async def get(self, email: str) -> ChartStyle | None:
        self.get_calls.append(email)
        return self.styles.get(email)

    async def save(self, email: str, style: ChartStyle) -> None:
        self.styles[email] = style
