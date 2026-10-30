"""Deterministic builders for fact tests.

No files, network, clock or randomness: every value here is fixed, so the same fixture produces
the same `FactInput` and `FactSet` on every run.
"""

from datetime import UTC, date, datetime

from ai_trend_pipeline.analysis.models import (
    CalculationLineage,
    Fact,
    FactCaveatCode,
    FactDisplay,
    FactInput,
    FactKind,
    FactMethod,
    FactSet,
    FactSetStatus,
    SourceReference,
    fact_id_for,
    fact_set_id_for,
)
from ai_trend_pipeline.research.models import (
    DatasetEvidence,
    DatasetRow,
    EvidenceKind,
    EvidenceSource,
    ResearchFailure,
    ResearchFailureCode,
    ResearchOutput,
    ResearchStatus,
    StoryContext,
    StoryInput,
    context_id_for,
    evidence_id_for,
)
from ai_trend_pipeline.sources.models import SourceName

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
CANDIDATE_ID = "candidate-v1-0123456789abcdef"
CONTEXT_ID = context_id_for("https://news.example/story")
DATASET_EVIDENCE_ID = evidence_id_for(EvidenceKind.DATASET, "life-expectancy")
ARTICLE_EVIDENCE_ID = evidence_id_for(EvidenceKind.ARTICLE, CONTEXT_ID)
DEFAULT_FACT_ID = fact_id_for(
    FactKind.LATEST,
    ["World"],
    "Life expectancy",
    72.5,
    2020,
    2020,
    [DATASET_EVIDENCE_ID],
)


def make_story(**overrides: object) -> StoryInput:
    data: dict[str, object] = {
        "candidate_id": CANDIDATE_ID,
        "source": SourceName.HN,
        "external_id": "hn-1",
        "title": "Life expectancy keeps rising",
        "url": "https://news.example/story",
        "published_at": NOW,
        "observed_at": NOW,
        "summary": "Global life expectancy climbed again.",
        "proposed_angle": "Show the long-run trend.",
        "selection_reason": "Relevant and timely.",
        "metrics": {"score": 10},
    }
    data.update(overrides)
    return StoryInput.model_validate(data)


def make_rows(values: dict[str, dict[int, float]]) -> list[DatasetRow]:
    """Rows in entity insertion order, then year insertion order."""
    return [
        DatasetRow(entity=entity, code=None, year=year, value=value)
        for entity, points in values.items()
        for year, value in points.items()
    ]


def make_explicit_rows(rows: list[tuple[str, int, float]]) -> list[DatasetRow]:
    return [
        DatasetRow(entity=entity, code=None, year=year, value=value) for entity, year, value in rows
    ]


def make_dataset_evidence(
    rows: list[DatasetRow],
    *,
    slug: str = "life-expectancy",
    title: str = "Life expectancy",
    value_column: str = "life_expectancy_0",
    truncated: bool = False,
    evidence_id: str = DATASET_EVIDENCE_ID,
) -> EvidenceSource:
    return EvidenceSource(
        evidence_id=evidence_id,
        kind=EvidenceKind.DATASET,
        title=title,
        url=None,
        publisher=None,
        retrieved_at=NOW,
        observation_date=None,
        context_id=None,
        excerpt=None,
        dataset=DatasetEvidence(
            slug=slug,
            title=title,
            value_column=value_column,
            since_year=2000,
            rows=rows,
            matched_countries=[],
            notes=[],
            truncated=truncated,
        ),
    )


def make_article_evidence(
    *,
    evidence_id: str = ARTICLE_EVIDENCE_ID,
    context_id: str = CONTEXT_ID,
) -> EvidenceSource:
    return EvidenceSource.model_validate(
        {
            "evidence_id": evidence_id,
            "kind": EvidenceKind.ARTICLE,
            "title": "Life expectancy keeps rising",
            "url": "https://news.example/story",
            "publisher": "news.example",
            "retrieved_at": NOW,
            "observation_date": date(2026, 10, 30),
            "context_id": context_id,
            "excerpt": "A short excerpt.",
            "dataset": None,
        }
    )


