"""Optional AI-illustration interface. Separate from the card renderer and unused for now."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from ai_trend_pipeline.media.models import MediaBrief, MediaType


@dataclass(frozen=True)
class Illustration:
    data: bytes
    media_type: MediaType
    width: int
    height: int
    provenance: str
    licence: str


class IllustrationProvider(Protocol):
    name: str
    version: str

    def illustrate(self, brief: MediaBrief) -> Illustration: ...


ILLUSTRATION_PROVIDERS: Mapping[str, Callable[[], IllustrationProvider]] = {}
