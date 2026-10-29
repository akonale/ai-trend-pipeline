"""Tests for the static source registry."""

from typing import cast

import pytest

from ai_trend_pipeline.sources import SourceName
from ai_trend_pipeline.sources.fake import FakeSourceActivities
from ai_trend_pipeline.sources.hn import HNSourceActivities
from ai_trend_pipeline.sources.registry import (
    REGISTRY,
    SourceDefinition,
    enabled_sources,
    get_source,
)


def test_registry_contains_hn_then_fake() -> None:
    assert [name for name in REGISTRY] == [SourceName.HN, SourceName.FAKE]
    for name, definition in REGISTRY.items():
        assert definition.name is name
        assert definition.label
        assert definition.poll_activity
    hn = REGISTRY[SourceName.HN]
    fake = REGISTRY[SourceName.FAKE]
    assert hn.label == "Hacker News"
    assert hn.poll_activity == HNSourceActivities.poll_hn_source.__name__ == "poll_hn_source"
    assert fake.label == "Test source"
    assert (
        fake.poll_activity == FakeSourceActivities.poll_fake_source.__name__ == "poll_fake_source"
    )


def test_get_source_resolves_registered_names() -> None:
    assert get_source(SourceName.HN) is REGISTRY[SourceName.HN]
    assert get_source(SourceName.FAKE) is REGISTRY[SourceName.FAKE]


def test_enabled_sources_filters_and_keeps_order() -> None:
    assert enabled_sources(set()) == []
    assert enabled_sources({SourceName.HN}) == [REGISTRY[SourceName.HN]]
    assert enabled_sources({SourceName.FAKE}) == [REGISTRY[SourceName.FAKE]]
    assert enabled_sources({SourceName.FAKE, SourceName.HN}) == [
        REGISTRY[SourceName.HN],
        REGISTRY[SourceName.FAKE],
    ]


def test_registry_is_read_only() -> None:
    mutable = cast("dict[SourceName, SourceDefinition]", REGISTRY)
    with pytest.raises(TypeError):
        mutable[SourceName.FAKE] = REGISTRY[SourceName.HN]
