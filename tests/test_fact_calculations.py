"""Calculation tests for the deterministic fact builder."""

import json
from datetime import date

from ai_trend_pipeline.analysis import (
    MAX_FACTS,
    MAX_OUTLIER_FACTS,
    MIN_OUTLIER_ENTITIES,
    Fact,
    FactCaveatCode,
    FactKind,
    FactMethod,
    FactNoteCode,
    FactSet,
    FactSetStatus,
    fact_id_for,
)
from ai_trend_pipeline.analysis.calculations import build_fact_set, fact_input_for
from ai_trend_pipeline.research import ResearchStatus, StoryInput
from tests.fact_fixtures import (
    DATASET_EVIDENCE_ID,
    make_article_evidence,
    make_dataset_evidence,
    make_explicit_rows,
    make_fact_input,
    make_research_output,
    make_rows,
    make_story,
)


def _facts(result: FactSet, kind: FactKind) -> list[Fact]:
    return [fact for fact in result.facts if fact.kind is kind]


def _one(result: FactSet, kind: FactKind) -> Fact:
    matches = _facts(result, kind)
    assert len(matches) == 1, f"expected one {kind} fact, got {len(matches)}"
    return matches[0]


def _build(
    rows: dict[str, dict[int, float]],
    *,
    story: StoryInput | None = None,
    max_facts: int = MAX_FACTS,
) -> FactSet:
    evidence = make_dataset_evidence(make_rows(rows))
    return build_fact_set(make_fact_input([evidence], story=story, max_facts=max_facts))


def all_kinds_input() -> FactSet:
    evidence = make_dataset_evidence(
        make_rows(
            {
                "Alpha": {2000: 10.0, 2005: 30.0, 2010: 5.0, 2015: 20.0, 2020: 15.0},
                "Beta": {2000: 9.0, 2005: 8.0, 2010: 1.0},
                "Gamma": {2000: 9.0, 2005: 8.0, 2010: 2.0},
                "Delta": {2000: 9.0, 2005: 8.0, 2010: 3.0},
                "Epsilon": {2000: 9.0, 2005: 8.0, 2010: 4.0},
                "Zeta": {2000: 9.0, 2005: 8.0, 2010: 1000.0},
            }
        )
    )
    return build_fact_set(make_fact_input([evidence]))


def test_first_and_latest_values_years_and_observation_dates() -> None:
    result = _build({"World": {2000: 50.0, 2005: 60.0, 2010: 70.0}})
    assert result.status is FactSetStatus.AVAILABLE
    assert result.evidence_ids == [DATASET_EVIDENCE_ID]
    assert result.dataset_slug == "life-expectancy"

    first = _one(result, FactKind.FIRST)
    assert first.value == 50.0
    assert first.observation_dates == [date(2000, 1, 1)]
    assert (first.period_start_year, first.period_end_year) == (2000, 2000)
    assert first.lineage.calculation is FactKind.FIRST
    assert first.lineage.formula == "first_value"
    assert first.lineage.method is FactMethod.SERIES_ENDPOINT
    assert first.lineage.entities == ["World"]
    assert first.lineage.entity_count == 1
    assert first.lineage.row_count == 3
    assert first.lineage.input_evidence_ids == [DATASET_EVIDENCE_ID]
    assert first.sources[0].evidence_id == DATASET_EVIDENCE_ID
    assert first.sources[0].kind.value == "dataset"
    assert first.display.label == "Life expectancy, World, 2000"
    assert first.display.value_text == "50"
    assert first.display.subject == "World"
    assert first.display.period_text == "2000"

    latest = _one(result, FactKind.LATEST)
    assert latest.value == 70.0
    assert latest.observation_dates == [date(2010, 1, 1)]
    assert (latest.period_start_year, latest.period_end_year) == (2010, 2010)
    assert latest.lineage.formula == "latest_value"
    assert latest.display.value_text == "70"
    assert latest.display.label == "Life expectancy, World, 2010"

    # Endpoint extrema produce no duplicate min/max facts.
    assert _facts(result, FactKind.MIN) == []
    assert _facts(result, FactKind.MAX) == []
    assert len(result.facts) == 4


