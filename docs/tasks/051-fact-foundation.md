# 051: Deterministic fact foundation

Status: built

## Goal

Add versioned `Fact` and `FactSet` contracts plus a pure, LLM-free analysis library and activity
that turn 050's numeric evidence into bounded, deterministic facts (changes, comparisons,
first/latest/min/max, rankings, relevant outliers) with stable IDs, source references, observation
dates, calculation lineage, caveats, and compact display values. Unsupported or insufficient
evidence yields no facts, never a fallback.

## Design

### Why this shape

050 already returns bounded `EvidenceSource` items. Only `EvidenceKind.DATASET` items contain
structured numbers (`DatasetEvidence.rows`); article text is untrusted prose and is never turned
into a number. 051 is therefore a pure function over dataset rows: same input bytes produce the same
`FactSet` and the same fact IDs. There is no model call, no HTTP, no clock, no `config.py` setting,
and no workflow change. 053 wires the activity; 052 consumes facts and display values.

### New package `analysis/`

| File | Responsibility |
| --- | --- |
| `analysis/models.py` | Frozen, bounded `Fact`, `FactSet`, `FactInput`, `SourceReference`, `CalculationLineage`, `FactDisplay`, enums, constants, ID helpers. Imports only the stdlib and `research.models`/`sources.models`; no I/O, settings or workflow imports. |
| `analysis/calculations.py` | Pure `build_fact_set(data: FactInput) -> FactSet`, `fact_input_for(research: ResearchOutput, story: StoryInput | None = None) -> FactInput`, grouping/selection/rank/outlier helpers and deterministic formatting. No I/O. |
| `analysis/activities.py` | `FactActivities.calculate_facts`, a sync `@activity.defn` wrapping `build_fact_set`. No clients, no I/O, no `ApplicationError`; not registered. |
| `analysis/__init__.py` | Re-exports contract models and constants only (no calculations/activities import), mirroring `research/__init__.py`. |

`analysis/` must not import `anthropic`, `httpx`, `temporalio.client`, `temporalio.workflow`,
`config.py`, `worker.py`, any workflow module, `services/` or `web/`.

### Constants

| Constant | Value | Use |
| --- | --- | --- |
| `FACT_SCHEMA_VERSION` | `1` | `schema_version` on both models and in ID prefixes |
| `MAX_FACTS` | `32` | facts per set (default and ceiling) |
| `MAX_SERIES_ENTITIES` | `4` | entities that get per-series facts |
| `MAX_FACT_ENTITIES` | `2` | `Fact.entities` (comparison names two) |
| `MAX_LINEAGE_ENTITIES` | `20` | `CalculationLineage.entities` |
| `MAX_SOURCES_PER_FACT` | `4` | `Fact.sources` |
| `MAX_CAVEATS` | `8` | `Fact.caveats` |
| `MAX_FACT_NOTES` | `10` | `FactSet.notes` |
| `MAX_EVIDENCE` | `4` | `FactInput.evidence` |
| `MIN_RANKING_ENTITIES` | `3` | entities needed in the latest common year for cross-entity facts |
| `MIN_OUTLIER_ENTITIES` | `5` | entities needed for IQR outlier detection |
| `MAX_OUTLIER_FACTS` | `2` | outlier facts per set |
| `IQR_FACTOR` | `1.5` | Tukey fence |
| `MAX_METRIC_CHARS` | `200` | `Fact.metric` and lineage titles |
| `MAX_ENTITY_CHARS` | `100` | entity and lineage names |
| `MAX_UNIT_CHARS` | `40` | `Fact.unit` |
| `MAX_FACT_ID_CHARS` | `32` | `fact-v1-` + 16 hex |
| `MAX_FACT_SET_ID_CHARS` | `32` | `fset-v1-` + 16 hex |
| `MAX_DISPLAY_LABEL_CHARS` | `160` | `FactDisplay.label` |
| `MAX_DISPLAY_VALUE_CHARS` | `120` | `FactDisplay.value_text` |
| `MAX_DISPLAY_SUBJECT_CHARS` | `120` | `FactDisplay.subject` |
| `MAX_PERIOD_TEXT_CHARS` | `40` | `FactDisplay.period_text` |
| `MAX_FORMULA_CHARS` | `200` | `CalculationLineage.formula` |
| `MAX_PARAMETERS` | `6` | `CalculationLineage.parameters` |
| `MAX_FACT_SET_BYTES_TARGET` | `64 * 1024` | test-only serialized ceiling |
| `FACT_ID_PATTERN` | `^fact-v1-[0-9a-f]{16}$` | ID validation |
| `FACT_SET_ID_PATTERN` | `^fset-v1-[0-9a-f]{16}$` | ID validation |

