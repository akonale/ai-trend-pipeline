"""The single normalization path shared by the HN and fake connectors.

Source text is untrusted: this module never logs titles, URLs or metrics.
"""

from collections.abc import Iterable
from datetime import datetime

from pydantic import BaseModel, ConfigDict, HttpUrl, TypeAdapter, ValidationError

from ai_trend_pipeline.sources.models import (
    MAX_EXTERNAL_ID_LENGTH,
    MAX_METRIC_KEY_LENGTH,
    MAX_METRIC_VALUE,
    MAX_METRICS,
    MAX_TITLE_LENGTH,
    MAX_URL_LENGTH,
    SourceEvent,
    SourceName,
    SourcePollOutput,
    ensure_aware_utc,
)

_HTTP_URL: TypeAdapter[HttpUrl] = TypeAdapter(HttpUrl)


class RawSourceItem(BaseModel):
    """Internal connector output; never crosses an activity boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    external_id: str
    title: str | None
    url: str | None
    published_at: datetime | None
    metrics: dict[str, int]
    discarded: bool = False


def _normalize_url(raw: str | None) -> HttpUrl | None:
    if raw is None:
        return None
    candidate = raw.strip()
    if not candidate or len(candidate) > MAX_URL_LENGTH:
        return None
    try:
        return _HTTP_URL.validate_python(candidate)
    except ValidationError:
        return None


def _metrics_ok(metrics: dict[str, int]) -> bool:
    if len(metrics) > MAX_METRICS:
        return False
    return all(
        1 <= len(key) <= MAX_METRIC_KEY_LENGTH and 0 <= value <= MAX_METRIC_VALUE
        for key, value in metrics.items()
    )


def normalize_events(
    *,
    source: SourceName,
    items: Iterable[RawSourceItem],
    limit: int,
    observed_at: datetime,
) -> SourcePollOutput:
    """Drop invalid items and duplicates, preserving order and using one observed_at."""
    observed = ensure_aware_utc(observed_at)
    events: list[SourceEvent] = []
    seen: set[str] = set()
    for item in items:
        if len(events) >= limit:
            break
        if item.discarded:
            continue
        external_id = item.external_id.strip()
        if not external_id or len(external_id) > MAX_EXTERNAL_ID_LENGTH or external_id in seen:
            continue
        title = (item.title or "").strip()
        if not title or len(title) > MAX_TITLE_LENGTH:
            continue
        if not _metrics_ok(item.metrics):
            continue
        seen.add(external_id)
        events.append(
            SourceEvent(
                source=source,
                external_id=external_id,
                title=title,
                url=_normalize_url(item.url),
                published_at=item.published_at,
                observed_at=observed,
                metrics=dict(item.metrics),
            )
        )
    return SourcePollOutput(source=source, events=events)
