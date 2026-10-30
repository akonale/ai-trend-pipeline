"""Pure, deterministic fact calculation over 050 dataset evidence.

No I/O, clock, randomness, LLM or settings: identical `FactInput` bytes produce identical
`FactSet` bytes, including fact order, IDs, notes, caveats and parameter key order.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from ai_trend_pipeline.analysis.models import (
    IQR_FACTOR,
    MAX_OUTLIER_FACTS,
    MAX_SERIES_ENTITIES,
    MAX_SOURCE_TITLE_CHARS,
    MIN_OUTLIER_ENTITIES,
    MIN_RANKING_ENTITIES,
    CalculationLineage,
    Fact,
    FactCaveatCode,
    FactDisplay,
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
from ai_trend_pipeline.research.models import (
    DatasetEvidence,
    DatasetRow,
    EvidenceKind,
    EvidenceSource,
    ResearchOutput,
    ResearchStatus,
    StoryInput,
)

_FORMULA_FIRST = "first_value"
_FORMULA_LATEST = "latest_value"
_FORMULA_MIN = "min_value"
_FORMULA_MAX = "max_value"
_FORMULA_CHANGE = "latest_value - first_value"
_FORMULA_PERCENT = "100 * (latest_value - first_value) / first_value"
_FORMULA_DIFFERENCE = "max_latest - min_latest"
_FORMULA_RATIO = "max_latest / min_latest"
_FORMULA_RANKING = "ordinal_rank(latest_value, descending, ties_by_entity_name)"
_FORMULA_OUTLIER = "iqr_1_5"


@dataclass(frozen=True)
class _Series:
    key: str
    display: str
    points: tuple[tuple[int, float], ...]
    duplicate: bool


def _normalise(value: float) -> float:
    return 0.0 if value == 0 else value


def _round(value: float) -> float:
    return _normalise(round(value, 6))


def format_number(value: float) -> str:
    """Locale-independent compact number: four significant digits, no trailing zeros."""
    return f"{_normalise(value):.4g}"


def _ordered_caveats(caveats: set[FactCaveatCode]) -> list[FactCaveatCode]:
    return [code for code in FactCaveatCode if code in caveats]


def _ordered_notes(notes: set[FactNoteCode]) -> list[FactNoteCode]:
    return [code for code in FactNoteCode if code in notes]


def _source_reference(evidence: EvidenceSource) -> SourceReference:
    return SourceReference(
        evidence_id=evidence.evidence_id,
        kind=EvidenceKind.DATASET,
        title=evidence.title[:MAX_SOURCE_TITLE_CHARS],
        url=None,
        publisher=None,
    )


def _lineage(
    *,
    calculation: FactKind,
    evidence: EvidenceSource,
    dataset: DatasetEvidence,
    entities: Sequence[str],
    entity_count: int,
    start_year: int,
    end_year: int,
    baseline_year: int | None,
    baseline_value: float | None,
    formula: str,
    method: FactMethod,
    parameters: dict[str, float] | None = None,
) -> CalculationLineage:
    return CalculationLineage(
        calculation=calculation,
        input_evidence_ids=[evidence.evidence_id],
        dataset_slug=dataset.slug,
        value_column=dataset.value_column,
        entities=list(entities),
        entity_count=entity_count,
        start_year=start_year,
        end_year=end_year,
        baseline_year=baseline_year,
        baseline_value=baseline_value,
        row_count=len(dataset.rows),
        formula=formula,
        method=method,
        parameters={} if parameters is None else parameters,
    )


def _fact(
    *,
    kind: FactKind,
    entities: list[str],
    metric: str,
    value: float,
    unit: str | None,
    observation_years: Sequence[int],
    period_start_year: int,
    period_end_year: int,
    evidence: EvidenceSource,
    lineage: CalculationLineage,
    caveats: set[FactCaveatCode],
    label: str,
    value_text: str,
    subject: str,
    period_text: str,
) -> Fact:
    normalised = _normalise(value)
    return Fact(
        fact_id=fact_id_for(
            kind,
            entities,
            metric,
            normalised,
            period_start_year,
            period_end_year,
            [evidence.evidence_id],
        ),
        kind=kind,
        entities=entities,
        metric=metric,
        value=normalised,
        unit=unit,
        observation_dates=[date(year, 1, 1) for year in observation_years],
        period_start_year=period_start_year,
        period_end_year=period_end_year,
        sources=[_source_reference(evidence)],
        lineage=lineage,
        caveats=_ordered_caveats(caveats),
        display=FactDisplay(
            label=label, value_text=value_text, subject=subject, period_text=period_text
        ),
    )


def _collect_series(rows: Sequence[DatasetRow]) -> dict[str, _Series]:
    """Group rows by casefolded entity, merging duplicate entity-year observations."""
    displays: dict[str, str] = {}
    years: dict[str, dict[int, tuple[int, float]]] = {}
    duplicates: dict[str, bool] = {}

    for index, row in enumerate(rows):
        key = row.entity.strip().casefold()
        if not key:
            continue
        if key not in years:
            displays[key] = row.entity.strip()
            years[key] = {}
            duplicates[key] = False
        if row.year in years[key]:
            duplicates[key] = True
        years[key][row.year] = (index, row.value)

    series: dict[str, _Series] = {}
    for key, points_by_year in years.items():
        points = tuple(sorted((year, value) for year, (_, value) in points_by_year.items()))
        series[key] = _Series(
            key=key, display=displays[key], points=points, duplicate=duplicates[key]
        )
    return series


def _whole_word_position(text: str, key: str) -> int | None:
    if not key:
        return None
    match = re.search(rf"(?<!\w){re.escape(key)}(?!\w)", text)
    return None if match is None else match.start()


def _select_series(
    series: Sequence[_Series], story: StoryInput | None
) -> tuple[list[_Series], bool]:
    mentioned: list[tuple[int, _Series]] = []
    if story is not None:
        text = f"{story.title}\n{story.summary}\n{story.proposed_angle}".casefold()
        for item in series:
            position = _whole_word_position(text, item.key)
            if position is not None:
                mentioned.append((position, item))
    mentioned.sort(key=lambda pair: (pair[0], pair[1].display.casefold(), pair[1].key))

    mentioned_keys = {item.key for _, item in mentioned}
    remaining = [item for item in series if item.key not in mentioned_keys]
    remaining.sort(
        key=lambda item: (
            -len(item.points),
            -item.points[-1][0],
            item.display.casefold(),
            item.key,
        )
    )
    ordered = [item for _, item in mentioned] + remaining
    return ordered[:MAX_SERIES_ENTITIES], bool(mentioned)


def _value_at(series: _Series, year: int) -> float | None:
    for point_year, value in series.points:
        if point_year == year:
            return value
    return None


def _latest_common_year(series: Sequence[_Series]) -> int | None:
    counts: dict[int, int] = {}
    for item in series:
        for year, _ in item.points:
            counts[year] = counts.get(year, 0) + 1
    common = [year for year, count in counts.items() if count >= MIN_RANKING_ENTITIES]
    return max(common) if common else None


def _pool(series: Sequence[_Series], year: int) -> list[tuple[str, float]]:
    pool = [
        (item.display, value) for item in series if (value := _value_at(item, year)) is not None
    ]
    pool.sort(key=lambda pair: (-pair[1], pair[0].casefold(), pair[0]))
    return pool


def _median(sorted_values: Sequence[float]) -> float:
    middle = len(sorted_values) // 2
    if len(sorted_values) % 2 == 1:
        return sorted_values[middle]
    return (sorted_values[middle - 1] + sorted_values[middle]) / 2


def _point_label(metric: str, subject: str, period_text: str) -> str:
    return f"{metric}, {subject}, {period_text}"


def _entity_facts(
    series: _Series,
    evidence: EvidenceSource,
    dataset: DatasetEvidence,
    metric: str,
    notes: set[FactNoteCode],
) -> list[Fact]:
    points = series.points
    first_year, first_value = points[0]
    last_year, last_value = points[-1]
    gaps = any(right[0] - left[0] > 1 for left, right in zip(points, points[1:], strict=False))
    single = len(points) == 1
    base: set[FactCaveatCode] = set()
    if dataset.truncated:
        base.add(FactCaveatCode.DATASET_TRUNCATED)
    if series.duplicate:
        base.add(FactCaveatCode.DUPLICATE_OBSERVATIONS_COLLAPSED)

    facts: list[Fact] = []

    first_caveats = set(base)
    if single:
        first_caveats.add(FactCaveatCode.SINGLE_OBSERVATION)
    facts.append(
        _fact(
            kind=FactKind.FIRST,
            entities=[series.display],
            metric=metric,
            value=first_value,
            unit=None,
            observation_years=[first_year],
            period_start_year=first_year,
            period_end_year=first_year,
            evidence=evidence,
            lineage=_lineage(
                calculation=FactKind.FIRST,
                evidence=evidence,
                dataset=dataset,
                entities=[series.display],
                entity_count=1,
                start_year=first_year,
                end_year=first_year,
                baseline_year=None,
                baseline_value=None,
                formula=_FORMULA_FIRST,
                method=FactMethod.SERIES_ENDPOINT,
            ),
            caveats=first_caveats,
            label=_point_label(metric, series.display, str(first_year)),
            value_text=format_number(first_value),
            subject=series.display,
            period_text=str(first_year),
        )
    )

    latest_caveats = set(base)
    if single:
        latest_caveats.add(FactCaveatCode.SINGLE_OBSERVATION)
    if gaps:
        latest_caveats.add(FactCaveatCode.PARTIAL_SERIES)
    facts.append(
        _fact(
            kind=FactKind.LATEST,
            entities=[series.display],
            metric=metric,
            value=last_value,
            unit=None,
            observation_years=[last_year],
            period_start_year=last_year,
            period_end_year=last_year,
            evidence=evidence,
            lineage=_lineage(
                calculation=FactKind.LATEST,
                evidence=evidence,
                dataset=dataset,
                entities=[series.display],
                entity_count=1,
                start_year=last_year,
                end_year=last_year,
                baseline_year=None,
                baseline_value=None,
                formula=_FORMULA_LATEST,
                method=FactMethod.SERIES_ENDPOINT,
            ),
            caveats=latest_caveats,
            label=_point_label(metric, series.display, str(last_year)),
            value_text=format_number(last_value),
            subject=series.display,
            period_text=str(last_year),
        )
    )

    if len(points) >= 2:
        change_caveats = set(base)
        if gaps:
            change_caveats.add(FactCaveatCode.PARTIAL_SERIES)
        if first_value <= 0:
            change_caveats.add(FactCaveatCode.NON_POSITIVE_BASELINE)
            notes.add(FactNoteCode.NON_POSITIVE_BASELINE)
        delta = _round(last_value - first_value)
        period_text = f"{first_year}-{last_year}"
        facts.append(
            _fact(
                kind=FactKind.CHANGE,
                entities=[series.display],
                metric=metric,
                value=delta,
                unit=None,
                observation_years=[first_year, last_year],
                period_start_year=first_year,
                period_end_year=last_year,
                evidence=evidence,
                lineage=_lineage(
                    calculation=FactKind.CHANGE,
                    evidence=evidence,
                    dataset=dataset,
                    entities=[series.display],
                    entity_count=1,
                    start_year=first_year,
                    end_year=last_year,
                    baseline_year=first_year,
                    baseline_value=first_value,
                    formula=_FORMULA_CHANGE,
                    method=FactMethod.SERIES_ENDPOINT,
                ),
                caveats=change_caveats,
                label=_point_label(metric, series.display, period_text),
                value_text=f"{delta:+.4g}",
                subject=series.display,
                period_text=period_text,
            )
        )

        if first_value > 0:
            percent_caveats = set(base)
            if gaps:
                percent_caveats.add(FactCaveatCode.PARTIAL_SERIES)
            percent = _round((last_value - first_value) / first_value * 100)
            facts.append(
                _fact(
                    kind=FactKind.PERCENT_CHANGE,
                    entities=[series.display],
                    metric=metric,
                    value=percent,
                    unit="%",
                    observation_years=[first_year, last_year],
                    period_start_year=first_year,
                    period_end_year=last_year,
                    evidence=evidence,
                    lineage=_lineage(
                        calculation=FactKind.PERCENT_CHANGE,
                        evidence=evidence,
                        dataset=dataset,
                        entities=[series.display],
                        entity_count=1,
                        start_year=first_year,
                        end_year=last_year,
                        baseline_year=first_year,
                        baseline_value=first_value,
                        formula=_FORMULA_PERCENT,
                        method=FactMethod.SERIES_ENDPOINT,
                    ),
                    caveats=percent_caveats,
                    label=_point_label(metric, series.display, period_text),
                    value_text=f"{percent:+.1f}%",
                    subject=series.display,
                    period_text=period_text,
                )
            )

    minimum_year, minimum_value = _extreme_year(points, minimum=True)
    if first_year < minimum_year < last_year:
        facts.append(
            _extreme_fact(
                kind=FactKind.MIN,
                year=minimum_year,
                value=minimum_value,
                series=series,
                evidence=evidence,
                dataset=dataset,
                metric=metric,
                caveats=base,
                formula=_FORMULA_MIN,
            )
        )

    maximum_year, maximum_value = _extreme_year(points, minimum=False)
    if first_year < maximum_year < last_year:
        facts.append(
            _extreme_fact(
                kind=FactKind.MAX,
                year=maximum_year,
                value=maximum_value,
                series=series,
                evidence=evidence,
                dataset=dataset,
                metric=metric,
                caveats=base,
                formula=_FORMULA_MAX,
            )
        )

    return facts


def _extreme_year(points: Sequence[tuple[int, float]], *, minimum: bool) -> tuple[int, float]:
    values = [value for _, value in points]
    extreme = min(values) if minimum else max(values)
    year = next(year for year, value in points if value == extreme)
    return year, extreme


def _extreme_fact(
    *,
    kind: FactKind,
    year: int,
    value: float,
    series: _Series,
    evidence: EvidenceSource,
    dataset: DatasetEvidence,
    metric: str,
    caveats: set[FactCaveatCode],
    formula: str,
) -> Fact:
    return _fact(
        kind=kind,
        entities=[series.display],
        metric=metric,
        value=value,
        unit=None,
        observation_years=[year],
        period_start_year=year,
        period_end_year=year,
        evidence=evidence,
        lineage=_lineage(
            calculation=kind,
            evidence=evidence,
            dataset=dataset,
            entities=[series.display],
            entity_count=1,
            start_year=year,
            end_year=year,
            baseline_year=None,
            baseline_value=None,
            formula=formula,
            method=FactMethod.SERIES_EXTREME,
        ),
        caveats=caveats,
        label=_point_label(metric, series.display, str(year)),
        value_text=format_number(value),
        subject=series.display,
        period_text=str(year),
    )


def _comparison_fact(
    *,
    unit: str | None,
    value: float,
    year: int,
    names: tuple[str, str],
    pool_size: int,
    evidence: EvidenceSource,
    dataset: DatasetEvidence,
    metric: str,
    formula: str,
    caveats: set[FactCaveatCode],
    value_text: str,
) -> Fact:
    maximum, minimum = names
    subject = f"{maximum} vs {minimum}"
    return _fact(
        kind=FactKind.COMPARISON,
        entities=[maximum, minimum],
        metric=metric,
        value=value,
        unit=unit,
        observation_years=[year],
        period_start_year=year,
        period_end_year=year,
        evidence=evidence,
        lineage=_lineage(
            calculation=FactKind.COMPARISON,
            evidence=evidence,
            dataset=dataset,
            entities=[maximum, minimum],
            entity_count=pool_size,
            start_year=year,
            end_year=year,
            baseline_year=None,
            baseline_value=None,
            formula=formula,
            method=FactMethod.MAX_MIN,
        ),
        caveats=caveats,
        label=f"{metric}, {subject}, {year}",
        value_text=value_text,
        subject=subject,
        period_text=str(year),
    )


def _cross_entity_facts(
    series: Sequence[_Series],
    selected: Sequence[_Series],
    evidence: EvidenceSource,
    dataset: DatasetEvidence,
    metric: str,
    notes: set[FactNoteCode],
) -> list[Fact]:
    year = _latest_common_year(series)
    if year is None:
        notes.add(FactNoteCode.INSUFFICIENT_CROSS_ENTITY_DATA)
        return []

    pool = _pool(series, year)
    pool_size = len(pool)
    maximum_name, maximum_value = pool[0]
    minimum_name, minimum_value = pool[-1]
    names = (maximum_name, minimum_name)
    facts: list[Fact] = []

    difference_caveats: set[FactCaveatCode] = set()
    if minimum_value <= 0:
        difference_caveats.add(FactCaveatCode.NON_POSITIVE_BASELINE)
    difference = _round(maximum_value - minimum_value)
    facts.append(
        _comparison_fact(
            unit=None,
            value=difference,
            year=year,
            names=names,
            pool_size=pool_size,
            evidence=evidence,
            dataset=dataset,
            metric=metric,
            formula=_FORMULA_DIFFERENCE,
            caveats=difference_caveats,
            value_text=format_number(difference),
        )
    )

    if minimum_value > 0:
        ratio = _round(maximum_value / minimum_value)
        facts.append(
            _comparison_fact(
                unit="ratio",
                value=ratio,
                year=year,
                names=names,
                pool_size=pool_size,
                evidence=evidence,
                dataset=dataset,
                metric=metric,
                formula=_FORMULA_RATIO,
                caveats=set(),
                value_text=f"{ratio:.2f}x",
            )
        )

    selected_names = {item.display for item in selected}
    value_counts: dict[float, int] = {}
    for _, value in pool:
        value_counts[value] = value_counts.get(value, 0) + 1

    for rank, (name, value) in enumerate(pool, start=1):
        if name not in selected_names:
            continue
        caveats: set[FactCaveatCode] = set()
        if value_counts[value] > 1:
            caveats.add(FactCaveatCode.TIED_VALUES)
        facts.append(
            _fact(
                kind=FactKind.RANKING,
                entities=[name],
                metric=metric,
                value=value,
                unit=None,
                observation_years=[year],
                period_start_year=year,
                period_end_year=year,
                evidence=evidence,
                lineage=_lineage(
                    calculation=FactKind.RANKING,
                    evidence=evidence,
                    dataset=dataset,
                    entities=[name],
                    entity_count=pool_size,
                    start_year=year,
                    end_year=year,
                    baseline_year=None,
                    baseline_value=None,
                    formula=_FORMULA_RANKING,
                    method=FactMethod.ORDINAL,
                    parameters={"rank": float(rank)},
                ),
                caveats=caveats,
                label=f"{metric}, rank of {name}, {year}",
                value_text=f"#{rank} of {pool_size}",
                subject=name,
                period_text=str(year),
            )
        )

    if pool_size >= MIN_OUTLIER_ENTITIES:
        facts.extend(
            _outlier_facts(
                pool=pool,
                year=year,
                evidence=evidence,
                dataset=dataset,
                metric=metric,
            )
        )

    return facts


def _outlier_facts(
    *,
    pool: Sequence[tuple[str, float]],
    year: int,
    evidence: EvidenceSource,
    dataset: DatasetEvidence,
    metric: str,
) -> list[Fact]:
    values = sorted(value for _, value in pool)
    median = _median(values)
    half = len(values) // 2
    if len(values) % 2 == 1:
        lower = values[:half]
        upper = values[half + 1 :]
    else:
        lower = values[:half]
        upper = values[half:]
    q1 = _median(lower)
    q3 = _median(upper)
    iqr = q3 - q1
    lower_bound = q1 - IQR_FACTOR * iqr
    upper_bound = q3 + IQR_FACTOR * iqr

    candidates = [
        (name, value) for name, value in pool if value < lower_bound or value > upper_bound
    ]
    candidates.sort(key=lambda pair: (-abs(pair[1] - median), pair[0].casefold(), pair[0]))

    parameters = {
        "q1": _round(q1),
        "q3": _round(q3),
        "iqr": _round(iqr),
        "median": _round(median),
        "lower_bound": _round(lower_bound),
        "upper_bound": _round(upper_bound),
    }

    facts: list[Fact] = []
    for name, value in candidates[:MAX_OUTLIER_FACTS]:
        facts.append(
            _fact(
                kind=FactKind.OUTLIER,
                entities=[name],
                metric=metric,
                value=value,
                unit=None,
                observation_years=[year],
                period_start_year=year,
                period_end_year=year,
                evidence=evidence,
                lineage=_lineage(
                    calculation=FactKind.OUTLIER,
                    evidence=evidence,
                    dataset=dataset,
                    entities=[name],
                    entity_count=len(pool),
                    start_year=year,
                    end_year=year,
                    baseline_year=None,
                    baseline_value=None,
                    formula=_FORMULA_OUTLIER,
                    method=FactMethod.IQR_1_5,
                    parameters=dict(parameters),
                ),
                caveats={FactCaveatCode.STATISTICAL_OUTLIER},
                label=f"{metric}, outlier {name}, {year}",
                value_text=format_number(value),
                subject=name,
                period_text=str(year),
            )
        )
    return facts


def _fact_set(
    data: FactInput,
    status: FactSetStatus,
    facts: list[Fact],
    evidence_ids: list[str],
    notes: set[FactNoteCode],
    dataset_slug: str | None,
) -> FactSet:
    return FactSet(
        fact_set_id=fact_set_id_for(data.story_id, status, facts),
        story_id=data.story_id,
        status=status,
        research_status=data.research_status,
        facts=facts,
        evidence_ids=evidence_ids,
        notes=_ordered_notes(notes),
        dataset_slug=dataset_slug,
    )


def fact_input_for(research: ResearchOutput, story: StoryInput | None = None) -> FactInput:
    """Map 050 research output into the pure builder's input."""
    return FactInput(
        story_id=research.story_id,
        research_status=research.status,
        evidence=research.evidence,
        story=story,
    )


