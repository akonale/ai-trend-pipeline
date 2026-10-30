"""Frozen, bounded contracts for deterministic facts derived from dataset evidence.

Imports only the standard library plus the research and sources models: no I/O, no settings, no
workflow code, so importing the package stays cheap and side-effect free.
"""

import hashlib
import json
import re
from collections.abc import Sequence
from datetime import date
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

from ai_trend_pipeline.research.models import (
    EvidenceKind,
    EvidenceSource,
    ResearchStatus,
    StoryInput,
)
from ai_trend_pipeline.sources.models import HttpUrl1024

FACT_SCHEMA_VERSION = 1
MAX_FACTS = 32
MAX_SERIES_ENTITIES = 4
MAX_FACT_ENTITIES = 2
MAX_LINEAGE_ENTITIES = 20
MAX_SOURCES_PER_FACT = 4
MAX_CAVEATS = 8
MAX_FACT_NOTES = 10
MAX_EVIDENCE = 4
MIN_RANKING_ENTITIES = 3
MIN_OUTLIER_ENTITIES = 5
MAX_OUTLIER_FACTS = 2
IQR_FACTOR = 1.5
MAX_METRIC_CHARS = 200
MAX_ENTITY_CHARS = 100
MAX_UNIT_CHARS = 40
MAX_FACT_ID_CHARS = 32
MAX_FACT_SET_ID_CHARS = 32
MAX_DISPLAY_LABEL_CHARS = 160
MAX_DISPLAY_VALUE_CHARS = 120
MAX_DISPLAY_SUBJECT_CHARS = 120
MAX_PERIOD_TEXT_CHARS = 40
MAX_FORMULA_CHARS = 200
MAX_PARAMETERS = 6
MAX_FACT_SET_BYTES_TARGET = 64 * 1024
FACT_ID_PATTERN = r"^fact-v1-[0-9a-f]{16}$"
FACT_SET_ID_PATTERN = r"^fset-v1-[0-9a-f]{16}$"

MIN_YEAR = 1800
MAX_YEAR = 2100
MAX_EVIDENCE_ID_CHARS = 80
MAX_DATASET_SLUG_CHARS = 100
MAX_VALUE_COLUMN_CHARS = 100
MAX_ROW_COUNT = 300
MAX_SOURCE_TITLE_CHARS = 200
MAX_PUBLISHER_CHARS = 253
MAX_STORY_ID_CHARS = 64
MAX_PARAMETER_KEY_CHARS = 40

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")

FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
EntityName = Annotated[str, StringConstraints(min_length=1, max_length=MAX_ENTITY_CHARS)]
EvidenceId = Annotated[str, StringConstraints(min_length=1, max_length=MAX_EVIDENCE_ID_CHARS)]
ParameterKey = Annotated[str, StringConstraints(min_length=1, max_length=MAX_PARAMETER_KEY_CHARS)]


class FactKind(StrEnum):
    FIRST = "first"
    LATEST = "latest"
    MIN = "min"
    MAX = "max"
    CHANGE = "change"
    PERCENT_CHANGE = "percent_change"
    COMPARISON = "comparison"
    RANKING = "ranking"
    OUTLIER = "outlier"


class FactMethod(StrEnum):
    SERIES_ENDPOINT = "series_endpoint"
    SERIES_EXTREME = "series_extreme"
    MAX_MIN = "max_min"
    ORDINAL = "ordinal"
    IQR_1_5 = "iqr_1_5"


class FactCaveatCode(StrEnum):
    SINGLE_OBSERVATION = "single_observation"
    PARTIAL_SERIES = "partial_series"
    DATASET_TRUNCATED = "dataset_truncated"
    DUPLICATE_OBSERVATIONS_COLLAPSED = "duplicate_observations_collapsed"
    NON_POSITIVE_BASELINE = "non_positive_baseline"
    TIED_VALUES = "tied_values"
    STATISTICAL_OUTLIER = "statistical_outlier"


class FactNoteCode(StrEnum):
    RESEARCH_UNSUPPORTED = "research_unsupported"
    NO_DATASET_EVIDENCE = "no_dataset_evidence"
    NO_NUMERIC_ROWS = "no_numeric_rows"
    ADDITIONAL_DATASET_IGNORED = "additional_dataset_ignored"
    DUPLICATE_OBSERVATIONS_COLLAPSED = "duplicate_observations_collapsed"
    INSUFFICIENT_SERIES = "insufficient_series"
    INSUFFICIENT_CROSS_ENTITY_DATA = "insufficient_cross_entity_data"
    NON_POSITIVE_BASELINE = "non_positive_baseline"
    STORY_ENTITIES_MATCHED = "story_entities_matched"
    FACT_BUDGET_EXHAUSTED = "fact_budget_exhausted"


class FactSetStatus(StrEnum):
    AVAILABLE = "available"
    EMPTY = "empty"
    UNSUPPORTED = "unsupported"


def _empty_facts() -> list["Fact"]:
    return []


def _empty_strings() -> list[str]:
    return []


def _empty_caveats() -> list[FactCaveatCode]:
    return []


def _empty_notes() -> list[FactNoteCode]:
    return []