### Enums

- `FactKind`: `first`, `latest`, `min`, `max`, `change`, `percent_change`, `comparison`, `ranking`,
  `outlier`.
- `FactMethod`: `series_endpoint` (first/latest/change/percent), `series_extreme` (min/max),
  `max_min` (comparison), `ordinal` (ranking), `iqr_1_5` (outlier).
- `FactCaveatCode`: `single_observation`, `partial_series`, `dataset_truncated`,
  `duplicate_observations_collapsed`, `non_positive_baseline`, `tied_values`,
  `statistical_outlier`.
- `FactNoteCode`: `research_unsupported`, `no_dataset_evidence`, `no_numeric_rows`,
  `additional_dataset_ignored`, `duplicate_observations_collapsed`, `insufficient_series`,
  `insufficient_cross_entity_data`, `non_positive_baseline`, `story_entities_matched`,
  `fact_budget_exhausted`.
- `FactSetStatus`: `available`, `empty`, `unsupported`.

Caveats are deduplicated and emitted in enum declaration order (cap 8). Notes are appended by the
generation algorithm in the order listed above, deduplicated first-wins, capped at 10. Both orders
are part of the deterministic contract.

### Models

All models are frozen, `extra="forbid"`, and bounded. Datetimes are not used: OWID evidence has
years only, and a `FactSet` carries no clock timestamp so the same input serializes byte-identically
on every run; Temporal history records when the activity ran.

`SourceReference`

| Field | Type and bounds |
| --- | --- |
| `evidence_id` | str 1-80 |
| `kind` | `EvidenceKind` (reuse `research.models`) |
| `title` | str 1-200 |
| `url` | `HttpUrl1024 \| None` |
| `publisher` | str `\| None` 1-253 |

`CalculationLineage`

| Field | Type and bounds |
| --- | --- |
| `calculation` | `FactKind`; must equal the owning fact's `kind` |
| `input_evidence_ids` | list[str] 1-4, unique |
| `dataset_slug` | str 1-100 |
| `value_column` | str 1-100 |
| `entities` | list[str] 1-20, unique, bounded order |
| `entity_count` | int `>= 1` (size of the calculation pool) |
| `start_year` | int 1800-2100 |
| `end_year` | int 1800-2100, `>= start_year` |
| `baseline_year` | int `\| None` 1800-2100 |
| `baseline_value` | finite float `\| None` |
| `row_count` | int 0-300 (rows in the chosen dataset evidence) |
| `formula` | str 1-200 (fixed text per calculation, below) |
| `method` | `FactMethod` |
| `parameters` | dict[str, finite float], at most 6, keys 1-40 |

`Fact`

| Field | Type and bounds |
| --- | --- |
| `fact_id` | str, matches `FACT_ID_PATTERN` |
| `schema_version` | int `>= 1`, default `1` |
| `kind` | `FactKind` |
| `entities` | list[str] 1-2, each 1-100, unique |
| `metric` | str 1-200 (`DatasetEvidence.title`) |
| `value` | finite float; `-0.0` normalised to `0.0` |
| `unit` | str `\| None` 1-40; `"%"`, `"ratio"`, or `None` (never inferred) |
| `observation_dates` | list[date] 1-4, unique, ascending |
| `period_start_year` | int 1800-2100 |
| `period_end_year` | int 1800-2100, `>= period_start_year` |
| `sources` | list[`SourceReference`] 1-4 |
| `lineage` | `CalculationLineage` |
| `caveats` | list[`FactCaveatCode`] at most 8, unique |
| `display` | `FactDisplay` |