def test_min_and_max_are_interior_extremes_with_their_years() -> None:
    result = _build({"World": {2000: 50.0, 2005: 40.0, 2010: 70.0, 2015: 45.0, 2020: 60.0}})
    minimum = _one(result, FactKind.MIN)
    maximum = _one(result, FactKind.MAX)

    assert minimum.value == 40.0
    assert minimum.period_start_year == 2005
    assert minimum.observation_dates == [date(2005, 1, 1)]
    assert minimum.lineage.formula == "min_value"
    assert minimum.lineage.method is FactMethod.SERIES_EXTREME
    assert minimum.lineage.start_year == 2005
    assert minimum.lineage.end_year == 2005
    assert minimum.display.label == "Life expectancy, World, 2005"

    assert maximum.value == 70.0
    assert maximum.period_start_year == 2010
    assert maximum.observation_dates == [date(2010, 1, 1)]
    assert maximum.lineage.formula == "max_value"
    assert maximum.lineage.method is FactMethod.SERIES_EXTREME


def test_change_and_percent_change_values_units_and_baseline_lineage() -> None:
    result = _build({"World": {2000: 50.0, 2020: 75.0}})
    change = _one(result, FactKind.CHANGE)
    percent = _one(result, FactKind.PERCENT_CHANGE)

    assert change.value == 25.0
    assert change.unit is None
    assert change.observation_dates == [date(2000, 1, 1), date(2020, 1, 1)]
    assert (change.period_start_year, change.period_end_year) == (2000, 2020)
    assert change.lineage.baseline_year == 2000
    assert change.lineage.baseline_value == 50.0
    assert change.lineage.formula == "latest_value - first_value"
    assert change.lineage.method is FactMethod.SERIES_ENDPOINT
    assert change.display.value_text == "+25"
    assert change.display.label == "Life expectancy, World, 2000-2020"
    assert change.caveats == [FactCaveatCode.PARTIAL_SERIES]

    assert percent.value == 50.0
    assert percent.unit == "%"
    assert percent.lineage.baseline_year == 2000
    assert percent.lineage.baseline_value == 50.0
    assert percent.lineage.formula == "100 * (latest_value - first_value) / first_value"
    assert percent.display.value_text == "+50.0%"

    rounded = _build({"World": {2000: 1.0, 2001: 1.23456789}})
    assert _one(rounded, FactKind.CHANGE).value == 0.234568
    assert _one(rounded, FactKind.PERCENT_CHANGE).value == 23.456789


def test_percent_change_omitted_for_zero_and_negative_baselines() -> None:
    for baseline in (0.0, -5.0):
        result = _build({"World": {2000: baseline, 2010: 10.0}})
        assert _facts(result, FactKind.PERCENT_CHANGE) == []
        change = _one(result, FactKind.CHANGE)
        assert FactCaveatCode.NON_POSITIVE_BASELINE in change.caveats
        assert FactNoteCode.NON_POSITIVE_BASELINE in result.notes


def test_comparison_uses_max_and_min_in_latest_common_year() -> None:
    result = _build(
        {
            "Alpha": {2000: 8.0, 2010: 10.0},
            "Beta": {2000: 4.0, 2010: 5.0},
            "Gamma": {2000: 9.0, 2010: 20.0},
        }
    )
    comparisons = _facts(result, FactKind.COMPARISON)
    assert len(comparisons) == 2
    difference, ratio = comparisons

    assert difference.value == 15.0
    assert difference.unit is None
    assert difference.entities == ["Gamma", "Beta"]
    assert difference.lineage.entities == ["Gamma", "Beta"]
    assert difference.lineage.entity_count == 3
    assert difference.lineage.formula == "max_latest - min_latest"
    assert difference.lineage.method is FactMethod.MAX_MIN
    assert difference.lineage.start_year == 2010
    assert difference.lineage.end_year == 2010
    assert difference.display.subject == "Gamma vs Beta"
    assert difference.display.value_text == "15"
    assert difference.display.label == "Life expectancy, Gamma vs Beta, 2010"

    assert ratio.value == 4.0
    assert ratio.unit == "ratio"
    assert ratio.lineage.formula == "max_latest / min_latest"
    assert ratio.display.value_text == "4.00x"

    non_positive = _build(
        {
            "Alpha": {2000: 8.0, 2010: 10.0},
            "Beta": {2000: 4.0, 2010: 5.0},
            "Gamma": {2000: 9.0, 2010: 20.0},
            "Delta": {2000: 0.0, 2010: 0.0},
        }
    )
    assert len(_facts(non_positive, FactKind.COMPARISON)) == 1
    only_difference = _one(non_positive, FactKind.COMPARISON)
    assert FactCaveatCode.NON_POSITIVE_BASELINE in only_difference.caveats
    assert only_difference.unit is None


