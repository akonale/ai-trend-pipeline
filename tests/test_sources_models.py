"""Tests for the frozen, bounded source contract models."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from ai_trend_pipeline.sources import (
    SourceEvent,
    SourceName,
    SourcePollInput,
    SourcePollOutput,
)

OBSERVED = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _event(
    *,
    source: SourceName = SourceName.HN,
    external_id: str = "1",
    title: str = "A story",
    url: str | None = None,
    published_at: datetime | None = None,
    observed_at: datetime = OBSERVED,
    metrics: dict[str, int] | None = None,
) -> SourceEvent:
    payload: dict[str, object] = {
        "source": source,
        "external_id": external_id,
        "title": title,
        "url": url,
        "published_at": published_at,
        "observed_at": observed_at,
        "metrics": {} if metrics is None else metrics,
    }
    return SourceEvent.model_validate(payload)


def test_source_event_minimal_and_frozen() -> None:
    event = SourceEvent(
        source=SourceName.HN,
        external_id="1",
        title="A story",
        observed_at=OBSERVED,
    )
    assert event.url is None
    assert event.published_at is None
    assert event.metrics == {}
    with pytest.raises(ValidationError):
        event.title = "changed"


def test_source_event_rejects_non_http_urls() -> None:
    for bad in (
        "javascript:alert(1)",
        "ftp://example.com/file",
        "data:text/plain,x",
        "file:///tmp/x",
        "http://",
        "https://?x=1",
    ):
        with pytest.raises(ValidationError):
            _event(url=bad)
    assert _event(url="http://example.com").url is not None
    assert _event(url="https://example.com/x").url is not None
    assert _event(url=None).url is None


def test_source_event_enforces_bounds() -> None:
    for bad in ("", "   ", "a" * 129):
        with pytest.raises(ValidationError):
            _event(external_id=bad)
    for bad_title in ("", "   ", "b" * 201):
        with pytest.raises(ValidationError):
            _event(title=bad_title)
    with pytest.raises(ValidationError):
        _event(url="https://example.com/" + "a" * 1100)
    with pytest.raises(ValidationError):
        _event(metrics={"a": 1, "b": 2, "c": 3, "d": 4, "e": 5})
    for bad_metrics in ({"": 1}, {"a" * 17: 1}, {"a": -1}, {"a": 10_000_001}):
        with pytest.raises(ValidationError):
            _event(metrics=bad_metrics)


def test_source_event_timestamps_are_aware_utc() -> None:
    with pytest.raises(ValidationError):
        _event(published_at=datetime(2026, 1, 1, 12, 0))
    with pytest.raises(ValidationError):
        _event(observed_at=datetime(2026, 1, 1, 12, 0))
    plus_two = timezone(timedelta(hours=2))
    event = _event(
        published_at=datetime(2026, 1, 1, 12, 0, tzinfo=plus_two),
        observed_at=datetime(2026, 1, 1, 12, 0, tzinfo=plus_two),
    )
    assert event.published_at == datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    assert event.observed_at == datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    assert event.observed_at.utcoffset() == timedelta(0)


def test_source_event_keeps_title_verbatim() -> None:
    title = '<script>alert("x")</script>'
    assert _event(title=title).title == title


def test_source_poll_input_bounds() -> None:
    assert SourcePollInput(source=SourceName.HN).limit == 30
    assert SourcePollInput(source=SourceName.HN, limit=1).limit == 1
    assert SourcePollInput(source=SourceName.HN, limit=100).limit == 100
    for bad in (0, 101):
        with pytest.raises(ValidationError):
            SourcePollInput(source=SourceName.HN, limit=bad)
    with pytest.raises(ValidationError):
        SourcePollInput.model_validate({"source": "hn", "limit": 1, "extra": "x"})


def test_source_poll_output_invariants() -> None:
    empty = SourcePollOutput(source=SourceName.HN)
    assert empty.events == []
    with pytest.raises(ValidationError):
        SourcePollOutput(source=SourceName.HN, events=[_event(source=SourceName.FAKE)])
    with pytest.raises(ValidationError):
        SourcePollOutput(
            source=SourceName.HN,
            events=[_event(external_id="1"), _event(external_id="1", title="again")],
        )
    many = [_event(external_id=str(i)) for i in range(101)]
    with pytest.raises(ValidationError):
        SourcePollOutput(source=SourceName.HN, events=many)


def test_contract_models_round_trip_json() -> None:
    event = _event(
        url="https://example.com/x",
        published_at=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
        metrics={"score": 3, "comments": 1},
    )
    assert SourceEvent.model_validate_json(event.model_dump_json()) == event
