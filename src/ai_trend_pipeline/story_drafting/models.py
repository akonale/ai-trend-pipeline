"""Frozen, bounded contracts for story drafting and critique.

Imports only the standard library plus the 050 research and 051 analysis contracts: no I/O,
settings, Anthropic or workflow code. The tool-facing models (`StoryDraftContent`,
`CritiqueAssessment`) deliberately omit story IDs and schema versions so the model can never set
them; the activities derive those values in code.
"""

import re
from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from ai_trend_pipeline.analysis.models import (
    FACT_ID_PATTERN,
    MAX_FACT_ID_CHARS,
    MAX_YEAR,
    MIN_YEAR,
    FactSet,
    FactSetStatus,
)
from ai_trend_pipeline.media.models import MediaManifest
from ai_trend_pipeline.research.models import (
    MAX_DATASET_ROWS,
    MAX_DATASET_SLUG_CHARS,
    MAX_DATASET_TITLE_CHARS,
    MAX_EVIDENCE_EXCERPT_CHARS,
    MAX_EVIDENCE_ID_CHARS,
    MAX_HOST_CHARS,
    MAX_STORY_ID_CHARS,
    MAX_TITLE_CHARS,
    EvidenceKind,
    EvidenceSource,
    ResearchOutput,
    ResearchStatus,
    StoryInput,
)
from ai_trend_pipeline.sources.models import HttpUrl1024, ensure_aware_utc

STORY_PACKAGE_SCHEMA_VERSION = 1
CRITIQUE_SCHEMA_VERSION = 1
MAX_HEADLINE_CHARS = 160
MAX_SUMMARY_CHARS = 600
MAX_BODY_CHARS = 6_000
MAX_KEY_POINTS = 5
MAX_KEY_POINT_CHARS = 400
MAX_KEY_POINT_FACT_IDS = 4
MAX_SOURCE_LINKS = 4
MAX_SOURCE_LABEL_CHARS = 120
MAX_CAVEATS = 5
MAX_CAVEAT_CHARS = 300
MAX_POST_TEXT_CHARS = 300
MAX_CONTEXTS = 3
MAX_DRAFT_EVIDENCE = 4
MAX_CONTEXT_CHARS_PER_ITEM = 4_000
MAX_DRAFT_CONTEXT_CHARS = 8_000
# Ceiling for the JSON-rendered maximal bounded DraftInput, not a fixture size.
MAX_DRAFT_PROMPT_CHARS = 100_000
MAX_CRITIQUE_FINDINGS = 8
MAX_FINDING_CHARS = 300
MAX_REVISION_GUIDANCE_CHARS = 600
MAX_REVISION_ROUNDS = 1
MAX_DRAFT_INPUT_BYTES_TARGET = 96 * 1024
MAX_PACKAGE_BYTES_TARGET = 32 * 1024
MAX_DRAFT_TOKENS = 4_096
MAX_CRITIQUE_TOKENS = 2_048
TOOL_NAME_DRAFT = "draft_story"
TOOL_NAME_CRITIQUE = "critique_story"
STORY_DRAFT_TOOL_MISSING_ERROR_TYPE = "StoryDraftToolMissing"
STORY_DRAFT_INVALID_ERROR_TYPE = "StoryDraftInvalid"
STORY_CRITIQUE_TOOL_MISSING_ERROR_TYPE = "StoryCritiqueToolMissing"
STORY_CRITIQUE_INVALID_ERROR_TYPE = "StoryCritiqueInvalid"

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_BODY_CONTROL_CHARS = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")
_URL_MARKER = re.compile(r"(?i)(?:https?://|www\.)")


class CritiqueCategory(StrEnum):
    UNSUPPORTED_CLAIM = "unsupported_claim"
    NUMERICAL_FIDELITY = "numerical_fidelity"
    HEADLINE_OVERSTATEMENT = "headline_overstatement"
    MISSING_CAVEAT = "missing_caveat"
    SOURCE_FIDELITY = "source_fidelity"
    DATE_FIDELITY = "date_fidelity"
    MISSING_CITATION = "missing_citation"


class CritiqueSeverity(StrEnum):
    BLOCKING = "blocking"
    ADVISORY = "advisory"