def _empty_evidence() -> list[EvidenceSource]:
    return []


def _empty_parameters() -> dict[str, float]:
    return {}


def _clean_display(value: object, limit: int) -> object:
    if not isinstance(value, str):
        return value
    cleaned = _WHITESPACE.sub(" ", value)
    cleaned = _CONTROL_CHARS.sub("", cleaned).strip()
    return cleaned[:limit]


def _normalise_float(value: float) -> float:
    """Return `value`, normalising negative zero so serialization is byte-stable."""
    return 0.0 if value == 0 else value


class SourceReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str = Field(min_length=1, max_length=MAX_EVIDENCE_ID_CHARS)
    kind: EvidenceKind
    title: str = Field(min_length=1, max_length=MAX_SOURCE_TITLE_CHARS)
    url: HttpUrl1024 | None = None
    publisher: str | None = Field(default=None, min_length=1, max_length=MAX_PUBLISHER_CHARS)


class CalculationLineage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    calculation: FactKind
    input_evidence_ids: list[EvidenceId] = Field(min_length=1, max_length=MAX_SOURCES_PER_FACT)
    dataset_slug: str = Field(min_length=1, max_length=MAX_DATASET_SLUG_CHARS)
    value_column: str = Field(min_length=1, max_length=MAX_VALUE_COLUMN_CHARS)
    entities: list[EntityName] = Field(min_length=1, max_length=MAX_LINEAGE_ENTITIES)
    entity_count: int = Field(ge=1)
    start_year: int = Field(ge=MIN_YEAR, le=MAX_YEAR)
    end_year: int = Field(ge=MIN_YEAR, le=MAX_YEAR)
    baseline_year: int | None = Field(default=None, ge=MIN_YEAR, le=MAX_YEAR)
    baseline_value: FiniteFloat | None = None
    row_count: int = Field(ge=0, le=MAX_ROW_COUNT)
    formula: str = Field(min_length=1, max_length=MAX_FORMULA_CHARS)
    method: FactMethod
    parameters: dict[ParameterKey, FiniteFloat] = Field(
        default_factory=_empty_parameters, max_length=MAX_PARAMETERS
    )

    @model_validator(mode="after")
    def _check_bounds(self) -> "CalculationLineage":
        if self.end_year < self.start_year:
            raise ValueError("end_year must be >= start_year")
        if len(set(self.input_evidence_ids)) != len(self.input_evidence_ids):
            raise ValueError("input_evidence_ids must be unique")
        if len(set(self.entities)) != len(self.entities):
            raise ValueError("entities must be unique")
        return self


