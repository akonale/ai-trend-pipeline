# 071: Jev general-audience AI news classification

Status: built

## Goal

Add a TypeSafe Jev classification stage before the existing Anthropic editorial writer so only
AI-centered stories with suitable general-audience appeal reach story candidates. Jev supplies
typed judgments; deterministic code applies named Strict, Balanced, and Relaxed policies and owns
every accept/reject decision.

## Acceptance criteria

- [ ] Add the pinned `typesafe-sdk` dependency with `uv` and use its async client in a new,
      idempotent `classify_story_events` activity. Read `TYPESAFE_API_KEY` and a versioned
      `TYPESAFE_MODEL` only through `config.py`; pin the default to the current stable Jev version
      rather than a moving alias so tuned thresholds do not drift silently.
- [ ] Keep all Jev question wording, criteria, policy thresholds, and decision rules together in
      `story_classification.py`. Follow the TypeSafe skill: narrow atomic judgments, structured
      state, code-owned decisions, and no generated prose from Jev.
- [ ] Ask independent questions for each event covering: whether AI or its effects are central;
      human interest; ease of understanding for a non-technical reader; whether the item is routine
      enterprise/technical/industry news; and whether it has an explicit everyday impact. Use Noul
      for yes/no judgments and Score with concrete level descriptions for ordered judgments.
- [ ] Send only relevant normalized event data to Jev. Exclude score, comments, and other engagement
      metrics from model state so popularity cannot influence classification. Treat titles and URLs
      as untrusted data, and do not send credentials, prompts, or unrelated application state.
- [ ] Bundle each event's questions into shared requests rather than making one request per
      question. Deterministically chunk large batches to remain within the documented Jev context
      limits without enabling long-context mode; preserve input order and return exactly one bounded
      result per input `external_id`.
- [ ] Validate response question IDs, result completeness, probability/score ranges, and event-ID
      mapping in code. Missing, duplicate, malformed, or out-of-range answers raise a typed
      non-retryable `ApplicationError`; transient SDK/API errors propagate for Temporal retry. An
      API failure is never converted into a negative classification.
- [ ] Implement a pure, total decision function over the typed judgments and a policy table with
      `strict`, `balanced`, and `relaxed` modes. `strict` is the default and selects only
      exceptional, clearly understandable human-interest stories; `balanced` and `relaxed`
      progressively lower the human-interest/clarity gates and soften the routine-industry
      exclusion. AI centrality remains a hard gate in every mode.
- [ ] The truth table gives every judgment combination a deterministic outcome. A routine
      enterprise/industry classification can be overridden only by sufficiently strong explicit
      everyday impact under that mode; it never overrides AI centrality, human-interest, or clarity
      gates. Do not use answer `confidence` as a pass/fail gate.
- [ ] Calibrate question criteria with the supplied examples without string-matching their titles:
      “370 Books in 20 months: South African author defends her use of AI” should fit the positive
      human-interest criteria, while ClickHouse/data-platform acquisition news should fit the
      routine enterprise-infrastructure exclusion.
- [ ] Integrate classification into `SourceIngestionWorkflow` behind a named Temporal patch. The
      flow becomes prefilter → Jev classify → deterministic policy filter → existing Anthropic
      `select_story_candidates` over eligible events only. The Anthropic activity remains responsible
      for comparing eligible stories, collapsing overlaps, and writing summary/angle/reason; it may
      still select none.
- [ ] A successful classification with zero eligible events skips Anthropic and marks the batch
      processed. After exhausted classification retries, log a fixed sanitized failure, leave the
      batch unprocessed for a later poll, start no candidates, and make no Anthropic call. Existing
      source polling, budgets, duplicate suppression, and post-selection failure behaviour otherwise
      stay unchanged.
- [ ] Add optional/defaulted workflow input fields and replay gates so pre-071 ingestion histories
      replay unchanged. New activity and workflow boundaries use bounded Pydantic models; no I/O,
      SDK calls, wall-clock access, or randomness occurs in workflow code.
- [ ] Register the Jev activity/client with the ingestion worker and add sanitized
      `candidate_ingestion.classification` and `candidate_ingestion.classification_failed` events.
      Logs may contain mode, model version, and counts, never titles, URLs, question text, answers,
      payloads, or secrets.
- [ ] Add recorded TypeSafe SDK/API fixtures for positive, negative, mixed, uncertain, chunked,
      malformed, and transient-error cases. Tests make no live TypeSafe, Anthropic, or other HTTP
      calls and do not assert exact live-model probabilities.