`FactDisplay`

| Field | Type and bounds |
| --- | --- |
| `label` | str 1-160, whitespace-collapsed, control characters removed |
| `value_text` | str 1-120 |
| `subject` | str 1-120 |
| `period_text` | str 1-40 |

`FactSet`

| Field | Type and bounds |
| --- | --- |
| `fact_set_id` | str, matches `FACT_SET_ID_PATTERN` |
| `schema_version` | int `>= 1`, default `1` |
| `story_id` | str 1-64 (the 050 `ResearchOutput.story_id`) |
| `status` | `FactSetStatus` |
| `research_status` | `ResearchStatus` (reuse `research.models`) |
| `facts` | list[`Fact`] at most 32, unique `fact_id`, all `schema_version`s equal the set's |
| `evidence_ids` | list[str] at most 4, unique; every `Fact.sources[].evidence_id` must be in it |
| `notes` | list[`FactNoteCode`] at most 10, unique |
| `dataset_slug` | str `\| None` 1-100 |

`FactSet` invariants:

- `available` requires at least one fact, a non-empty `evidence_ids`, and a `dataset_slug`;
  `research_status` must be `supported`.
- `empty` forbids facts and `evidence_ids`; `research_status` must be `supported`.
- `unsupported` forbids facts and `evidence_ids`, requires `research_status == unsupported` and the
  `research_unsupported` note, and has `dataset_slug = None`.

`FactInput`

| Field | Type and bounds |
| --- | --- |
| `story_id` | str 1-64 |
| `research_status` | `ResearchStatus` |
| `evidence` | list[`EvidenceSource`] at most 4 |
| `story` | `StoryInput \| None = None` (`research.models`) |
| `max_facts` | int 1-32, default `MAX_FACTS` |

If `story` is set, `story.candidate_id` must equal `story_id`. `fact_input_for` maps a
`ResearchOutput` (its `story_id`, `status`, `evidence`) plus an optional `StoryInput`, so 053 does
not pass article context text through the fact activity.

### Stable IDs

`fact_id_for(kind, entities, metric, value, period_start_year, period_end_year, evidence_ids)`
hashes the canonical JSON list
`[FACT_SCHEMA_VERSION, kind.value, list(entities), metric, value, period_start_year,
period_end_year, list(evidence_ids)]` with `json.dumps(..., ensure_ascii=False,
separators=(",", ":"))` and UTF-8, then returns `fact-v1-` + the first 16 hex characters of
SHA-256. `fact_set_id_for(story_id, status, facts)` hashes
`[FACT_SCHEMA_VERSION, story_id, status.value, [f.fact_id for f in facts]]` the same way and
returns `fset-v1-` + 16 hex.

IDs contain no clock, retrieval time, list position or display text. The same input therefore
produces the same IDs; a changed value, year, metric, entity or evidence ID changes the fact ID.
Both helpers are public and unit-tested.

### Calculations: selection and rules

The builder uses the first `EvidenceKind.DATASET` evidence item, in input order, that has at least
one row. Any further dataset evidence adds `additional_dataset_ignored`. Article evidence is never
a numeric source.

**Series grouping.** Group rows by `entity.strip().casefold()`; the display name is the first
source-order spelling for that key, so case/whitespace duplicates merge. No alias/code merging is
attempted. Within a group, sort by `(year, source_index)`; a repeated `(entity, year)` keeps the
last source-order row. Duplicates add the `duplicate_observations_collapsed` set note and caveat to
that entity's facts.

**Entity order.** With `story`, entities whose casefolded key occurs as a whole word in
`title + "\n" + summary + "\n" + proposed_angle` come first, ordered by first match position; add
`story_entities_matched`. Remaining entities follow by observation count descending, latest year
descending, display name casefold ascending. Keep `MAX_SERIES_ENTITIES = 4`.

**Facts per selected entity**, in this order:

