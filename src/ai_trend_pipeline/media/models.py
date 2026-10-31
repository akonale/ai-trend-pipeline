"""Bounded, frozen media contracts.

Imports only the standard library, Pydantic and the analysis fact-id pattern. It must not import
`story_drafting`, because `StoryPackage` imports this module.
"""

import hashlib
import json
import re
from enum import StrEnum
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from ai_trend_pipeline.analysis.models import FACT_ID_PATTERN, MAX_FACT_ID_CHARS

MEDIA_SCHEMA_VERSION = 1
MIN_MEDIA_DIMENSION_PX = 64
MAX_MEDIA_DIMENSION_PX = 2048
MAX_STATIC_BYTES = 1_000_000
MAX_ANIMATED_BYTES = 4_000_000
MIN_ANIMATION_MS = 1_000
MAX_ANIMATION_MS = 6_000
MAX_ALT_TEXT_CHARS = 1_000
MAX_HIGHLIGHT_CHARS = 280
MAX_SOURCE_LABEL_CHARS = 120  # same number as story_drafting.models.MAX_SOURCE_LABEL_CHARS
MAX_HEADLINE_CHARS = 160  # same number as story_drafting.models.MAX_HEADLINE_CHARS
MAX_ANIMATION_BEATS = 4
MAX_HIGHLIGHT_FACT_IDS = 4
MAX_MANIFEST_JSON_BYTES = 8_192
MAX_MEDIA_STORY_ID_CHARS = 64

ARTIFACT_ID_PATTERN = r"^art-v1-[0-9a-f]{16}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")
_URL_MARKER = re.compile(r"(?i)(?:https?://|www\.)")
_MARKUP_CHARS = ("<", ">", "`")


class MediaType(StrEnum):
    PNG = "image/png"
    WEBP = "image/webp"
    GIF = "image/gif"


MEDIA_EXTENSIONS: dict[MediaType, str] = {
    MediaType.PNG: "png",
    MediaType.WEBP: "webp",
    MediaType.GIF: "gif",
}
MEDIA_SIGNATURES: dict[MediaType, bytes] = {
    MediaType.PNG: b"\x89PNG\r\n\x1a\n",
    MediaType.WEBP: b"RIFF",
    MediaType.GIF: b"GIF89a",
}


def has_media_signature(data: bytes, media_type: MediaType) -> bool:
    """True when `data` starts with the signature for `media_type` (WEBP also needs WEBP at 8)."""
    if not data.startswith(MEDIA_SIGNATURES[media_type]):
        return False
    if media_type is MediaType.WEBP:
        return data[8:12] == b"WEBP"
    return True


class MediaKind(StrEnum):
    STATIC = "static"
    ANIMATED = "animated"


class MediaConcept(StrEnum):
    HEADLINE_CARD = "headline_card"


class AnimationBeat(StrEnum):
    HEADLINE_REVEAL = "headline_reveal"
    FACT_EMPHASIS = "fact_emphasis"
    SOURCE_FOOTER = "source_footer"


class MediaOutcome(StrEnum):
    GIF = "gif"
    STATIC = "static"
    STATIC_FALLBACK = "static_fallback"
    TEXT_ONLY = "text_only"


def canonical_json(obj: object) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def clean_media_text(value: object) -> object:
    """Collapse control characters and whitespace, strip, then reject URLs and markup."""
    if not isinstance(value, str):
        return value
    cleaned = _WHITESPACE.sub(" ", _CONTROL_CHARS.sub(" ", value)).strip()
    if _URL_MARKER.search(cleaned):
        raise ValueError("media text must not contain URLs")
    if any(char in cleaned for char in _MARKUP_CHARS):
        raise ValueError("media text must not contain markup characters")
    return cleaned


def _empty_fact_ids() -> list[str]:
    return []


FactIdText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_FACT_ID_CHARS,
        pattern=FACT_ID_PATTERN,
    ),
]