def test_ranking_is_ordinal_with_name_tiebreak_and_tie_caveat() -> None:
    result = _build(
        {
            "Alpha": {2000: 8.0, 2010: 10.0},
            "Beta": {2000: 9.0, 2010: 10.0},
            "Gamma": {2000: 4.0, 2010: 5.0},
        }
    )
    rankings = _facts(result, FactKind.RANKING)
    assert [fact.entities[0] for fact in rankings] == ["Alpha", "Beta", "Gamma"]
    assert [fact.lineage.parameters["rank"] for fact in rankings] == [1.0, 2.0, 3.0]
    assert [fact.display.value_text for fact in rankings] == ["#1 of 3", "#2 of 3", "#3 of 3"]
    assert rankings[0].lineage.method is FactMethod.ORDINAL
    assert rankings[0].lineage.formula == (
        "ordinal_rank(latest_value, descending, ties_by_entity_name)"
    )
    assert rankings[0].lineage.entity_count == 3
    assert FactCaveatCode.TIED_VALUES in rankings[0].caveats
    assert FactCaveatCode.TIED_VALUES in rankings[1].caveats
    assert rankings[2].caveats == []
    assert rankings[0].display.label == "Life expectancy, rank of Alpha, 2010"

    selected_only = _build(
        {
            "Alpha": {2000: 5.0, 2010: 5.0},
            "Beta": {2000: 4.0, 2010: 4.0},
            "Gamma": {2000: 3.0, 2010: 3.0},
            "Delta": {2000: 2.0, 2010: 2.0},
            "Epsilon": {2000: 1.0, 2010: 1.0},
        }
    )
    assert [fact.entities[0] for fact in _facts(selected_only, FactKind.RANKING)] == [
        "Alpha",
        "Beta",
        "Delta",
        "Epsilon",
    ]
    assert all(fact.lineage.entity_count == 5 for fact in _facts(selected_only, FactKind.RANKING))


def test_outliers_use_tukey_quartiles_and_are_bounded() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 100.0, 200.0]
    rows = {f"Entity {index}": {2010: value} for index, value in enumerate(values)}
    result = _build(rows)
    outliers = _facts(result, FactKind.OUTLIER)

    assert len(outliers) == MAX_OUTLIER_FACTS
    assert [fact.value for fact in outliers] == [200.0, 100.0]
    first = outliers[0]
    assert list(first.lineage.parameters) == [
        "q1",
        "q3",
        "iqr",
        "median",
        "lower_bound",
        "upper_bound",
    ]
    assert first.lineage.parameters == {
        "q1": 3.0,
        "q3": 8.0,
        "iqr": 5.0,
        "median": 5.5,
        "lower_bound": -4.5,
        "upper_bound": 15.5,
    }
    assert first.lineage.method is FactMethod.IQR_1_5
    assert first.lineage.entity_count == 10
    assert first.caveats == [FactCaveatCode.STATISTICAL_OUTLIER]
    assert first.display.label == "Life expectancy, outlier Entity 9, 2010"
    assert first.display.value_text == "200"

    below_minimum = _build(
        {"Alpha": {2010: 1.0}, "Beta": {2010: 2.0}, "Gamma": {2010: 3.0}, "Delta": {2010: 100.0}}
    )
    assert _facts(below_minimum, FactKind.OUTLIER) == []
    assert len(values) >= MIN_OUTLIER_ENTITIES


def test_partial_series_and_single_observation_caveats() -> None:
    partial = _build({"World": {2000: 1.0, 2005: 2.0, 2007: 3.0}})
    for kind in (FactKind.LATEST, FactKind.CHANGE, FactKind.PERCENT_CHANGE):
        assert FactCaveatCode.PARTIAL_SERIES in _one(partial, kind).caveats

    single = _build({"World": {2010: 5.0}})
    assert FactCaveatCode.SINGLE_OBSERVATION in _one(single, FactKind.FIRST).caveats
    assert FactCaveatCode.SINGLE_OBSERVATION in _one(single, FactKind.LATEST).caveats
    assert _facts(single, FactKind.CHANGE) == []
    assert _facts(single, FactKind.PERCENT_CHANGE) == []
    assert FactNoteCode.INSUFFICIENT_SERIES in single.notes