| Kind | Emit when | `value` | `unit` | Dates / period | Lineage |
| --- | --- | --- | --- | --- | --- |
| `first` | always | earliest-year value | None | first year | `[entity]`, count 1, start=end=first year, baseline None, `series_endpoint` |
| `latest` | always | latest-year value | None | latest year | `[entity]`, count 1, start=end=latest year, baseline None, `series_endpoint` |
| `change` | at least 2 distinct years | `round(latest - first, 6)` | None | first/latest years, dates for both | `[entity]`, count 1, baseline year/value, `series_endpoint`, formula `latest_value - first_value` |
| `percent_change` | ≥2 years and `first > 0` | `round((latest - first) / first * 100, 6)` | `"%"` | first/latest years | same baseline lineage, formula `100 * (latest_value - first_value) / first_value` |
| `min` | minimum value's earliest year is strictly between first and last | minimum value | None | that year | `[entity]`, count 1, start=end=year, baseline None, `series_extreme` |
| `max` | maximum value's earliest year is strictly between first and last | maximum value | None | that year | same as `min`, `series_extreme` |

- `first`/`latest` carry `single_observation` when the series has one year.
- `latest`, `change`, `percent_change` carry `partial_series` when the sorted years have a gap > 1.
- When `first <= 0`, omit `percent_change`, add `non_positive_baseline` to the `change` caveats and
  the set notes.
- All facts from a `DatasetEvidence.truncated` dataset carry `dataset_truncated`.
- `observation_dates` are `date(year, 1, 1)`; the last date is the most recent observation.

**Cross-entity facts** use the latest common year: the highest year with at least
`MIN_RANKING_ENTITIES = 3` distinct entities. If none, skip comparison/ranking/outliers and add
`insufficient_cross_entity_data`. The pool is every entity with a value in that year.

| Kind | Rule |
| --- | --- |
| `comparison` (difference) | `max_entity - min_entity` over `(value desc, name asc)`; entities `[max, min]`; `unit=None`; `method=max_min`; formula `max_latest - min_latest`; lineage `entities=[max, min]`, `entity_count=len(pool)`, start=end=year |
| `comparison` (ratio) | only when `min_entity.value > 0`; `unit="ratio"`, value `round(max / min, 6)`, formula `max_latest / min_latest`; otherwise omit it and add `non_positive_baseline` to the difference fact |
| `ranking` | rank pool by `(value desc, name asc)`, unique ordinal ranks; emit one fact per selected entity in the pool; `value` = that entity's value, `unit=None`; `parameters={"rank": float(rank)}`; `method=ordinal`; formula `ordinal_rank(latest_value, descending, ties_by_entity_name)`; `entities=[entity]`, `entity_count=len(pool)`; `tied_values` caveat when another pool entity shares the value |
| `outlier` | requires `len(pool) >= 5`; Tukey hinges: sort values, median, Q1 = median of the lower half (excluding the overall median when `n` is odd), Q3 = median of the upper half; strict outside `[Q1 - 1.5*IQR, Q3 + 1.5*IQR]`; order by absolute distance from the median desc then name asc; emit at most 2; `value` = entity value, `parameters` in this key order `q1, q3, iqr, median, lower_bound, upper_bound`; `method=iqr_1_5`; `statistical_outlier` caveat |

Every fact cites the chosen dataset evidence in `sources` (`kind=dataset`, its `evidence_id`,
`title`, `url=None`, `publisher=None`) and has exactly one `input_evidence_ids` entry.

**Generation and budget.** Build in this order: for each selected entity `[first, latest, change,
percent_change, min, max]`, then cross-entity `[comparison difference, comparison ratio, ranking
facts in rank order, outlier facts in outlier order]`. Truncate to `input.max_facts`; if anything
was dropped, add `fact_budget_exhausted`. Compute IDs and the set ID afterwards.

### Display values

Formatting is fixed and locale-independent:

