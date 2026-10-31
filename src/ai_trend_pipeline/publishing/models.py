"""Pure, frozen story-publication contracts. No HTTP, clock or file I/O."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    UrlConstraints,
    field_validator,
    model_validator,
)

from ai_trend_pipeline.media.models import (
    MAX_MEDIA_STORY_ID_CHARS,
    SHA256_PATTERN,
    MediaArtifact,
    MediaType,
)
from ai_trend_pipeline.sources.models import ensure_aware_utc
from ai_trend_pipeline.story_drafting.models import StoryPackage

MAX_FACETS = 4
MAX_FACET_URL_CHARS = 1024
MAX_RESULT_FIELD_CHARS = 512

FacetUrl = Annotated[HttpUrl, UrlConstraints(max_length=MAX_FACET_URL_CHARS)]


class DestinationName(StrEnum):
    BLUESKY = "bluesky"


class PublicationErrorCode(StrEnum):
    """Values double as `ApplicationError.type`."""

    CONTENT_HASH_MISMATCH = "ContentHashMismatch"
    MANIFEST_MISMATCH = "ManifestMismatch"
    POST_TEXT_TOO_LONG = "PostTextTooLong"
    ALT_TEXT_TOO_LONG = "AltTextTooLong"
    MEDIA_NOT_SUPPORTED = "MediaNotSupported"
    MEDIA_TOO_LARGE = "MediaTooLarge"
    MEDIA_MISSING = "PublicationMediaMissing"
    ARTIFACT_HASH_MISMATCH = "ArtifactHashMismatch"
    AUTH_FAILED = "DestinationAuthFailed"
    RATE_LIMITED = "DestinationRateLimited"
    UNAVAILABLE = "DestinationUnavailable"
    REJECTED = "DestinationRejected"
    UNEXPECTED_RESPONSE = "UnexpectedDestinationResponse"


RETRYABLE_CODES: frozenset[PublicationErrorCode] = frozenset(
    {
        PublicationErrorCode.RATE_LIMITED,
        PublicationErrorCode.UNAVAILABLE,
        PublicationErrorCode.UNEXPECTED_RESPONSE,
    }
)


class DestinationCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    destination: DestinationName
    max_text_chars: int = Field(ge=1)
    max_text_bytes: int = Field(ge=1)
    max_images: int = Field(ge=0)
    static_media_types: tuple[MediaType, ...]
    max_static_bytes: int = Field(ge=0)
    supports_animation: bool
    animated_media_types: tuple[MediaType, ...]
    max_animated_bytes: int = Field(ge=0)
    max_alt_text_chars: int = Field(ge=1)
    supports_link_facets: bool


class LinkFacet(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    byte_start: int = Field(ge=0)
    byte_end: int
    uri: FacetUrl

    @model_validator(mode="after")
    def _check_range(self) -> "LinkFacet":
        if self.byte_end <= self.byte_start:
            raise ValueError("byte_end must be greater than byte_start")
        return self


class PublicationMedia(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact: MediaArtifact
    alt_text: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check_alt(self) -> "PublicationMedia":
        if self.alt_text != self.artifact.alt_text:
            raise ValueError("alt_text must equal the artifact alt text")
        return self


def _empty_facets() -> list[LinkFacet]:
    return []


class PublicationPackage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    destination: DestinationName
    story_id: str = Field(min_length=1, max_length=MAX_MEDIA_STORY_ID_CHARS)
    content_hash: str = Field(pattern=SHA256_PATTERN)
    text: str = Field(min_length=1)
    facets: list[LinkFacet] = Field(default_factory=_empty_facets, max_length=MAX_FACETS)
    media: PublicationMedia | None = None

    @model_validator(mode="after")
    def _check_facets(self) -> "PublicationPackage":
        size = len(self.text.encode("utf-8"))
        previous_end = 0
        for facet in sorted(self.facets, key=lambda item: item.byte_start):
            if facet.byte_end > size:
                raise ValueError("facet lies outside the text")
            if facet.byte_start < previous_end:
                raise ValueError("facets must not overlap")
            previous_end = facet.byte_end
        return self


class PublicationInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    destination: DestinationName
    story_id: str = Field(min_length=1, max_length=MAX_MEDIA_STORY_ID_CHARS)
    candidate_id: str = Field(min_length=1, max_length=MAX_MEDIA_STORY_ID_CHARS)
    story_created_at: datetime
    requested_at: datetime
    package: StoryPackage
    content_hash: str = Field(pattern=SHA256_PATTERN)

    @field_validator("story_created_at", "requested_at", mode="after")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return ensure_aware_utc(value)

    @model_validator(mode="after")
    def _check_story(self) -> "PublicationInput":
        if self.package.story_id != self.story_id:
            raise ValueError("package.story_id must equal story_id")
        return self


class PublishedMediaKind(StrEnum):
    NONE = "none"
    STATIC = "static"
    ANIMATED = "animated"


class PublicationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    destination: DestinationName
    story_id: str = Field(min_length=1, max_length=MAX_MEDIA_STORY_ID_CHARS)
    candidate_id: str = Field(min_length=1, max_length=MAX_MEDIA_STORY_ID_CHARS)
    story_created_at: datetime
    content_hash: str = Field(pattern=SHA256_PATTERN)
    idempotency_key: str = Field(min_length=1, max_length=256)
    uri: str = Field(min_length=1, max_length=MAX_RESULT_FIELD_CHARS)
    cid: str = Field(min_length=1, max_length=MAX_RESULT_FIELD_CHARS)
    rkey: str = Field(min_length=1, max_length=MAX_RESULT_FIELD_CHARS)
    public_url: str = Field(min_length=1, max_length=MAX_RESULT_FIELD_CHARS)
    published_at: datetime
    media_kind: PublishedMediaKind
    artifact_id: str | None = None
    already_published: bool

    @field_validator("story_created_at", "published_at", mode="after")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return ensure_aware_utc(value)


def publication_key(story_id: str, destination: DestinationName, content_hash: str) -> str:
    """Workflow ID and idempotency key: one approved content hash maps to one key."""
    return f"publish-v1-{story_id}-{destination.value}-{content_hash}"
