"""Frozen, bounded contract models shared by every source connector."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    UrlConstraints,
    field_validator,
    model_validator,
)

MAX_EXTERNAL_ID_LENGTH = 128
MAX_TITLE_LENGTH = 200
MAX_URL_LENGTH = 1024
MAX_METRICS = 4
MAX_METRIC_KEY_LENGTH = 16
MAX_METRIC_VALUE = 10_000_000
MAX_EVENTS = 100
MAX_POLL_LIMIT = 100

HttpUrl1024 = Annotated[HttpUrl, UrlConstraints(max_length=MAX_URL_LENGTH)]
MetricKey = Annotated[str, StringConstraints(min_length=1, max_length=MAX_METRIC_KEY_LENGTH)]
MetricValue = Annotated[int, Field(ge=0, le=MAX_METRIC_VALUE)]


class SourceName(StrEnum):
    """Registered-by-contract names; only `hn` is in the registry in this task."""

    HN = "hn"
    FAKE = "fake"


def ensure_aware_utc(value: datetime) -> datetime:
    """Reject naive datetimes and store aware ones in UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


class SourceEvent(BaseModel):
    """One normalized source item. Treat `metrics` as read-only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: SourceName
    external_id: str = Field(min_length=1, max_length=MAX_EXTERNAL_ID_LENGTH)
    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)
    url: HttpUrl1024 | None = None
    published_at: datetime | None = None
    observed_at: datetime
    metrics: dict[MetricKey, MetricValue] = Field(default_factory=dict, max_length=MAX_METRICS)

    @field_validator("external_id", "title", mode="before")
    @classmethod
    def _strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("published_at", "observed_at", mode="after")
    @classmethod
    def _to_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else ensure_aware_utc(value)


class SourcePollInput(BaseModel):
    """Input to a source poll: source and maximum items/events."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: SourceName
    limit: int = Field(default=30, ge=1, le=MAX_POLL_LIMIT)


def _empty_events() -> list[SourceEvent]:
    return []


class SourcePollOutput(BaseModel):
    """Normalized events from one poll; one source and unique external IDs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: SourceName
    events: list[SourceEvent] = Field(default_factory=_empty_events, max_length=MAX_EVENTS)

    @model_validator(mode="after")
    def _check_events(self) -> "SourcePollOutput":
        seen: set[str] = set()
        for event in self.events:
            if event.source is not self.source:
                raise ValueError("event source does not match the output source")
            if event.external_id in seen:
                raise ValueError(f"duplicate external_id {event.external_id!r}")
            seen.add(event.external_id)
        return self


class SourceConnector(Protocol):
    """A source connector turns a poll input into bounded normalized events."""

    async def poll(self, data: SourcePollInput) -> SourcePollOutput: ...