def build_fact_set(data: FactInput) -> FactSet:
    """Build a bounded, deterministic fact set from dataset evidence only."""
    notes: set[FactNoteCode] = set()

    if data.research_status is ResearchStatus.UNSUPPORTED:
        notes.add(FactNoteCode.RESEARCH_UNSUPPORTED)
        return _fact_set(data, FactSetStatus.UNSUPPORTED, [], [], notes, None)

    dataset_evidences = [item for item in data.evidence if item.kind is EvidenceKind.DATASET]
    chosen: EvidenceSource | None = None
    for item in dataset_evidences:
        if item.dataset is not None and item.dataset.rows:
            chosen = item
            break
    if chosen is None:
        notes.add(
            FactNoteCode.NO_DATASET_EVIDENCE
            if not dataset_evidences
            else FactNoteCode.NO_NUMERIC_ROWS
        )
        return _fact_set(data, FactSetStatus.EMPTY, [], [], notes, None)

    dataset = chosen.dataset
    if dataset is None:
        raise ValueError("dataset evidence without a dataset")
    if len(dataset_evidences) > 1:
        notes.add(FactNoteCode.ADDITIONAL_DATASET_IGNORED)

    series_by_key = _collect_series(dataset.rows)
    if not series_by_key:
        notes.add(FactNoteCode.NO_NUMERIC_ROWS)
        return _fact_set(data, FactSetStatus.EMPTY, [], [], notes, None)

    series = list(series_by_key.values())
    if any(item.duplicate for item in series):
        notes.add(FactNoteCode.DUPLICATE_OBSERVATIONS_COLLAPSED)
    if not any(len(item.points) >= 2 for item in series):
        notes.add(FactNoteCode.INSUFFICIENT_SERIES)

    selected, matched = _select_series(series, data.story)
    if matched:
        notes.add(FactNoteCode.STORY_ENTITIES_MATCHED)

    metric = dataset.title
    facts: list[Fact] = []
    for item in selected:
        facts.extend(_entity_facts(item, chosen, dataset, metric, notes))
    facts.extend(_cross_entity_facts(series, selected, chosen, dataset, metric, notes))

    if len(facts) > data.max_facts:
        facts = facts[: data.max_facts]
        notes.add(FactNoteCode.FACT_BUDGET_EXHAUSTED)

    return _fact_set(
        data,
        FactSetStatus.AVAILABLE,
        facts,
        [chosen.evidence_id],
        notes,
        dataset.slug,
    )
