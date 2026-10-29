"""The registered fake second source and its defaults."""

from datetime import UTC, datetime
from typing import cast

import pytest
from temporalio.testing import ActivityEnvironment

from ai_trend_pipeline.config import Settings, load_settings
from ai_trend_pipeline.sources import SourceName, SourcePollInput, SourcePollOutput
from ai_trend_pipeline.sources.fake import DEFAULT_FAKE_ITEMS, FakeSourceActivities
from ai_trend_pipeline.sources.normalize import RawSourceItem
from ai_trend_pipeline.sources.registry import REGISTRY, enabled_sources


def test_fake_registry_entry_and_activity_name() -> None:
    fake = REGISTRY[SourceName.FAKE]
    assert fake.name is SourceName.FAKE
    assert fake.label == "Test source"
    assert fake.poll_activity == "poll_fake_source"
    assert fake.poll_activity == FakeSourceActivities.poll_fake_source.__name__
    assert [
        definition.name for definition in enabled_sources({SourceName.FAKE, SourceName.HN})
    ] == [
        SourceName.HN,
        SourceName.FAKE,
    ]
    assert [definition.label for definition in enabled_sources({SourceName.HN})] == ["Hacker News"]


@pytest.mark.asyncio
async def test_fake_activity_returns_the_two_fixed_events() -> None:
    result = await ActivityEnvironment().run(
        FakeSourceActivities().poll_fake_source,
        SourcePollInput(source=SourceName.FAKE),
    )
    assert isinstance(result, SourcePollOutput)
    assert result.source is SourceName.FAKE
    assert [event.external_id for event in result.events] == ["fake-1001", "fake-1002"]
    assert len({event.external_id for event in result.events}) == 2
    now = datetime.now(UTC)
    for event in result.events:
        assert event.source is SourceName.FAKE
        assert event.title
        assert event.url is not None
        assert event.url.scheme in {"http", "https"}
        assert event.published_at is None
        assert event.observed_at.utcoffset() is not None
        assert abs((now - event.observed_at).total_seconds()) < 60
        assert 0 <= event.metrics["score"] <= 100


@pytest.mark.asyncio
async def test_fake_activity_honours_limit_and_accepts_injected_items() -> None:
    limited = await ActivityEnvironment().run(
        FakeSourceActivities().poll_fake_source,
        SourcePollInput(source=SourceName.FAKE, limit=1),
    )
    assert isinstance(limited, SourcePollOutput)
    assert [event.external_id for event in limited.events] == ["fake-1001"]

    injected = RawSourceItem(
        external_id="injected-1",
        title="An injected test story",
        url="https://example.com/injected",
        published_at=None,
        metrics={"score": 1},
    )
    custom = await ActivityEnvironment().run(
        FakeSourceActivities(items=(injected,)).poll_fake_source,
        SourcePollInput(source=SourceName.FAKE),
    )
    assert isinstance(custom, SourcePollOutput)
    assert [event.external_id for event in custom.events] == ["injected-1"]
    assert len(DEFAULT_FAKE_ITEMS) == 2


def test_fake_source_is_off_by_default_and_on_by_config() -> None:
    assert Settings().source_enabled == frozenset({SourceName.HN})
    assert load_settings({}).source_enabled == frozenset({SourceName.HN})
    assert load_settings({"SOURCE_ENABLED": "hn,fake"}).source_enabled == frozenset(
        {SourceName.HN, SourceName.FAKE}
    )
    assert load_settings({"SOURCE_ENABLED": "fake"}).source_enabled == frozenset({SourceName.FAKE})
    with pytest.raises(ValueError):
        load_settings({"SOURCE_ENABLED": "google-trends"})
    assert cast(object, Settings(source_enabled=frozenset({SourceName.FAKE})))
