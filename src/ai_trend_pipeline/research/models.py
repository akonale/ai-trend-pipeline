"""Frozen, bounded contracts for story research and evidence.

Imports only the shared source models and the standard library: no I/O, no settings and no
workflow code, so `config.py` can import the host type cheaply.
"""

import hashlib
import ipaddress
import re
from datetime import date, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    field_validator,
    model_validator,
)

from ai_trend_pipeline.sources.models import (
    HttpUrl1024,
    MetricKey,
    MetricValue,
    SourceName,
    ensure_aware_utc,
)

MAX_CONTEXTS = 3
MAX_EVIDENCE = 4
MAX_CONTEXT_TEXT_CHARS = 8_000
MAX_TOTAL_CONTEXT_CHARS = 16_000
MIN_CONTEXT_TEXT_CHARS = 300
MAX_DATASET_ROWS = 300
MAX_DATASET_COUNTRIES = 20
MAX_EXTRA_CONTEXT_URLS = 2
MAX_EVIDENCE_EXCERPT_CHARS = 500
MAX_FAILURES = 8
MAX_NOTES = 8
MAX_FAILURE_MESSAGE_CHARS = 200
MAX_HOST_CHARS = 253
MAX_TITLE_CHARS = 300
ALLOWED_CONTENT_TYPES = frozenset({"text/html", "text/plain", "application/xhtml+xml"})
ALLOWED_PORTS = frozenset({80, 443})
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
MAX_RESEARCH_OUTPUT_BYTES_TARGET = 96 * 1024
MAX_APPROVED_HOSTS = 50
MAX_DATASET_NOTES = 5
MAX_DATASET_NOTE_CHARS = 200
MAX_STORY_METRICS = 4
MAX_CONTEXT_ID_CHARS = 80
MAX_EVIDENCE_ID_CHARS = 80
MAX_STORY_ID_CHARS = 64
MAX_CANDIDATE_ID_CHARS = 64
MAX_EXTERNAL_ID_CHARS = 128
MAX_STORY_TITLE_CHARS = 200
MAX_STORY_TEXT_CHARS = 500
MAX_CONTENT_TYPE_CHARS = 100
MAX_DATASET_SLUG_CHARS = 100
MAX_DATASET_TITLE_CHARS = 200
MAX_VALUE_COLUMN_CHARS = 100
MAX_ENTITY_CHARS = 100
MAX_CODE_CHARS = 20
MIN_YEAR = 1800
MAX_YEAR = 2100

_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa", ".lan")
_HOST_FORBIDDEN = set("/:@?#[] ")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def normalize_host_name(value: str) -> str:
    """Lowercase and validate a bare hostname; reject schemes, ports, paths and IP literals."""
    host = value.strip().lower()
    if not host:
        raise ValueError("host must not be empty")
    if len(host) > MAX_HOST_CHARS:
        raise ValueError(f"host must be at most {MAX_HOST_CHARS} characters")
    if any(character in _HOST_FORBIDDEN for character in host):
        raise ValueError("host must not contain a scheme, port, path, userinfo or brackets")
    if host.startswith(".") or host.endswith("."):
        raise ValueError("host must not start or end with a dot")
    if host == "localhost" or host.endswith(_LOCAL_SUFFIXES):
        raise ValueError("host must not be a local or internal name")
    if "." not in host:
        raise ValueError("host must be a fully-qualified domain name")
    labels = host.split(".")
    for label in labels:
        if not label or len(label) > 63:
            raise ValueError("host labels must be 1-63 characters")
        if label.startswith("-") or label.endswith("-"):
            raise ValueError("host labels must not start or end with a hyphen")
        if not all(character.isalnum() or character == "-" for character in label):
            raise ValueError("host contains invalid characters")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host
    raise ValueError("host must not be an IP literal")


HostName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_HOST_CHARS),
    AfterValidator(normalize_host_name),
]


