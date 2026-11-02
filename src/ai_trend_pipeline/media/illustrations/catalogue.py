"""The vendored illustration catalogue: a validated record per SVG, and a checked loader."""

import hashlib
import json
import re
from datetime import date
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from ai_trend_pipeline.media.illustrations.matching import TOPIC_TAGS

ILLUSTRATIONS_DIR = Path(__file__).resolve().parent
ASSET_DIR = ILLUSTRATIONS_DIR / "assets"
CATALOGUE_PATH = ILLUSTRATIONS_DIR / "catalogue.json"
MAX_ASSETS = 20
MAX_ASSETS_PER_TOPIC = 2
CC0_URL = "https://creativecommons.org/publicdomain/zero/1.0/"

_HEX_COLOUR = r"^#[0-9a-f]{6}$"
_ALLOWED_HOST = re.compile(r"^https://(?:[a-z0-9-]+\.)*opendoodles\.com(?:/|$)")


class IllustrationSource(StrEnum):
    OPEN_DOODLES = "open-doodles"


class IllustrationLicence(StrEnum):
    CC0_1_0 = "CC0-1.0"


class RecolourSlot(StrEnum):
    INK = "ink"
    PAPER = "paper"
    ACCENT = "accent"
    SUPPORT = "support"


Tag = Annotated[str, StringConstraints(min_length=1, max_length=40, pattern=r"^[a-z0-9 ]+$")]


class IllustrationAsset(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,31}$")
    file: str
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=3, max_length=80)
    topic: str
    source: IllustrationSource
    source_url: str = Field(pattern=r"^https://")
    author: str = Field(min_length=1, max_length=80)
    licence: IllustrationLicence
    licence_url: str = Field(pattern=r"^https://")
    licence_verified_on: date
    retrieved_on: date
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    modified: bool
    modification_note: str | None = None
    tags: list[Tag] = Field(min_length=3, max_length=30)
    recolour: dict[Annotated[str, StringConstraints(pattern=_HEX_COLOUR)], RecolourSlot] = Field(
        min_length=1
    )
    attribution_required: bool
    attribution_text: str | None = None

    @model_validator(mode="after")
    def _check_record(self) -> "IllustrationAsset":
        if self.file != f"{self.id}.svg":
            raise ValueError("file must be <id>.svg")
        if self.topic not in TOPIC_TAGS:
            raise ValueError(f"unknown topic {self.topic!r}")
        if self.tags != list(TOPIC_TAGS[self.topic]):
            raise ValueError("tags must equal the topic's row in the matching table")
        if len(set(self.tags)) != len(self.tags):
            raise ValueError("tags must be unique")
        if not _ALLOWED_HOST.match(self.source_url):
            raise ValueError("source_url must be a page on opendoodles.com")
        if self.licence_url != CC0_URL:
            raise ValueError("licence_url must be the CC0 1.0 legal page")
        if self.modified and not self.modification_note:
            raise ValueError("modification_note is required when modified is true")
        if self.attribution_required and not self.attribution_text:
            raise ValueError("attribution_text is required when attribution is required")
        return self


@lru_cache(maxsize=1)
def _load_cached(path: Path) -> tuple[IllustrationAsset, ...]:
    entries: list[object] = json.loads(path.read_text(encoding="utf-8"))
    assets = tuple(IllustrationAsset.model_validate(entry) for entry in entries)
    if len(assets) > MAX_ASSETS:
        raise ValueError(f"the catalogue has more than {MAX_ASSETS} assets")
    if len({asset.id for asset in assets}) != len(assets):
        raise ValueError("asset ids must be unique")
    return assets


def load_catalogue(path: Path = CATALOGUE_PATH) -> tuple[IllustrationAsset, ...]:
    """Load and validate the catalogue. The result is cached per path."""
    return _load_cached(path)


def catalogue_sha256(path: Path = CATALOGUE_PATH) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_asset_svg(asset: IllustrationAsset, asset_dir: Path = ASSET_DIR) -> str:
    """Read an asset file, checking it still matches the catalogue's SHA-256."""
    data = (asset_dir / asset.file).read_bytes()
    if hashlib.sha256(data).hexdigest() != asset.sha256:
        raise ValueError(f"{asset.file} does not match its catalogue sha256")
    return data.decode("utf-8")