- `format_number(value)` = `f"{value:.4g}"`, normalising `-0.0` to `0.0`.
- point/`min`/`max`/outlier: `value_text = format_number(value)`.
- `change`: `value_text = f"{value:+.4g}"` (signed, no unit).
- `percent_change`: `value_text = f"{value:+.1f}%"`.
- comparison difference: `format_number(value)`; comparison ratio: `f"{value:.2f}x"`.
- ranking: `value_text = f"#{rank} of {entity_count}"`.

`subject` is the entity name, or `"{max} vs {min}"` for comparison. `period_text` is `"2020"` or
`"2000-2020"`. `label` is:

- point/change/percent: `"{metric}, {entity}, {period_text}"`
- comparison: `"{metric}, {max} vs {min}, {year}"`
- ranking: `"{metric}, rank of {entity}, {year}"`
- outlier: `"{metric}, outlier {entity}, {year}"`

Every display field is whitespace-collapsed, has control characters removed, and is truncated to
its cap after cleaning. Display text never contains article/context prose, prompt text, URLs, or
story text other than entity names used for matching.

### Omission rules (no guessing)

- `research_status == unsupported` → `FactSet(status="unsupported", facts=[])` with
  `research_unsupported`, even when dataset rows exist.
- No dataset evidence → `empty` + `no_dataset_evidence`; dataset evidence with no rows →
  `empty` + `no_numeric_rows`.
- No entity with two years → no change/percent facts; first/latest still emitted with
  `single_observation`.
- Non-positive first value → no percent change.
- Non-positive comparison minimum → no ratio.
- Fewer than 3 pool entities → no cross-entity facts.
- Fewer than 5 pool entities → no outlier facts.
- Endpoint minima/maxima → no duplicate `min`/`max` fact.
- Never synthesise a value, a year, a unit, an entity, or a fact from story text, article text, or
  an LLM. `unit` is never inferred from column names.

### Activity boundary

`FactActivities` takes no constructor arguments. `calculate_facts(self, data: FactInput) ->
FactSet` is a sync `@activity.defn` that returns `build_fact_set(data)`. It is pure, read-only,
idempotent and safe to retry; expected data conditions are result values, not exceptions. It starts
no workflow, calls no Temporal client, logs nothing, and is not registered by `worker.py`. Because
it is sync, 053 must give the worker an activity executor and a short start-to-close timeout when
registering it. No `config.py` or settings change is needed.

### Determinism and bounds

The same `FactInput` must produce identical `model_dump_json()` bytes, including fact order, note
and caveat order, lineage entity order, parameter key order, and IDs. Floats are normalised with
`round(x, 6)` for computed values and `-0.0` → `0.0`; raw point values are kept as parsed. The
serialized ceiling is 64 KiB; a near-maximum set must serialize below it. Lists are capped by the
constants above.

## Acceptance criteria

- [ ] `analysis/models.py` defines `Fact`, `FactSet`, `FactInput`, `SourceReference`,
      `CalculationLineage`, `FactDisplay`, the five enums, all constants, and the stable
      `fact-v1-`/`fset-v1-` ID helpers; models are frozen, `extra="forbid"`, bounded, and enforce
      the status/source/schema/period/date invariants above.
- [ ] `analysis/calculations.py` implements first, latest, min, max, change, percent change,
      comparison (difference and ratio), ranking (ordinal, name tiebreak) and IQR outliers over the
      first usable 050 dataset evidence, with the documented entity/order/omission rules; only
      dataset rows can produce facts.
- [ ] Every fact carries a stable ID, one source reference to the dataset evidence, observation
      dates, period years, complete calculation lineage (including formula, method, baseline,
      entity count and outlier parameters), ordered caveats, and bounded display values.
- [ ] Unsupported or insufficient research produces a `FactSet` with zero facts and a specific
      status/note; no fallback, guessed, or story-text-derived fact can be produced.
- [ ] `FactActivities.calculate_facts` is a pure, sync, LLM-free, I/O-free activity wrapping the
      builder; it is idempotent, starts no workflow or Temporal client, and is not registered or
      referenced by `worker.py`.
