"""Contract tests for the fact models: bounds, freezing, invariants and stable IDs."""

import math
import re
from datetime import date

import pytest
from pydantic import ValidationError

from ai_trend_pipeline.analysis import (
    FACT_ID_PATTERN,
    FACT_SET_ID_PATTERN,
    MAX_CAVEATS,
    MAX_DISPLAY_VALUE_CHARS,
    MAX_FACT_SET_BYTES_TARGET,
    MAX_FACTS,
    MAX_LINEAGE_ENTITIES,
    MAX_SOURCES_PER_FACT,
    CalculationLineage,
    FactCaveatCode,
    FactInput,
    FactKind,
    FactMethod,
    FactNoteCode,
    FactSet,
    FactSetStatus,
    SourceReference,
    fact_id_for,
    fact_set_id_for,
)
from ai_trend_pipeline.analysis.calculations import build_fact_set
from ai_trend_pipeline.research import EvidenceKind, ResearchStatus
from tests.fact_fixtures import (
    CANDIDATE_ID,
    DATASET_EVIDENCE_ID,
    make_article_evidence,
    make_dataset_evidence,
    make_display,
    make_fact,
    make_fact_input,
    make_fact_set,
    make_lineage,
    make_rows,
    make_source,
    make_story,
)


def test_fact_is_frozen_bounded_and_rejects_extra_fields() -> None:
    fact = make_fact()
    assert fact.value == 72.5
    assert re.fullmatch(FACT_ID_PATTERN, fact.fact_id) is not None

    with pytest.raises(ValidationError):
        make_fact(entities=["A", "B", "C"])
    with pytest.raises(ValidationError):
        make_fact(entities=["World", "World"])
    with pytest.raises(ValidationError):
        make_fact(sources=[make_source() for _ in range(MAX_SOURCES_PER_FACT + 1)])
    with pytest.raises(ValidationError):
        make_fact(caveats=[FactCaveatCode.SINGLE_OBSERVATION] * (MAX_CAVEATS + 1))
    with pytest.raises(ValidationError):
        make_fact(fact_id="fact-v1-not-hex")
    with pytest.raises(ValidationError):
        make_fact(value=float("inf"))
    with pytest.raises(ValidationError):
        make_fact(value=float("nan"))
    with pytest.raises(ValidationError):
        make_fact(unit="USD")
    with pytest.raises(ValidationError):
        make_fact(unknown_field="nope")
    with pytest.raises(ValidationError):
        fact.kind = FactKind.FIRST

    negative_zero = make_fact(value=-0.0)
    assert negative_zero.value == 0.0
    assert math.copysign(1.0, negative_zero.value) == 1.0


def test_fact_validates_period_order_observation_dates_and_lineage_kind() -> None:
    with pytest.raises(ValidationError):
        make_fact(period_start_year=2021, period_end_year=2020)
    with pytest.raises(ValidationError):
        make_fact(observation_dates=[date(2021, 1, 1), date(2020, 1, 1)])
    with pytest.raises(ValidationError):
        make_fact(observation_dates=[date(2020, 1, 1), date(2020, 1, 1)])
    with pytest.raises(ValidationError):
        make_fact(kind=FactKind.FIRST)

    with pytest.raises(ValidationError):
        make_lineage(entities=[f"Entity {index}" for index in range(MAX_LINEAGE_ENTITIES + 1)])
    with pytest.raises(ValidationError):
        make_lineage(entities=["World", "World"])
    with pytest.raises(ValidationError):
        make_lineage(start_year=2021, end_year=2020)
    with pytest.raises(ValidationError):
        make_lineage(parameters={f"p{index}": float(index) for index in range(7)})

    display = make_display()
    assert display.label == "Life expectancy, World, 2020"


def test_fact_set_status_invariants_and_source_links() -> None:
    with pytest.raises(ValidationError):
        make_fact_set(facts=[])
    with pytest.raises(ValidationError):
        make_fact_set(evidence_ids=[])
    with pytest.raises(ValidationError):
        make_fact_set(dataset_slug=None)
    with pytest.raises(ValidationError):
        make_fact_set(research_status=ResearchStatus.UNSUPPORTED)

    with pytest.raises(ValidationError):
        make_fact_set(status=FactSetStatus.EMPTY, facts=[make_fact()])
    with pytest.raises(ValidationError):
        make_fact_set(status=FactSetStatus.EMPTY, evidence_ids=[DATASET_EVIDENCE_ID])
    with pytest.raises(ValidationError):
        make_fact_set(
            status=FactSetStatus.EMPTY,
            facts=[],
            evidence_ids=[],
            dataset_slug=None,
            research_status=ResearchStatus.UNSUPPORTED,
        )

    with pytest.raises(ValidationError):
        make_fact_set(
            status=FactSetStatus.UNSUPPORTED,
            research_status=ResearchStatus.UNSUPPORTED,
            facts=[],
            evidence_ids=[],
            dataset_slug=None,
            notes=[],
        )
    unsupported = make_fact_set(
        status=FactSetStatus.UNSUPPORTED,
        research_status=ResearchStatus.UNSUPPORTED,
        facts=[],
        evidence_ids=[],
        dataset_slug=None,
        notes=[FactNoteCode.RESEARCH_UNSUPPORTED],
    )
    assert unsupported.facts == []
    assert unsupported.status is FactSetStatus.UNSUPPORTED

    with pytest.raises(ValidationError):
        make_fact_set(evidence_ids=["ev-v1-0000000000000000"])
    with pytest.raises(ValidationError):
        make_fact_set(facts=[make_fact(schema_version=2)])
    with pytest.raises(ValidationError):
        make_fact_set(facts=[make_fact(), make_fact()])

    available = make_fact_set()
    assert available.facts[0].sources[0].evidence_id in available.evidence_ids