def test_duplicate_entities_and_years_are_collapsed_deterministically() -> None:
    evidence = make_dataset_evidence(
        make_explicit_rows(
            [
                ("World", 2000, 1.0),
                (" world ", 2001, 2.0),
                ("World", 2001, 3.0),
            ]
        )
    )
    result = build_fact_set(make_fact_input([evidence]))

    assert FactNoteCode.DUPLICATE_OBSERVATIONS_COLLAPSED in result.notes
    latest = _one(result, FactKind.LATEST)
    assert latest.entities == ["World"]
    assert latest.value == 3.0
    assert _one(result, FactKind.CHANGE).value == 2.0
    assert all(
        FactCaveatCode.DUPLICATE_OBSERVATIONS_COLLAPSED in fact.caveats for fact in result.facts
    )


def test_missing_data_returns_empty_fact_sets_with_reasons() -> None:
    no_evidence = build_fact_set(make_fact_input([]))
    assert no_evidence.status is FactSetStatus.EMPTY
    assert no_evidence.facts == []
    assert no_evidence.evidence_ids == []
    assert no_evidence.dataset_slug is None
    assert no_evidence.notes == [FactNoteCode.NO_DATASET_EVIDENCE]

    no_rows = build_fact_set(make_fact_input([make_dataset_evidence([])]))
    assert no_rows.status is FactSetStatus.EMPTY
    assert no_rows.notes == [FactNoteCode.NO_NUMERIC_ROWS]

    no_common_year = _build({"Alpha": {2000: 1.0}, "Beta": {2001: 2.0}, "Gamma": {2002: 3.0}})
    assert no_common_year.status is FactSetStatus.AVAILABLE
    assert FactNoteCode.INSUFFICIENT_CROSS_ENTITY_DATA in no_common_year.notes
    for kind in (FactKind.COMPARISON, FactKind.RANKING, FactKind.OUTLIER):
        assert _facts(no_common_year, kind) == []

    single_observations = _build({"Alpha": {2010: 1.0}, "Beta": {2010: 2.0}, "Gamma": {2010: 3.0}})
    assert FactNoteCode.INSUFFICIENT_SERIES in single_observations.notes
    assert _facts(single_observations, FactKind.CHANGE) == []
    assert _facts(single_observations, FactKind.PERCENT_CHANGE) == []


def test_unsupported_research_cannot_produce_fallback_facts() -> None:
    evidence = make_dataset_evidence(make_rows({"World": {2000: 1.0, 2010: 2.0}}))
    result = build_fact_set(make_fact_input([evidence], research_status=ResearchStatus.UNSUPPORTED))
    assert result.status is FactSetStatus.UNSUPPORTED
    assert result.facts == []
    assert result.evidence_ids == []
    assert result.dataset_slug is None
    assert result.notes == [FactNoteCode.RESEARCH_UNSUPPORTED]


def test_story_text_never_becomes_a_numeric_fact() -> None:
    story = make_story(
        title="World will hit 987654 soon",
        summary="Analysts expect 55555 by 2030.",
        proposed_angle="Show 777 percent growth.",
    )
    result = _build({"World": {2000: 1.0, 2010: 2.0}}, story=story)
    serialized = result.model_dump_json()

    assert "987654" not in serialized
    assert "55555" not in serialized
    assert "777" not in serialized
    for fact in result.facts:
        assert fact.value in {1.0, 2.0, 100.0}
    assert FactNoteCode.STORY_ENTITIES_MATCHED in result.notes


def test_story_entity_mentions_prioritize_entity_order() -> None:
    values = {
        "Alpha": {2000: 50.0, 2010: 55.0},
        "Beta": {2000: 40.0, 2010: 45.0},
        "Gamma": {2000: 30.0, 2010: 35.0},
        "Delta": {2000: 20.0, 2010: 25.0},
        "Epsilon": {2000: 10.0, 2010: 15.0},
    }
    mentioned = _build(values, story=make_story(summary="Gamma is the headline story."))
    assert mentioned.facts[0].entities == ["Gamma"]
    assert FactNoteCode.STORY_ENTITIES_MATCHED in mentioned.notes

    without_mentions = _build(values, story=make_story(summary="Nothing relevant here."))
    assert without_mentions.facts[0].entities == ["Alpha"]
    assert FactNoteCode.STORY_ENTITIES_MATCHED not in without_mentions.notes

    no_story = _build(values)
    assert no_story.facts[0].entities == ["Alpha"]