- [ ] Identical inputs produce byte-identical `FactSet` JSON and IDs across runs; a near-maximum
      set serializes under 64 KiB; no workflow, worker, config, service, web, template, CSS, or
      existing chart/candidate behaviour changes; `just lint` (Ruff + strict Pyright) and
      `just test` pass.

## Files to touch

- `src/ai_trend_pipeline/analysis/__init__.py` (new; re-export models/constants only)
- `src/ai_trend_pipeline/analysis/models.py` (new)
- `src/ai_trend_pipeline/analysis/calculations.py` (new)
- `src/ai_trend_pipeline/analysis/activities.py` (new)
- `tests/fact_fixtures.py` (new; deterministic `FactInput`/dataset builders, no I/O)
- `tests/test_fact_models.py` (new)
- `tests/test_fact_calculations.py` (new)
- `tests/test_fact_activities.py` (new)
- `docs/architecture/README.md` (module map row for `analysis/`)
- `docs/architecture/tech.md` (activity table row, a short "Facts (`analysis/`)" subsection,
  determinism/omission notes)
- `docs/tasks/051-fact-foundation.md` (status and handoff log only)

No `README.md` change: this task adds no user-facing behaviour or configuration. Do not edit
`config.py`, `owid.py`, `research/`, `sources/`, `worker.py`, `story_candidate.py`,
`source_ingestion.py`, `story_selection.py`, `daily_chart.py`, `chart_loop.py`, `services/`,
`web/`, templates, CSS, or existing tests.

## Tests to write

`tests/test_fact_models.py`

- `test_fact_is_frozen_bounded_and_rejects_extra_fields`: entity/source/caveat caps, ID pattern,
  finite value, `-0.0` normalisation, extra field rejection, frozen assignment raises.
- `test_fact_validates_period_order_observation_dates_and_lineage_kind`: start > end raises,
  dates must be unique/ascending, `lineage.calculation` must equal `kind`, `entities` bounded.
- `test_fact_set_status_invariants_and_source_links`: available needs facts/evidence/dataset,
  empty and unsupported forbid facts, unsupported requires `research_unsupported`, every source ID
  is in `evidence_ids`, schema versions agree.
- `test_fact_input_limits_and_story_id_must_match`: max 4 evidence, `max_facts` 1-32, frozen,
  mismatched `story.candidate_id` raises, `story=None` is allowed.
- `test_fact_models_round_trip_json_and_near_max_serialize_under_64_kib`: JSON round-trip preserves
  every field; a near-maximum set is under the byte target.
- `test_fact_id_helpers_are_stable_and_seed_sensitive`: same seed gives the same `fact-v1-`/
  `fset-v1-` ID; changing kind, entity, metric, value, year or evidence ID changes it; pattern and
  length hold.

`tests/test_fact_calculations.py`

- `test_first_and_latest_values_years_and_observation_dates`: exact values, years, dates, lineage
  formula/method/entities/entity_count, sources.
- `test_min_and_max_are_interior_extremes_with_their_years`: interior extrema emitted with the
  right year; endpoint extrema produce no duplicate min/max fact.
- `test_change_and_percent_change_values_units_and_baseline_lineage`: exact delta/percent,
  rounding, `unit="%"`, baseline year/value, `series_endpoint`.
- `test_percent_change_omitted_for_zero_and_negative_baselines`: no percent fact, `change` carries
  `non_positive_baseline`, set note present.
- `test_comparison_uses_max_and_min_in_latest_common_year`: difference and ratio values, entities
  order, `max_min`, formula, `entity_count`; ratio omitted when the minimum is non-positive.
- `test_ranking_is_ordinal_with_name_tiebreak_and_tie_caveat`: ranks in `parameters`, display
  `#N of M`, only selected entities, `tied_values` when values tie.
- `test_outliers_use_tukey_quartiles_and_are_bounded`: q1/q3/iqr/bounds parameter order, strict
  fences, deviation ordering, two-fact cap, no outliers below five entities.
- `test_partial_series_and_single_observation_caveats`: a gap adds `partial_series`; one row adds
  `single_observation` and no change/percent facts.