def test_fact_input_limits_and_story_id_must_match() -> None:
    evidence = [make_article_evidence() for _ in range(5)]
    with pytest.raises(ValidationError):
        make_fact_input(evidence)
    with pytest.raises(ValidationError):
        make_fact_input([make_article_evidence()], max_facts=0)
    with pytest.raises(ValidationError):
        make_fact_input([make_article_evidence()], max_facts=MAX_FACTS + 1)
    with pytest.raises(ValidationError):
        FactInput.model_validate(
            {
                "story_id": CANDIDATE_ID,
                "research_status": ResearchStatus.SUPPORTED,
                "evidence": [make_article_evidence()],
                "unknown_field": "nope",
            }
        )

    data = make_fact_input([make_article_evidence()])
    assert data.story is None
    with pytest.raises(ValidationError):
        data.story_id = "other"

    with pytest.raises(ValidationError):
        make_fact_input(
            [make_article_evidence()],
            story=make_story(candidate_id="candidate-v1-ffffffffffffffff"),
        )
    matched = make_fact_input([make_article_evidence()], story=make_story())
    assert matched.story is not None
    assert matched.story.candidate_id == matched.story_id


def test_fact_models_round_trip_json_and_near_max_serialize_under_64_kib() -> None:
    fact_set = make_fact_set()
    assert FactSet.model_validate_json(fact_set.model_dump_json()) == fact_set

    pattern = [
        10.0,
        30.0,
        5.0,
        20.0,
        15.0,
        25.0,
        12.0,
        28.0,
        8.0,
        22.0,
        18.0,
        26.0,
        9.0,
        24.0,
        14.0,
    ]
    rows: dict[str, dict[int, float]] = {}
    for entity_index in range(20):
        values = {
            2000 + year_index: value + float(entity_index)
            for year_index, value in enumerate(pattern)
        }
        if entity_index == 18:
            values[2014] = 10_000.0
        if entity_index == 19:
            values[2014] = 20_000.0
        rows[f"Entity {entity_index:02d}"] = values
    evidence = make_dataset_evidence(make_rows(rows))
    near_max = build_fact_set(make_fact_input([evidence]))

    assert len(near_max.facts) == MAX_FACTS
    assert len(near_max.model_dump_json().encode("utf-8")) < MAX_FACT_SET_BYTES_TARGET


def test_fact_id_helpers_are_stable_and_seed_sensitive() -> None:
    def build(
        *,
        kind: FactKind = FactKind.LATEST,
        entities: list[str] | None = None,
        metric: str = "Life expectancy",
        value: float = 72.5,
        period_start_year: int = 2020,
        period_end_year: int = 2020,
        evidence_ids: list[str] | None = None,
    ) -> str:
        return fact_id_for(
            kind,
            ["World"] if entities is None else entities,
            metric,
            value,
            period_start_year,
            period_end_year,
            [DATASET_EVIDENCE_ID] if evidence_ids is None else evidence_ids,
        )

    fact_id = build()
    assert fact_id == build()
    assert re.fullmatch(FACT_ID_PATTERN, fact_id) is not None
    assert len(fact_id) == len("fact-v1-") + 16

    variants = [
        build(kind=FactKind.FIRST),
        build(entities=["France"]),
        build(metric="GDP"),
        build(value=72.6),
        build(period_start_year=2019),
        build(period_end_year=2021),
        build(evidence_ids=["ev-v1-ffffffffffffffff"]),
    ]
    seen = {fact_id}
    for changed in variants:
        assert changed not in seen
        seen.add(changed)

    fact = make_fact()
    set_id = fact_set_id_for(CANDIDATE_ID, FactSetStatus.AVAILABLE, [fact])
    assert set_id == fact_set_id_for(CANDIDATE_ID, FactSetStatus.AVAILABLE, [fact])
    assert re.fullmatch(FACT_SET_ID_PATTERN, set_id) is not None
    assert len(set_id) == len("fset-v1-") + 16
    assert set_id != fact_set_id_for(CANDIDATE_ID, FactSetStatus.EMPTY, [])
    assert set_id != fact_set_id_for("other", FactSetStatus.AVAILABLE, [fact])


def test_display_cleaning_collapses_whitespace_and_truncates() -> None:
    display = make_display(
        label="  Life\n\texpectancy   keeps  rising  ",
        value_text="\x00" + "9" * (MAX_DISPLAY_VALUE_CHARS + 10),
    )
    assert display.label == "Life expectancy keeps rising"
    assert len(display.value_text) == MAX_DISPLAY_VALUE_CHARS
    assert "\x00" not in display.value_text


def test_source_reference_and_lineage_bounds() -> None:
    with pytest.raises(ValidationError):
        SourceReference(
            evidence_id="",
            kind=EvidenceKind.DATASET,
            title="Title",
            url=None,
            publisher=None,
        )
    with pytest.raises(ValidationError):
        CalculationLineage.model_validate(
            {
                "calculation": FactKind.LATEST,
                "input_evidence_ids": [DATASET_EVIDENCE_ID, DATASET_EVIDENCE_ID],
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
        )

    assert (
        FactInput(
            story_id=CANDIDATE_ID,
            research_status=ResearchStatus.SUPPORTED,
            evidence=[make_dataset_evidence(make_rows({"World": {2020: 1.0}}))],
        ).max_facts
        == MAX_FACTS
    )