def make_fact_input(
    evidence: list[EvidenceSource],
    *,
    story_id: str = CANDIDATE_ID,
    research_status: ResearchStatus = ResearchStatus.SUPPORTED,
    story: StoryInput | None = None,
    max_facts: int = 32,
) -> FactInput:
    return FactInput(
        story_id=story_id,
        research_status=research_status,
        evidence=evidence,
        story=story,
        max_facts=max_facts,
    )


def make_research_output(
    evidence: list[EvidenceSource],
    *,
    story_id: str = CANDIDATE_ID,
    status: ResearchStatus = ResearchStatus.SUPPORTED,
) -> ResearchOutput:
    context = StoryContext.model_validate(
        {
            "context_id": CONTEXT_ID,
            "requested_url": "https://news.example/story",
            "url": "https://news.example/story",
            "title": "Life expectancy keeps rising",
            "publisher": "news.example",
            "content_type": "text/html",
            "text": "x" * 400,
            "truncated": False,
            "retrieved_at": NOW,
        }
    )
    failures = (
        []
        if status is ResearchStatus.SUPPORTED
        else [
            ResearchFailure(
                code=ResearchFailureCode.INSUFFICIENT_EVIDENCE,
                message="The article text is too short.",
            )
        ]
    )
    return ResearchOutput(
        story_id=story_id,
        status=status,
        contexts=[context] if status is ResearchStatus.SUPPORTED else [],
        evidence=evidence,
        failures=failures,
        notes=[],
        researched_at=NOW,
    )


def make_source() -> SourceReference:
    return SourceReference(
        evidence_id=DATASET_EVIDENCE_ID,
        kind=EvidenceKind.DATASET,
        title="Life expectancy",
        url=None,
        publisher=None,
    )


def make_lineage(**overrides: object) -> CalculationLineage:
    data: dict[str, object] = {
        "calculation": FactKind.LATEST,
        "input_evidence_ids": [DATASET_EVIDENCE_ID],
        "dataset_slug": "life-expectancy",
        "value_column": "life_expectancy_0",
        "entities": ["World"],
        "entity_count": 1,
        "start_year": 2020,
        "end_year": 2020,
        "baseline_year": None,
        "baseline_value": None,
        "row_count": 1,
        "formula": "latest_value",
        "method": FactMethod.SERIES_ENDPOINT,
        "parameters": {},
    }
    data.update(overrides)
    return CalculationLineage.model_validate(data)


def make_display(**overrides: object) -> FactDisplay:
    data: dict[str, object] = {
        "label": "Life expectancy, World, 2020",
        "value_text": "72.5",
        "subject": "World",
        "period_text": "2020",
    }
    data.update(overrides)
    return FactDisplay.model_validate(data)


def make_fact(**overrides: object) -> Fact:
    data: dict[str, object] = {
        "fact_id": DEFAULT_FACT_ID,
        "schema_version": 1,
        "kind": FactKind.LATEST,
        "entities": ["World"],
        "metric": "Life expectancy",
        "value": 72.5,
        "unit": None,
        "observation_dates": [date(2020, 1, 1)],
        "period_start_year": 2020,
        "period_end_year": 2020,
        "sources": [make_source()],
        "lineage": make_lineage(),
        "caveats": [],
        "display": make_display(),
    }
    data.update(overrides)
    return Fact.model_validate(data)


def make_fact_set(**overrides: object) -> FactSet:
    fact = make_fact()
    data: dict[str, object] = {
        "fact_set_id": fact_set_id_for(CANDIDATE_ID, FactSetStatus.AVAILABLE, [fact]),
        "schema_version": 1,
        "story_id": CANDIDATE_ID,
        "status": FactSetStatus.AVAILABLE,
        "research_status": ResearchStatus.SUPPORTED,
        "facts": [fact],
        "evidence_ids": [DATASET_EVIDENCE_ID],
        "notes": [],
        "dataset_slug": "life-expectancy",
    }
    data.update(overrides)
    return FactSet.model_validate(data)


def make_caveats() -> list[FactCaveatCode]:
    return []