def test_every_lineage_field_is_populated_for_each_calculation() -> None:
    result = all_kinds_input()
    assert {fact.kind for fact in result.facts} == set(FactKind)
    assert len(result.facts) >= 9
    assert all(fact.lineage.row_count == 20 for fact in result.facts)

    for fact in result.facts:
        lineage = fact.lineage
        assert lineage.calculation is fact.kind
        assert 1800 <= lineage.start_year <= lineage.end_year <= 2100
        assert lineage.dataset_slug == "life-expectancy"
        assert lineage.value_column == "life_expectancy_0"
        assert lineage.input_evidence_ids == [DATASET_EVIDENCE_ID]
        assert 1 <= lineage.entity_count <= 6
        assert 1 <= len(lineage.entities) <= 2
        assert 1 <= len(lineage.formula) <= 200
        assert isinstance(lineage.method, FactMethod)
        if fact.kind in (FactKind.CHANGE, FactKind.PERCENT_CHANGE):
            assert lineage.baseline_year is not None
            assert lineage.baseline_value is not None
        else:
            assert lineage.baseline_year is None
            assert lineage.baseline_value is None

    outlier = _one(result, FactKind.OUTLIER)
    assert list(outlier.lineage.parameters) == [
        "q1",
        "q3",
        "iqr",
        "median",
        "lower_bound",
        "upper_bound",
    ]
    ranking = _facts(result, FactKind.RANKING)[0]
    assert list(ranking.lineage.parameters) == ["rank"]

    comparison_record = _facts(result, FactKind.COMPARISON)[0]
    assert comparison_record.lineage.entities == ["Zeta", "Beta"]
    assert comparison_record.lineage.entity_count == 6


def test_fact_budget_truncates_in_documented_order() -> None:
    result = _build({"World": {2000: 10.0, 2005: 30.0, 2010: 20.0}}, max_facts=2)
    assert [fact.kind for fact in result.facts] == [FactKind.FIRST, FactKind.LATEST]
    assert FactNoteCode.FACT_BUDGET_EXHAUSTED in result.notes

    full = _build({"World": {2000: 10.0, 2005: 30.0, 2010: 20.0}})
    assert len(full.facts) > 2
    assert FactNoteCode.FACT_BUDGET_EXHAUSTED not in full.notes
    assert len(full.facts) <= MAX_FACTS


def test_identical_inputs_produce_identical_serialized_fact_sets() -> None:
    first = all_kinds_input()
    second = all_kinds_input()

    assert first.model_dump_json() == second.model_dump_json()
    assert [fact.fact_id for fact in first.facts] == [fact.fact_id for fact in second.facts]
    assert first.notes == second.notes
    assert [fact.caveats for fact in first.facts] == [fact.caveats for fact in second.facts]
    assert [list(fact.lineage.parameters) for fact in first.facts] == [
        list(fact.lineage.parameters) for fact in second.facts
    ]
    assert first.fact_set_id == second.fact_set_id
    for fact in first.facts:
        assert fact.fact_id == fact_id_for(
            fact.kind,
            fact.entities,
            fact.metric,
            fact.value,
            fact.period_start_year,
            fact.period_end_year,
            fact.lineage.input_evidence_ids,
        )


def test_fact_input_for_maps_research_output_and_story() -> None:
    dataset = make_dataset_evidence(make_rows({"World": {2000: 1.0, 2010: 2.0}}))
    article = make_article_evidence()
    research = make_research_output([article, dataset])
    story = make_story()

    data = fact_input_for(research, story)
    assert data.story_id == research.story_id
    assert data.research_status is ResearchStatus.SUPPORTED
    assert data.evidence == research.evidence
    assert data.story == story
    assert data.max_facts == MAX_FACTS

    built = build_fact_set(data)
    assert built.status is FactSetStatus.AVAILABLE
    assert all(fact.sources[0].evidence_id == DATASET_EVIDENCE_ID for fact in built.facts)

    unsupported = make_research_output([dataset], status=ResearchStatus.UNSUPPORTED)
    fallback = build_fact_set(fact_input_for(unsupported))
    assert fallback.status is FactSetStatus.UNSUPPORTED
    assert fallback.facts == []


def test_non_dataset_evidence_is_never_numeric() -> None:
    article = make_article_evidence()
    result = build_fact_set(make_fact_input([article]))
    assert result.status is FactSetStatus.EMPTY
    assert result.notes == [FactNoteCode.NO_DATASET_EVIDENCE]

    dataset = make_dataset_evidence(make_rows({"World": {2000: 1.0}}))
    both = build_fact_set(make_fact_input([article, dataset]))
    assert both.status is FactSetStatus.AVAILABLE
    assert json.loads(both.model_dump_json())["status"] == "available"