- [ ] Update `.env.example`, production worker configuration, README, architecture, deployment and
      ingestion docs for the new credential, pinned model, classification flow, and three modes.
      Ruff, strict Pyright, and the full pytest suite pass; chart, story-generation, media, and
      publishing behaviour remain unchanged.

## Design notes

- Jev classifies; code decides; Anthropic writes. Do not ask Jev “should this story be selected?”
  and do not replace deterministic thresholds with a broad LLM prompt.
- Keep classification results internal to ingestion. Do not add them to candidate workflow inputs,
  visibility attributes, history payloads beyond the existing bounded activity result, or the UI.
- Use fixed policy definitions in code in this task. Task 072 adds the global persisted UI control
  and per-poll policy loading; this task only supplies the shared mode enum and default `strict`
  workflow input needed by that integration.
- Initial thresholds are starting points, not claims of measured accuracy. Test policy boundaries
  and relative Strict/Balanced/Relaxed behaviour with fixed judgments; any live calibration requires
  separate user permission, an explicit budget, and disclosure of the data and API key used.

## Files to touch

- `pyproject.toml`
- `uv.lock`
- `src/ai_trend_pipeline/story_classification.py` (new)
- `src/ai_trend_pipeline/story_selection.py` (align editorial guidance only)
- `src/ai_trend_pipeline/source_ingestion.py`
- `src/ai_trend_pipeline/ingestion_logging.py`
- `src/ai_trend_pipeline/config.py`
- `src/ai_trend_pipeline/worker.py`
- `tests/test_story_classification.py` (new)
- `tests/test_story_selection.py`
- `tests/test_source_ingestion.py`
- `tests/test_ingestion_logging.py`
- `tests/fixtures/typesafe_story_classification_*.json` (new recorded fixtures)
- `tests/fixtures/histories/source_ingestion_*.json` (replay fixtures as needed)
- `.env.example`
- `infra/compose.prod.yaml`
- `README.md`
- `docs/architecture/README.md`
- `docs/architecture/business.md`
- `docs/architecture/tech.md`
- `docs/runbooks/deploy.md`
- `docs/runbooks/ingestion.md`

## Tests to write

- `test_classification_input_and_result_are_bounded_and_validated`
- `test_questions_are_atomic_typed_and_exclude_engagement_metrics`
- `test_question_criteria_cover_author_positive_and_clickhouse_negative`
- `test_large_batches_chunk_deterministically_and_preserve_event_order`
- `test_missing_duplicate_or_unknown_answers_fail_closed`
- `test_transient_typesafe_errors_retry_and_invalid_results_do_not`
- `test_policy_truth_table_covers_every_judgment_combination`
- `test_ai_centrality_remains_required_in_every_mode`
- `test_strict_balanced_relaxed_have_monotone_eligibility`
- `test_everyday_impact_override_is_bounded_by_policy`
- `test_engagement_and_confidence_do_not_change_eligibility`
- `test_zero_eligible_events_skip_anthropic_and_complete_the_batch`
- `test_classification_failure_leaves_batch_unprocessed_and_starts_nothing`
- `test_only_eligible_events_reach_existing_editorial_selection`
- `test_pre_071_ingestion_histories_replay`
- `test_classification_logs_are_sanitized`

## Out of scope

- No news-filter UI, Postgres policy store, arbitrary numeric threshold editor, or per-user policy
  (072).
- No retroactive reclassification or removal of existing candidates.
- No article fetching, new source connectors, semantic deduplication, or candidate ranking by
  popularity.
- No Jev-generated summaries, angles, reasons, story text, or media.
- No live API calibration in CI or the normal test loop.
- No candidate review, story generation, chart, media, publishing, or Bluesky behaviour changes.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: Revised the spec to use TypeSafe Jev for typed judgments, deterministic policy code for decisions, and the existing Anthropic activity only for editorial writing. Next: approve, then builder writes recorded-fixture and policy tests first.
**builder**: Added `story_classification.py` (Jev Noul/Score questions, policy table, pure `decide`, `classify_story_events`), patched `story-classification-v1` into `SourceIngestionWorkflow`, config/worker/compose/docs; pinned `typesafe-sdk==0.7.5`, default `jev-1.14.0`.
**builder**: Fixtures `typesafe_story_classification_*.json` are hand-built in the recorded API shape (no live call was made, none permitted); pre-071 replay history generated from unmodified code. Policy thresholds are starting points, uncalibrated.
**builder**: Also touched, outside the listed files, to keep existing tests green: `tests/test_worker.py`, `test_source_ingestion_e2e.py`, `test_source_ingestion_two_sources.py`, `story_e2e_harness.py`, new `tests/typesafe_fixtures.py` (ingestion workers now need the classifier activity).