- `test_duplicate_entities_and_years_are_collapsed_deterministically`: case/whitespace duplicates
  merge to the first spelling, repeated entity-year keeps the last row, note and caveat appear.
- `test_missing_data_returns_empty_fact_sets_with_reasons`: no dataset, all-empty rows, no common
  year and no multi-year series each return the documented status/notes and zero facts.
- `test_unsupported_research_cannot_produce_fallback_facts`: dataset rows plus unsupported status
  yields `unsupported`, zero facts, no evidence IDs.
- `test_story_text_never_becomes_a_numeric_fact`: a number in the title/summary that is absent from
  the rows never appears in a fact or display value.
- `test_story_entity_mentions_prioritize_entity_order`: a mentioned entity is first and
  `story_entities_matched` appears; without mentions the completeness/name order is used.
- `test_every_lineage_field_is_populated_for_each_calculation`: one fixture producing all nine
  kinds asserts every lineage field (including `row_count`, `parameters`, `method`, baseline).
- `test_fact_budget_truncates_in_documented_order`: a small `max_facts` keeps the first facts in
  the documented order and adds `fact_budget_exhausted`.
- `test_identical_inputs_produce_identical_serialized_fact_sets`: two builds yield equal
  `model_dump_json()` bytes, equal fact order, notes/caveats/parameters order, and equal IDs.

`tests/test_fact_activities.py`

- `test_calculate_facts_activity_matches_pure_builder`: `ActivityEnvironment` output equals
  `build_fact_set` for the same input.
- `test_calculate_facts_is_idempotent_across_calls`: two activity runs produce identical JSON.
- `test_unsupported_story_activity_returns_no_fallback_facts`: via the activity, unsupported input
  returns zero facts and the unsupported status.
- `test_analysis_modules_are_llm_io_free_and_not_registered`: AST import check rejects
  `anthropic`, `httpx`, `temporalio.client`, `temporalio.workflow`, `config`, workflow, service
  and web modules; `worker.py` contains no `FactActivities` reference.

`tests/fact_fixtures.py` (helper, not a test module)

- Deterministic builders for dataset evidence, story input, research status and multi-entity rows
  used by all three test modules; no files, network, clock or randomness.

## Out of scope

- No `StoryGenerationWorkflow`, orchestration, retries, registration, task queue, visibility or
  candidate handoff (053). Do not register `FactActivities` or touch `worker.py`.
- No LLM writer, critique, prompt, `StoryPackage`, model setting or Anthropic client (052).
- No fact table, Postgres, migration or search attribute; facts live in the workflow result.
- No web routes, view models, templates, CSS, `/stories`, status badges or UI (054-055).
- No publishing, Bluesky, chart generation, rendering, or `DailyChartWorkflow` changes.
- No new dependency, no `config.py`/settings change, no fetch/OWID changes, no article-text
  numeric extraction, no entity alias resolution, no semantic relevance scoring.
- No live HTTP, LLM or Temporal calls in tests; no clock or randomness in the builder.
- No `README.md`, `docs/history.md`, `docs/mvp-plan.md`, runbook or deploy changes.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

- **planner**: Wrote the spec; no UI, so `frontend-design` was not applied. Next: human approves.
- Pinned: new `analysis/` package; facts come only from 050 `dataset` evidence; sync pure
  `calculate_facts` activity, unregistered until 053; no settings, workflow or UI changes.
- Assumptions: up to 4 series entities (story-mentioned first, then completeness), latest-common-
  year comparison/ranking/outliers, 32-fact budget, 5-entity IQR minimum, no timestamp in
  `FactSet` so identical inputs serialize byte-identically.
- Omission policy: unsupported/empty research returns zero facts; percent change needs a positive
  baseline; ratio needs a positive minimum; units are never inferred.
- **builder**: Added `analysis/` models, pure `build_fact_set`/`fact_input_for`, and the sync
  `calculate_facts` activity, with fixtures and three test modules covering every spec case.
- Deterministic IDs/formatting, dataset-only facts, ordered caveats/notes, 32-fact budget; activity
  is unregistered and no worker/config/workflow/service/web/template code changed. Next: verifier.