class CritiqueLocation(StrEnum):
    HEADLINE = "headline"
    SUMMARY = "summary"
    BODY = "body"
    KEY_POINT = "key_point"
    SOURCE_LINKS = "source_links"
    CAVEATS = "caveats"
    POST_TEXT = "post_text"


class DraftNoteCode(StrEnum):
    CONTEXT_TRUNCATED = "context_truncated"
    ARTICLE_ONLY = "article_only"
    NO_FACTS_CITED = "no_facts_cited"


def _empty_strings() -> list[str]:
    return []


def _empty_key_points() -> list["StoryKeyPoint"]:
    return []


def _empty_source_links() -> list["StorySourceLink"]:
    return []


def _empty_findings() -> list["CritiqueFinding"]:
    return []


def _empty_contexts() -> list["DraftContext"]:
    return []


def _empty_evidence() -> list["DraftEvidence"]:
    return []


def _empty_notes() -> list[DraftNoteCode]:
    return []


def _clean_collapsed(value: object) -> object:
    if not isinstance(value, str):
        return value
    without_controls = _CONTROL_CHARS.sub(" ", value)
    return _WHITESPACE.sub(" ", without_controls).strip()


def _clean_body(value: object) -> object:
    if not isinstance(value, str):
        return value
    return _BODY_CONTROL_CHARS.sub("", value).strip()


def _reject_url(value: object) -> object:
    if isinstance(value, str) and _URL_MARKER.search(value):
        raise ValueError("free text must not contain URLs")
    return value


def _clean_prose(value: object) -> object:
    return _reject_url(_clean_collapsed(value))


def _clean_optional_prose(value: object) -> object:
    cleaned = _clean_collapsed(value)
    if isinstance(cleaned, str) and not cleaned:
        return None
    return _reject_url(cleaned)


FactIdText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_FACT_ID_CHARS,
        pattern=FACT_ID_PATTERN,
    ),
]
CaveatText = Annotated[str, StringConstraints(min_length=1, max_length=MAX_CAVEAT_CHARS)]


class StoryKeyPoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1, max_length=MAX_KEY_POINT_CHARS)
    fact_ids: list[FactIdText] = Field(
        default_factory=_empty_strings, max_length=MAX_KEY_POINT_FACT_IDS
    )

    @field_validator("text", mode="before")
    @classmethod
    def _clean_text(cls, value: object) -> object:
        return _clean_prose(value)

    @field_validator("fact_ids", mode="after")
    @classmethod
    def _unique_fact_ids(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("fact_ids must be unique")
        return value


class StorySourceLink(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str = Field(min_length=1, max_length=MAX_EVIDENCE_ID_CHARS)
    label: str = Field(min_length=1, max_length=MAX_SOURCE_LABEL_CHARS)
    url: HttpUrl1024

    @field_validator("label", mode="before")
    @classmethod
    def _clean_label(cls, value: object) -> object:
        return _clean_prose(value)


def _unique_evidence_ids(links: list[StorySourceLink]) -> list[StorySourceLink]:
    seen: set[str] = set()
    for link in links:
        if link.evidence_id in seen:
            raise ValueError("source_links evidence_id values must be unique")
        seen.add(link.evidence_id)
    return links


class StoryDraftContent(BaseModel):
    """The writer tool input schema: prose only, with no IDs or versions the model controls."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    headline: str = Field(min_length=1, max_length=MAX_HEADLINE_CHARS)
    summary: str = Field(min_length=1, max_length=MAX_SUMMARY_CHARS)
    body: str = Field(min_length=1, max_length=MAX_BODY_CHARS)
    key_points: list[StoryKeyPoint] = Field(
        default_factory=_empty_key_points, max_length=MAX_KEY_POINTS
    )
    source_links: list[StorySourceLink] = Field(
        default_factory=_empty_source_links, max_length=MAX_SOURCE_LINKS
    )
    caveats: list[CaveatText] = Field(default_factory=_empty_strings, max_length=MAX_CAVEATS)
    post_text: str | None = Field(default=None, min_length=1, max_length=MAX_POST_TEXT_CHARS)

    @field_validator("headline", "summary", mode="before")
    @classmethod
    def _clean_prose_fields(cls, value: object) -> object:
        return _clean_prose(value)

    @field_validator("body", mode="before")
    @classmethod
    def _clean_body_field(cls, value: object) -> object:
        return _reject_url(_clean_body(value))

    @field_validator("caveats", mode="before")
    @classmethod
    def _clean_caveats(cls, value: object) -> object:
        if isinstance(value, list):
            return [_clean_prose(item) for item in cast(list[object], value)]
        return value

    @field_validator("post_text", mode="before")
    @classmethod
    def _clean_post_text(cls, value: object) -> object:
        return _clean_optional_prose(value)

    @field_validator("source_links", mode="after")
    @classmethod
    def _unique_links(cls, value: list[StorySourceLink]) -> list[StorySourceLink]:
        return _unique_evidence_ids(value)


class StoryPackage(StoryDraftContent):
    """The versioned result; the activity sets `story_id` and `schema_version` in code."""

    story_id: str = Field(min_length=1, max_length=MAX_STORY_ID_CHARS)
    schema_version: int = Field(default=STORY_PACKAGE_SCHEMA_VERSION, ge=1)
    media: MediaManifest | None = None


class CritiqueFinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    category: CritiqueCategory
    severity: CritiqueSeverity
    message: str = Field(min_length=1, max_length=MAX_FINDING_CHARS)
    location: CritiqueLocation | None = None
    fact_ids: list[FactIdText] = Field(
        default_factory=_empty_strings, max_length=MAX_KEY_POINT_FACT_IDS
    )

    @field_validator("message", mode="before")
    @classmethod
    def _clean_message(cls, value: object) -> object:
        return _clean_prose(value)

    @field_validator("fact_ids", mode="after")
    @classmethod
    def _unique_fact_ids(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("fact_ids must be unique")
        return value


def _check_critique_invariants(
    passed: bool, findings: list[CritiqueFinding], revision_guidance: str | None
) -> None:
    blocking = any(finding.severity is CritiqueSeverity.BLOCKING for finding in findings)
    if passed and blocking:
        raise ValueError("a passed critique must not contain blocking findings")
    if not passed:
        if not blocking:
            raise ValueError("a failed critique needs at least one blocking finding")
        if revision_guidance is None:
            raise ValueError("a failed critique needs revision guidance")


class CritiqueAssessment(BaseModel):
    """The checker tool input schema: no story ID or schema version."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    passed: bool
    findings: list[CritiqueFinding] = Field(
        default_factory=_empty_findings, max_length=MAX_CRITIQUE_FINDINGS
    )
    revision_guidance: str | None = Field(
        default=None, min_length=1, max_length=MAX_REVISION_GUIDANCE_CHARS
    )

    @field_validator("revision_guidance", mode="before")
    @classmethod
    def _clean_guidance(cls, value: object) -> object:
        return _clean_optional_prose(value)

    @model_validator(mode="after")
    def _check_invariants(self) -> "CritiqueAssessment":
        _check_critique_invariants(self.passed, self.findings, self.revision_guidance)
        return self


class CritiqueOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    story_id: str = Field(min_length=1, max_length=MAX_STORY_ID_CHARS)
    schema_version: int = Field(default=CRITIQUE_SCHEMA_VERSION, ge=1)
    passed: bool
    findings: list[CritiqueFinding] = Field(
        default_factory=_empty_findings, max_length=MAX_CRITIQUE_FINDINGS
    )
    revision_guidance: str | None = Field(
        default=None, min_length=1, max_length=MAX_REVISION_GUIDANCE_CHARS
    )

    @field_validator("revision_guidance", mode="before")
    @classmethod
    def _clean_guidance(cls, value: object) -> object:
        return _clean_optional_prose(value)

    @model_validator(mode="after")
    def _check_invariants(self) -> "CritiqueOutput":
        _check_critique_invariants(self.passed, self.findings, self.revision_guidance)
        return self


class DraftContext(BaseModel):
    """Compact copy of a 050 `StoryContext` for the writer prompt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    context_id: str = Field(min_length=1, max_length=80)
    title: str | None = Field(default=None, min_length=1, max_length=MAX_TITLE_CHARS)
    publisher: str = Field(min_length=1, max_length=MAX_HOST_CHARS)
    url: HttpUrl1024
    text: str = Field(min_length=1, max_length=MAX_CONTEXT_CHARS_PER_ITEM)
    retrieved_at: datetime

    @field_validator("text", mode="before")
    @classmethod
    def _strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("retrieved_at", mode="after")
    @classmethod
    def _to_utc(cls, value: datetime) -> datetime:
        return ensure_aware_utc(value)


class DraftEvidence(BaseModel):
    """Compact copy of a 050 `EvidenceSource`; dataset rows are never carried."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str = Field(min_length=1, max_length=MAX_EVIDENCE_ID_CHARS)
    kind: EvidenceKind
    title: str = Field(min_length=1, max_length=MAX_TITLE_CHARS)
    url: HttpUrl1024 | None = None
    publisher: str | None = Field(default=None, min_length=1, max_length=MAX_HOST_CHARS)
    observation_date: date | None = None
    excerpt: str | None = Field(default=None, min_length=1, max_length=MAX_EVIDENCE_EXCERPT_CHARS)
    dataset_slug: str | None = Field(default=None, min_length=1, max_length=MAX_DATASET_SLUG_CHARS)
    dataset_title: str | None = Field(
        default=None, min_length=1, max_length=MAX_DATASET_TITLE_CHARS
    )
    dataset_since_year: int | None = Field(default=None, ge=MIN_YEAR, le=MAX_YEAR)
    dataset_row_count: int | None = Field(default=None, ge=0, le=MAX_DATASET_ROWS)

    @model_validator(mode="after")
    def _check_kind(self) -> "DraftEvidence":
        if self.kind is EvidenceKind.ARTICLE:
            if self.url is None or self.excerpt is None:
                raise ValueError("article evidence requires url and excerpt")
            if (
                self.dataset_slug is not None
                or self.dataset_title is not None
                or self.dataset_since_year is not None
                or self.dataset_row_count is not None
            ):
                raise ValueError("article evidence must not carry dataset fields")
        else:
            if (
                self.dataset_slug is None
                or self.dataset_title is None
                or self.dataset_row_count is None
            ):
                raise ValueError("dataset evidence requires slug, title and row count")
            if self.url is not None or self.excerpt is not None:
                raise ValueError("dataset evidence must not carry url or excerpt")
        return self


class RevisionRequest(BaseModel):
    """The single bounded revision: exactly one round, always after a failed critique."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    prior_draft: StoryPackage
    critique: CritiqueOutput
    round: int = Field(ge=1, le=MAX_REVISION_ROUNDS)

    @model_validator(mode="after")
    def _check_revision(self) -> "RevisionRequest":
        if self.prior_draft.story_id != self.critique.story_id:
            raise ValueError("prior_draft and critique must share a story_id")
        if self.critique.passed:
            raise ValueError("a revision needs a failed critique")
        if not any(
            finding.severity is CritiqueSeverity.BLOCKING for finding in self.critique.findings
        ):
            raise ValueError("a revision needs at least one blocking finding")
        if not self.critique.revision_guidance:
            raise ValueError("a revision needs non-empty revision guidance")
        return self


class DraftInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    story: StoryInput
    facts: FactSet
    contexts: list[DraftContext] = Field(default_factory=_empty_contexts, max_length=MAX_CONTEXTS)
    evidence: list[DraftEvidence] = Field(
        default_factory=_empty_evidence, max_length=MAX_DRAFT_EVIDENCE
    )
    context_truncated: bool = False
    revision: RevisionRequest | None = None

    @field_validator("contexts", mode="after")
    @classmethod
    def _check_contexts(cls, value: list[DraftContext]) -> list[DraftContext]:
        ids = [context.context_id for context in value]
        if len(set(ids)) != len(ids):
            raise ValueError("context_id values must be unique")
        if sum(len(context.text) for context in value) > MAX_DRAFT_CONTEXT_CHARS:
            raise ValueError(
                f"context text must total at most {MAX_DRAFT_CONTEXT_CHARS} characters"
            )
        return value

    @field_validator("evidence", mode="after")
    @classmethod
    def _check_evidence(cls, value: list[DraftEvidence]) -> list[DraftEvidence]:
        ids = [item.evidence_id for item in value]
        if len(set(ids)) != len(ids):
            raise ValueError("evidence_id values must be unique")
        return value

    @model_validator(mode="after")
    def _check_input(self) -> "DraftInput":
        if self.facts.story_id != self.story.candidate_id:
            raise ValueError("facts.story_id must equal story.candidate_id")
        if self.facts.status is FactSetStatus.UNSUPPORTED:
            raise ValueError("unsupported facts cannot produce a story draft")
        if not self.contexts:
            raise ValueError("a draft needs at least one article context")
        if not any(item.kind is EvidenceKind.ARTICLE for item in self.evidence):
            raise ValueError("a draft needs at least one article evidence item")
        return self


class DraftOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    story_id: str = Field(min_length=1, max_length=MAX_STORY_ID_CHARS)
    draft: StoryPackage
    revision_applied: bool
    notes: list[DraftNoteCode] = Field(default_factory=_empty_notes, max_length=4)

    @field_validator("notes", mode="after")
    @classmethod
    def _unique_notes(cls, value: list[DraftNoteCode]) -> list[DraftNoteCode]:
        if len(set(value)) != len(value):
            raise ValueError("notes must be unique")
        return value

    @model_validator(mode="after")
    def _check_story(self) -> "DraftOutput":
        if self.draft.story_id != self.story_id:
            raise ValueError("draft.story_id must equal story_id")
        return self


class CritiqueInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    draft_input: DraftInput
    draft: StoryPackage
    round: int = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _check_story(self) -> "CritiqueInput":
        if self.draft.story_id != self.draft_input.story.candidate_id:
            raise ValueError("draft.story_id must equal the candidate_id")
        return self


def _draft_evidence(item: EvidenceSource) -> DraftEvidence:
    if item.kind is EvidenceKind.ARTICLE:
        return DraftEvidence(
            evidence_id=item.evidence_id,
            kind=item.kind,
            title=item.title,
            url=item.url,
            publisher=item.publisher,
            observation_date=item.observation_date,
            excerpt=item.excerpt,
            dataset_slug=None,
            dataset_title=None,
            dataset_since_year=None,
            dataset_row_count=None,
        )
    dataset = item.dataset
    if dataset is None:
        raise ValueError("dataset evidence is missing its dataset")
    return DraftEvidence(
        evidence_id=item.evidence_id,
        kind=item.kind,
        title=item.title,
        url=None,
        publisher=item.publisher,
        observation_date=item.observation_date,
        excerpt=None,
        dataset_slug=dataset.slug,
        dataset_title=dataset.title,
        dataset_since_year=dataset.since_year,
        dataset_row_count=len(dataset.rows),
    )


def draft_input_for(story: StoryInput, research: ResearchOutput, facts: FactSet) -> DraftInput:
    """Build the only supported bounded `DraftInput` from 050 research and a 051 `FactSet`."""
    if research.status is not ResearchStatus.SUPPORTED:
        raise ValueError("research must be supported to draft a story")
    if facts.status is FactSetStatus.UNSUPPORTED:
        raise ValueError("unsupported facts cannot produce a story draft")
    if not (research.story_id == facts.story_id == story.candidate_id):
        raise ValueError("story, research and facts must share the same story_id")

    contexts: list[DraftContext] = []
    total_chars = 0
    truncated = False
    for context in research.contexts:
        if len(contexts) >= MAX_CONTEXTS:
            truncated = True
            break
        text = context.text[:MAX_CONTEXT_CHARS_PER_ITEM]
        if len(text) < len(context.text):
            truncated = True
        if total_chars + len(text) > MAX_DRAFT_CONTEXT_CHARS:
            truncated = True
            break
        contexts.append(
            DraftContext(
                context_id=context.context_id,
                title=context.title,
                publisher=context.publisher,
                url=context.url,
                text=text,
                retrieved_at=context.retrieved_at,
            )
        )
        total_chars += len(text)

    evidence: list[DraftEvidence] = []
    seen: set[str] = set()
    for item in research.evidence:
        if item.evidence_id in seen:
            continue
        if len(evidence) >= MAX_DRAFT_EVIDENCE:
            break
        seen.add(item.evidence_id)
        evidence.append(_draft_evidence(item))

    return DraftInput(
        story=story,
        facts=facts,
        contexts=contexts,
        evidence=evidence,
        context_truncated=truncated,
        revision=None,
    )