class FactDisplay(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str = Field(min_length=1, max_length=MAX_DISPLAY_LABEL_CHARS)
    value_text: str = Field(min_length=1, max_length=MAX_DISPLAY_VALUE_CHARS)
    subject: str = Field(min_length=1, max_length=MAX_DISPLAY_SUBJECT_CHARS)
    period_text: str = Field(min_length=1, max_length=MAX_PERIOD_TEXT_CHARS)

    @field_validator("label", mode="before")
    @classmethod
    def _clean_label(cls, value: object) -> object:
        return _clean_display(value, MAX_DISPLAY_LABEL_CHARS)

    @field_validator("value_text", mode="before")
    @classmethod
    def _clean_value_text(cls, value: object) -> object:
        return _clean_display(value, MAX_DISPLAY_VALUE_CHARS)

    @field_validator("subject", mode="before")
    @classmethod
    def _clean_subject(cls, value: object) -> object:
        return _clean_display(value, MAX_DISPLAY_SUBJECT_CHARS)

    @field_validator("period_text", mode="before")
    @classmethod
    def _clean_period_text(cls, value: object) -> object:
        return _clean_display(value, MAX_PERIOD_TEXT_CHARS)


class Fact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_id: str = Field(min_length=1, max_length=MAX_FACT_ID_CHARS, pattern=FACT_ID_PATTERN)
    schema_version: int = Field(default=FACT_SCHEMA_VERSION, ge=1)
    kind: FactKind
    entities: list[EntityName] = Field(min_length=1, max_length=MAX_FACT_ENTITIES)
    metric: str = Field(min_length=1, max_length=MAX_METRIC_CHARS)
    value: FiniteFloat
    unit: str | None = Field(default=None, min_length=1, max_length=MAX_UNIT_CHARS)
    observation_dates: list[date] = Field(min_length=1, max_length=4)
    period_start_year: int = Field(ge=MIN_YEAR, le=MAX_YEAR)
    period_end_year: int = Field(ge=MIN_YEAR, le=MAX_YEAR)
    sources: list[SourceReference] = Field(min_length=1, max_length=MAX_SOURCES_PER_FACT)
    lineage: CalculationLineage
    caveats: list[FactCaveatCode] = Field(default_factory=_empty_caveats, max_length=MAX_CAVEATS)
    display: FactDisplay

    @field_validator("value", mode="after")
    @classmethod
    def _normalise_zero(cls, value: float) -> float:
        return _normalise_float(value)

    @field_validator("unit", mode="after")
    @classmethod
    def _check_unit(cls, value: str | None) -> str | None:
        if value is not None and value not in {"%", "ratio"}:
            raise ValueError('unit must be "%", "ratio" or None')
        return value

    @model_validator(mode="after")
    def _check_invariants(self) -> "Fact":
        if self.period_end_year < self.period_start_year:
            raise ValueError("period_end_year must be >= period_start_year")
        if self.lineage.calculation != self.kind:
            raise ValueError("lineage.calculation must equal kind")
        if len(set(self.entities)) != len(self.entities):
            raise ValueError("entities must be unique")
        if len(set(self.caveats)) != len(self.caveats):
            raise ValueError("caveats must be unique")
        dates = self.observation_dates
        if len(set(dates)) != len(dates):
            raise ValueError("observation_dates must be unique")
        if dates != sorted(dates):
            raise ValueError("observation_dates must be ascending")
        return self


class FactSet(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_set_id: str = Field(
        min_length=1, max_length=MAX_FACT_SET_ID_CHARS, pattern=FACT_SET_ID_PATTERN
    )
    schema_version: int = Field(default=FACT_SCHEMA_VERSION, ge=1)
    story_id: str = Field(min_length=1, max_length=MAX_STORY_ID_CHARS)
    status: FactSetStatus
    research_status: ResearchStatus
    facts: list[Fact] = Field(default_factory=_empty_facts, max_length=MAX_FACTS)
    evidence_ids: list[EvidenceId] = Field(default_factory=_empty_strings, max_length=MAX_EVIDENCE)
    notes: list[FactNoteCode] = Field(default_factory=_empty_notes, max_length=MAX_FACT_NOTES)
    dataset_slug: str | None = Field(default=None, min_length=1, max_length=MAX_DATASET_SLUG_CHARS)

    @model_validator(mode="after")
    def _check_invariants(self) -> "FactSet":
        fact_ids = [fact.fact_id for fact in self.facts]
        if len(set(fact_ids)) != len(fact_ids):
            raise ValueError("fact_id values must be unique")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("evidence_ids must be unique")
        if len(set(self.notes)) != len(self.notes):
            raise ValueError("notes must be unique")

        for fact in self.facts:
            if fact.schema_version != self.schema_version:
                raise ValueError("every fact schema_version must equal the set's")
            for source in fact.sources:
                if source.evidence_id not in self.evidence_ids:
                    raise ValueError("every source evidence_id must be in evidence_ids")

        if self.status is FactSetStatus.AVAILABLE:
            if not self.facts or not self.evidence_ids or self.dataset_slug is None:
                raise ValueError("available facts need facts, evidence_ids and a dataset_slug")
            if self.research_status is not ResearchStatus.SUPPORTED:
                raise ValueError("available facts need supported research")
        elif self.status is FactSetStatus.EMPTY:
            if self.facts or self.evidence_ids:
                raise ValueError("empty fact sets must not carry facts or evidence_ids")
            if self.research_status is not ResearchStatus.SUPPORTED:
                raise ValueError("empty fact sets need supported research")
        else:
            if self.facts or self.evidence_ids:
                raise ValueError("unsupported fact sets must not carry facts or evidence_ids")
            if self.research_status is not ResearchStatus.UNSUPPORTED:
                raise ValueError("unsupported fact sets need unsupported research")
            if FactNoteCode.RESEARCH_UNSUPPORTED not in self.notes:
                raise ValueError("unsupported fact sets need the research_unsupported note")
            if self.dataset_slug is not None:
                raise ValueError("unsupported fact sets must not carry a dataset_slug")
        return self


class FactInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    story_id: str = Field(min_length=1, max_length=MAX_STORY_ID_CHARS)
    research_status: ResearchStatus
    evidence: list[EvidenceSource] = Field(default_factory=_empty_evidence, max_length=MAX_EVIDENCE)
    story: StoryInput | None = None
    max_facts: int = Field(default=MAX_FACTS, ge=1, le=MAX_FACTS)

    @model_validator(mode="after")
    def _check_story(self) -> "FactInput":
        if self.story is not None and self.story.candidate_id != self.story_id:
            raise ValueError("story.candidate_id must equal story_id")
        return self


def _hash_seed(seed: object) -> str:
    payload = json.dumps(seed, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def fact_id_for(
    kind: FactKind,
    entities: Sequence[str],
    metric: str,
    value: float,
    period_start_year: int,
    period_end_year: int,
    evidence_ids: Sequence[str],
) -> str:
    """Stable `fact-v1-<16 hex>` id derived only from calculation content and evidence."""
    seed = [
        FACT_SCHEMA_VERSION,
        kind.value,
        list(entities),
        metric,
        _normalise_float(value),
        period_start_year,
        period_end_year,
        list(evidence_ids),
    ]
    return f"fact-v1-{_hash_seed(seed)}"


def fact_set_id_for(story_id: str, status: FactSetStatus, facts: Sequence[Fact]) -> str:
    """Stable `fset-v1-<16 hex>` id derived from the story, status and ordered fact ids."""
    seed = [FACT_SCHEMA_VERSION, story_id, status.value, [fact.fact_id for fact in facts]]
    return f"fset-v1-{_hash_seed(seed)}"
