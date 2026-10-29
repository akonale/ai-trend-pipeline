"""Tests for the static source registry."""

from typing import cast

import pytest

from ai_trend_pipeline.sources import SourceName
from ai_trend_pipeline.sources.hn import HNSourceActivities
from ai_trend_pipeline.sources.registry import (
    REGISTRY,
    SourceDefinition,
    UnknownSourceError,
    enabled_sources,
    get_source,
)


def test_registry_contains_only_hn() -> None:
    assert len(REGISTRY) == 1
    for name, definition in REGISTRY.items():
        assert definition.name is name
        assert definition.label
        assert definition.poll_activity
    hn = REGISTRY[SourceName.HN]
    assert hn.label == "Hacker News"
    assert hn.poll_activity == HNSourceActivities.poll_hn_source.__name__ == "poll_hn_source"


def test_get_source_unknown_raises() -> None:
    assert get_source(SourceName.HN) is REGISTRY[SourceName.HN]
    with pytest.raises(UnknownSourceError):
        get_source(SourceName.FAKE)


def test_enabled_sources_filters_and_keeps_order() -> None:
    assert enabled_sources(set()) == []
    assert enabled_sources({SourceName.HN}) == [REGISTRY[SourceName.HN]]
    with pytest.raises(UnknownSourceError):
        enabled_sources({SourceName.FAKE})


def test_registry_is_read_only() -> None:
    mutable = cast("dict[SourceName, SourceDefinition]", REGISTRY)
    with pytest.raises(TypeError):
        mutable[SourceName.FAKE] = REGISTRY[SourceName.HN]