class ResearchStatus(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"


class EvidenceKind(StrEnum):
    ARTICLE = "article"
    DATASET = "dataset"


class ResearchFailureCode(StrEnum):
    NO_SOURCE_URL = "no_source_url"
    UNSAFE_URL = "unsafe_url"
    BLOCKED_NETWORK = "blocked_network"
    TOO_MANY_REDIRECTS = "too_many_redirects"
    MISSING_ARTICLE = "missing_article"
    FETCH_FAILED = "fetch_failed"
    UNSUPPORTED_CONTENT_TYPE = "unsupported_content_type"
    MALFORMED_CONTENT = "malformed_content"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ResearchNoteCode(StrEnum):
    NO_CATALOG_MATCH = "no_catalog_match"
    OWID_DISABLED = "owid_disabled"
    DATASET_UNAVAILABLE = "dataset_unavailable"
    DATASET_EMPTY = "dataset_empty"
    DATASET_TRUNCATED = "dataset_truncated"
    RESPONSE_TRUNCATED = "response_truncated"
    EXTRA_CONTEXT_NOT_ALLOWED = "extra_context_not_allowed"
    EXTRA_CONTEXT_FETCH_FAILED = "extra_context_fetch_failed"
    DUPLICATE_CONTEXT = "duplicate_context"
    TEXT_BUDGET_EXHAUSTED = "text_budget_exhausted"


def context_id_for(url: str) -> str:
    """Stable `ctx-v1-<16 hex>` id derived from a normalized final URL."""
    return f"ctx-v1-{hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]}"


def evidence_id_for(kind: EvidenceKind, key: str) -> str:
    """Stable `ev-v1-<16 hex>` id derived from the evidence kind and its context id or slug."""
    seed = f"{kind.value}\x1f{key}"
    return f"ev-v1-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]}"


def _strip(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


def _aware(value: datetime | None) -> datetime | None:
    return None if value is None else ensure_aware_utc(value)


def _control_free(value: object) -> object:
    if not isinstance(value, str):
        return value
    return _CONTROL_CHARS.sub("", value).strip()


class StoryInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str = Field(min_length=1, max_length=MAX_CANDIDATE_ID_CHARS)
    source: SourceName
    external_id: str = Field(min_length=1, max_length=MAX_EXTERNAL_ID_CHARS)
    title: str = Field(min_length=1, max_length=MAX_STORY_TITLE_CHARS)
    url: HttpUrl1024 | None = None
    published_at: datetime | None = None
    observed_at: datetime
    summary: str = Field(min_length=1, max_length=MAX_STORY_TEXT_CHARS)
    proposed_angle: str = Field(min_length=1, max_length=MAX_STORY_TEXT_CHARS)
    selection_reason: str = Field(min_length=1, max_length=MAX_STORY_TEXT_CHARS)
    metrics: dict[MetricKey, MetricValue] = Field(
        default_factory=dict, max_length=MAX_STORY_METRICS
    )

    @field_validator(
        "candidate_id",
        "external_id",
        "title",
        "summary",
        "proposed_angle",
        "selection_reason",
        mode="before",
    )
    @classmethod
    def _strip_text(cls, value: object) -> object:
        return _strip(value)

    @field_validator("published_at", "observed_at", mode="after")
    @classmethod
    def _to_utc(cls, value: datetime | None) -> datetime | None:
        return _aware(value)


class DatasetRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entity: str = Field(min_length=1, max_length=MAX_ENTITY_CHARS)
    code: str | None = Field(default=None, min_length=1, max_length=MAX_CODE_CHARS)
    year: int = Field(ge=MIN_YEAR, le=MAX_YEAR)
    value: float = Field(allow_inf_nan=False)


def _empty_rows() -> list[DatasetRow]:
    return []


def _empty_strings() -> list[str]:
    return []


class DatasetEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: str = Field(min_length=1, max_length=MAX_DATASET_SLUG_CHARS)
    title: str = Field(min_length=1, max_length=MAX_DATASET_TITLE_CHARS)
    value_column: str = Field(min_length=1, max_length=MAX_VALUE_COLUMN_CHARS)
    since_year: int = Field(ge=MIN_YEAR, le=MAX_YEAR)
    rows: list[DatasetRow] = Field(default_factory=_empty_rows, max_length=MAX_DATASET_ROWS)
    matched_countries: list[str] = Field(
        default_factory=_empty_strings, max_length=MAX_DATASET_COUNTRIES
    )
    notes: list[str] = Field(default_factory=_empty_strings, max_length=MAX_DATASET_NOTES)
    truncated: bool = False

    @field_validator("matched_countries", mode="after")
    @classmethod
    def _bound_countries(cls, value: list[str]) -> list[str]:
        for country in value:
            if not 1 <= len(country) <= MAX_ENTITY_CHARS:
                raise ValueError(f"country names must be 1-{MAX_ENTITY_CHARS} characters")
        return value

    @field_validator("notes", mode="after")
    @classmethod
    def _bound_notes(cls, value: list[str]) -> list[str]:
        for note in value:
            if not 1 <= len(note) <= MAX_DATASET_NOTE_CHARS:
                raise ValueError(f"dataset notes must be 1-{MAX_DATASET_NOTE_CHARS} characters")
        return value


class StoryContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    context_id: str = Field(min_length=1, max_length=MAX_CONTEXT_ID_CHARS)
    requested_url: HttpUrl1024
    url: HttpUrl1024
    title: str | None = Field(default=None, min_length=1, max_length=MAX_TITLE_CHARS)
    publisher: str = Field(min_length=1, max_length=MAX_HOST_CHARS)
    content_type: str = Field(min_length=1, max_length=MAX_CONTENT_TYPE_CHARS)
    text: str = Field(min_length=1, max_length=MAX_CONTEXT_TEXT_CHARS)
    truncated: bool = False
    retrieved_at: datetime

    @field_validator("text", mode="before")
    @classmethod
    def _strip_text(cls, value: object) -> object:
        return _strip(value)

    @field_validator("retrieved_at", mode="after")
    @classmethod
    def _to_utc(cls, value: datetime) -> datetime:
        return ensure_aware_utc(value)


class EvidenceSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str = Field(min_length=1, max_length=MAX_EVIDENCE_ID_CHARS)
    kind: EvidenceKind
    title: str = Field(min_length=1, max_length=MAX_TITLE_CHARS)
    url: HttpUrl1024 | None = None
    publisher: str | None = Field(default=None, min_length=1, max_length=MAX_HOST_CHARS)
    retrieved_at: datetime
    observation_date: date | None = None
    context_id: str | None = Field(default=None, min_length=1, max_length=MAX_CONTEXT_ID_CHARS)
    excerpt: str | None = Field(default=None, min_length=1, max_length=MAX_EVIDENCE_EXCERPT_CHARS)
    dataset: DatasetEvidence | None = None

    @field_validator("retrieved_at", mode="after")
    @classmethod
    def _to_utc(cls, value: datetime) -> datetime:
        return ensure_aware_utc(value)

    @model_validator(mode="after")
    def _check_kind(self) -> "EvidenceSource":
        if self.kind is EvidenceKind.ARTICLE:
            if self.context_id is None:
                raise ValueError("article evidence requires context_id")
            if self.dataset is not None:
                raise ValueError("article evidence must not carry a dataset")
        else:
            if self.dataset is None:
                raise ValueError("dataset evidence requires dataset")
            if self.context_id is not None:
                raise ValueError("dataset evidence must not carry context_id")
        return self


class ResearchFailure(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: ResearchFailureCode
    message: str = Field(min_length=1, max_length=MAX_FAILURE_MESSAGE_CHARS)
    host: HostName | None = None

    @field_validator("message", mode="before")
    @classmethod
    def _clean_message(cls, value: object) -> object:
        return _control_free(value)


def _empty_urls() -> list[HttpUrl]:
    return []


class ResearchInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    story: StoryInput
    extra_context_urls: list[HttpUrl1024] = Field(
        default_factory=_empty_urls, max_length=MAX_EXTRA_CONTEXT_URLS
    )
    include_owid: bool = True

    @field_validator("extra_context_urls", mode="after")
    @classmethod
    def _unique_urls(cls, value: list[HttpUrl]) -> list[HttpUrl]:
        if len({str(url) for url in value}) != len(value):
            raise ValueError("extra_context_urls must be unique")
        return value


def _empty_contexts() -> list[StoryContext]:
    return []


def _empty_evidence() -> list[EvidenceSource]:
    return []


def _empty_failures() -> list[ResearchFailure]:
    return []


def _empty_notes() -> list[ResearchNoteCode]:
    return []


class ResearchOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    story_id: str = Field(min_length=1, max_length=MAX_STORY_ID_CHARS)
    status: ResearchStatus
    contexts: list[StoryContext] = Field(default_factory=_empty_contexts, max_length=MAX_CONTEXTS)
    evidence: list[EvidenceSource] = Field(default_factory=_empty_evidence, max_length=MAX_EVIDENCE)
    failures: list[ResearchFailure] = Field(
        default_factory=_empty_failures, max_length=MAX_FAILURES
    )
    notes: list[ResearchNoteCode] = Field(default_factory=_empty_notes, max_length=MAX_NOTES)
    researched_at: datetime

    @field_validator("researched_at", mode="after")
    @classmethod
    def _to_utc(cls, value: datetime) -> datetime:
        return ensure_aware_utc(value)

    @model_validator(mode="after")
    def _check_invariants(self) -> "ResearchOutput":
        context_ids = [context.context_id for context in self.contexts]
        if len(set(context_ids)) != len(context_ids):
            raise ValueError("context_id values must be unique")
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("evidence_id values must be unique")
        if sum(len(context.text) for context in self.contexts) > MAX_TOTAL_CONTEXT_CHARS:
            raise ValueError(
                f"context text must total at most {MAX_TOTAL_CONTEXT_CHARS} characters"
            )
        for item in self.evidence:
            if item.kind is EvidenceKind.ARTICLE and item.context_id not in context_ids:
                raise ValueError("article evidence must point at one of the contexts")
        if self.status is ResearchStatus.SUPPORTED:
            if not any(len(context.text) >= MIN_CONTEXT_TEXT_CHARS for context in self.contexts):
                raise ValueError(
                    f"supported research needs an article context of at least "
                    f"{MIN_CONTEXT_TEXT_CHARS} characters"
                )
        elif not self.failures:
            raise ValueError("unsupported research needs at least one failure")
        return self


class FetchContextInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    url: HttpUrl1024
    approved_hosts: frozenset[HostName] | None = Field(default=None, max_length=MAX_APPROVED_HOSTS)


class FetchContextOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    requested_url: HttpUrl1024
    context: StoryContext | None = None
    failure: ResearchFailure | None = None
    notes: list[ResearchNoteCode] = Field(default_factory=_empty_notes, max_length=MAX_NOTES)

    @model_validator(mode="after")
    def _exactly_one(self) -> "FetchContextOutput":
        if (self.context is None) == (self.failure is None):
            raise ValueError("exactly one of context or failure is required")
        return self


class MatchEvidenceInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    story: StoryInput
    include_owid: bool = True


class MatchEvidenceOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence: EvidenceSource | None = None
    notes: list[ResearchNoteCode] = Field(default_factory=_empty_notes, max_length=MAX_NOTES)
