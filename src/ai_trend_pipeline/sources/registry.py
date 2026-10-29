"""Static source registry: names, labels and poll activity names only.

This module imports only `models.py`, so workflow code can import it without I/O.
"""

from collections.abc import Collection, Mapping
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict

from ai_trend_pipeline.sources.models import SourceName


class SourceDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: SourceName
    label: str
    poll_activity: str


class UnknownSourceError(LookupError):
    """Raised when a source name is not in the static registry."""


_REGISTRY = {
    SourceName.HN: SourceDefinition(
        name=SourceName.HN,
        label="Hacker News",
        poll_activity="poll_hn_source",
    ),
}

REGISTRY: Mapping[SourceName, SourceDefinition] = MappingProxyType(_REGISTRY)


def _registered_names() -> str:
    return ", ".join(name.value for name in REGISTRY)


def get_source(name: SourceName) -> SourceDefinition:
    try:
        return REGISTRY[name]
    except KeyError:
        raise UnknownSourceError(
            f"Unknown source {name.value!r}. Registered sources: {_registered_names()}"
        ) from None


def enabled_sources(enabled: Collection[SourceName]) -> list[SourceDefinition]:
    for name in enabled:
        get_source(name)
    return [definition for name, definition in REGISTRY.items() if name in enabled]