class MediaBrief(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(default=MEDIA_SCHEMA_VERSION, ge=1)
    story_id: str = Field(min_length=1, max_length=MAX_MEDIA_STORY_ID_CHARS)
    concept: MediaConcept = MediaConcept.HEADLINE_CARD
    headline: str = Field(min_length=1, max_length=MAX_HEADLINE_CHARS)
    highlight: str | None = Field(default=None, min_length=1, max_length=MAX_HIGHLIGHT_CHARS)
    highlight_fact_ids: list[FactIdText] = Field(
        default_factory=_empty_fact_ids, max_length=MAX_HIGHLIGHT_FACT_IDS
    )
    source_label: str | None = Field(default=None, min_length=1, max_length=MAX_SOURCE_LABEL_CHARS)
    animation_beats: list[AnimationBeat] = Field(min_length=1, max_length=MAX_ANIMATION_BEATS)
    alt_text: str = Field(min_length=1, max_length=MAX_ALT_TEXT_CHARS)

    @field_validator("headline", "highlight", "source_label", "alt_text", mode="before")
    @classmethod
    def _clean_text(cls, value: object) -> object:
        return clean_media_text(value)

    @field_validator("highlight_fact_ids", mode="after")
    @classmethod
    def _unique_fact_ids(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("highlight_fact_ids must be unique")
        return value

    @field_validator("animation_beats", mode="after")
    @classmethod
    def _unique_beats(cls, value: list[AnimationBeat]) -> list[AnimationBeat]:
        if len(set(value)) != len(value):
            raise ValueError("animation_beats must be unique")
        return value

    @model_validator(mode="after")
    def _check_beats(self) -> "MediaBrief":
        if AnimationBeat.FACT_EMPHASIS in self.animation_beats and self.highlight is None:
            raise ValueError("fact_emphasis needs a highlight")
        return self


class MediaArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: str = Field(pattern=ARTIFACT_ID_PATTERN)
    kind: MediaKind
    media_type: MediaType
    width: int = Field(ge=MIN_MEDIA_DIMENSION_PX, le=MAX_MEDIA_DIMENSION_PX)
    height: int = Field(ge=MIN_MEDIA_DIMENSION_PX, le=MAX_MEDIA_DIMENSION_PX)
    duration_ms: int | None = None
    byte_size: int = Field(ge=1)
    alt_text: str = Field(min_length=1, max_length=MAX_ALT_TEXT_CHARS)
    content_hash: str = Field(pattern=SHA256_PATTERN)
    relative_path: str = Field(min_length=1, max_length=128)

    @field_validator("alt_text", mode="before")
    @classmethod
    def _clean_alt(cls, value: object) -> object:
        return clean_media_text(value)

    @model_validator(mode="after")
    def _check_artifact(self) -> "MediaArtifact":
        expected = f"{self.content_hash}.{MEDIA_EXTENSIONS[self.media_type]}"
        if self.relative_path != expected:
            raise ValueError("relative_path must be <content_hash>.<extension>")
        if self.kind is MediaKind.STATIC:
            if self.media_type not in {MediaType.PNG, MediaType.WEBP}:
                raise ValueError("static media must be png or webp")
            if self.duration_ms is not None:
                raise ValueError("static media must not have a duration")
            if self.byte_size > MAX_STATIC_BYTES:
                raise ValueError("static media is too large")
        else:
            if self.media_type is not MediaType.GIF:
                raise ValueError("animated media must be gif")
            if self.duration_ms is None or not (
                MIN_ANIMATION_MS <= self.duration_ms <= MAX_ANIMATION_MS
            ):
                raise ValueError("animated media needs a duration of 1000..6000 ms")
            if self.byte_size > MAX_ANIMATED_BYTES:
                raise ValueError("animated media is too large")
        return self


class MediaManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(default=MEDIA_SCHEMA_VERSION, ge=1)
    story_id: str = Field(min_length=1, max_length=MAX_MEDIA_STORY_ID_CHARS)
    story_content_hash: str = Field(pattern=SHA256_PATTERN)
    brief_hash: str = Field(pattern=SHA256_PATTERN)
    outcome: MediaOutcome
    static_artifact: MediaArtifact | None = None
    animated_artifact: MediaArtifact | None = None

    @model_validator(mode="after")
    def _check_manifest(self) -> "MediaManifest":
        static, animated = self.static_artifact, self.animated_artifact
        if static is not None and static.kind is not MediaKind.STATIC:
            raise ValueError("static_artifact must be a static artifact")
        if animated is not None and animated.kind is not MediaKind.ANIMATED:
            raise ValueError("animated_artifact must be an animated artifact")
        if self.outcome is MediaOutcome.GIF:
            if static is None or animated is None:
                raise ValueError("a gif outcome needs both artifacts")
            if static.artifact_id == animated.artifact_id:
                raise ValueError("artifact ids must be distinct")
        elif self.outcome in {MediaOutcome.STATIC, MediaOutcome.STATIC_FALLBACK}:
            if static is None or animated is not None:
                raise ValueError("a static outcome needs only a static artifact")
        elif static is not None or animated is not None:
            raise ValueError("a text_only outcome must not have artifacts")
        if len(self.model_dump_json().encode("utf-8")) > MAX_MANIFEST_JSON_BYTES:
            raise ValueError("manifest JSON is too large")
        return self

    def artifact(self, artifact_id: str) -> MediaArtifact | None:
        for candidate in (self.static_artifact, self.animated_artifact):
            if candidate is not None and candidate.artifact_id == artifact_id:
                return candidate
        return None


class RenderMediaInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    brief: MediaBrief
    kind: MediaKind


def media_brief_hash(brief: MediaBrief) -> str:
    return sha256_hex(canonical_json(brief.model_dump(mode="json")))


def artifact_id_for(
    brief: MediaBrief, kind: MediaKind, renderer_name: str, renderer_version: str
) -> str:
    payload = {
        "brief": brief.model_dump(mode="json"),
        "kind": kind.value,
        "renderer": renderer_name,
        "version": renderer_version,
    }
    return "art-v1-" + sha256_hex(canonical_json(payload))[:16]
