# 048: Continuous HN ingestion

Status: built

## Goal

Add one long-running `SourceIngestionWorkflow` per enabled source that polls through 042's
`poll_hn_source` activity, keeps a bounded cursor and processed-ID state, prefilters new events,
calls 043's `select_story_candidates`, and starts 044's `StoryCandidateWorkflow` only for selected
stories. Add an explicit, idempotent operator command that ensures the configured source workflows
exist; do not start them implicitly from worker startup.

## Mechanism decision: explicit operator command (chosen)

Start source workflows with a dedicated command, not on worker startup:

- `just ingest` (dev) and `docker compose -f infra/compose.prod.yaml --env-file .env.prod exec
  worker python -m ai_trend_pipeline.ingestion_starter` (prod) run a thin entry point that loads
  settings, connects, builds `TemporalSourceIngestionGateway`, and calls
  `SourceIngestionService.ensure()`.
- The service starts one workflow per source in `Settings.source_enabled` and the static registry,
  through the gateway, with id `source-ingestion-v1-<source>`,
  `WorkflowIDConflictPolicy.USE_EXISTING` and `WorkflowIDReusePolicy.ALLOW_DUPLICATE`, so repeated
  and concurrent runs are safe and a terminated chain can be restarted.
- `SOURCE_ENABLED=none` ensures nothing and exits successfully.

Why this is the safer option: starting ingestion has external cost (HN HTTP and Anthropic calls), so
it should be an explicit operator action rather than a side effect of every worker start, dev
launch, CI process, or extra worker replica. A forgotten command is visible (an empty inbox) and
recoverable by re-running it; the docs and `docs/runbooks/deploy.md` name the deploy step. The
worker still registers the ingestion workflow and activities, so the command only schedules work.

## Ingestion-state design

All state lives in `SourceIngestionWorkflow`; no Postgres table, migration, or separate cursor
store.

| Concern | Design |
| --- | --- |
| Workflow id | `source-ingestion-v1-<source.value>` (`source_ingestion_workflow_id`). Versioned so an incompatible future ingestion rewrite can use a new chain. |
| Task queue | `SOURCE_INGESTION_TASK_QUEUE = "source-ingestion-task-queue"`; separate from the chart and candidate queues. |
| Cursor | `cursor: str \| None`, an opaque per-source watermark. For HN it is the highest numeric `external_id` seen; `advance_cursor` leaves a non-numeric cursor unchanged. It is never used for filtering; it is for observability and future connectors. |
| Processed IDs | `processed_ids: tuple[str, ...]`, at most `MAX_PROCESSED_IDS = 1000`, first-seen order, duplicates rejected by the model. An ID is added when its event is considered (selected, not selected, or selection failed), so accepted batches are never re-evaluated while inside the bound. FIFO eviction is intentional; evicted stories can be re-evaluated and are protected by deterministic candidate IDs. |
| Per-run counters | `polls_in_run` (reset on continue-as-new) and `total_polls` (preserved). Both increment for each poll attempt, including a failed poll. |
| Continue-as-new | After a poll, if `polls_in_run >= max_polls_per_run` (default `50`, bounds 1-500) or `workflow.info().is_continue_as_new_suggested()`, pass a new `SourceIngestionInput` with the current cursor, processed IDs and `total_polls`, and `polls_in_run=0`. The same workflow ID continues. |
| Stop / cancellation | A `stop` signal returns `SourceIngestionResult(reason="stopped")` after the current activity. Cancellation is allowed to propagate and never cancels candidate children because they use `ParentClosePolicy.ABANDON`. |
| Query | `ingestion_state() -> SourceIngestionState` returns source, cursor, processed IDs, counters, `last_poll_at` from `workflow.now()`, and the last poll/new/selected counts. |

Loop shape: poll by `get_source(source).poll_activity` -> filter events already in
`processed_ids` -> `prefilter_events(fresh)` (043's pure helper; dedupe, order, cap) -> if the batch
is non-empty, call `select_story_candidates` -> mark the batch processed -> start a candidate child
for each of the first `max_candidates` selected IDs still present in the batch -> check stop, then
continue-as-new, then wait for
`poll_interval_seconds` with `workflow.wait_condition(lambda: self._stop_requested, timeout=...)`.
Use `workflow.now()` for `last_poll_at`; never `datetime.now`, `time.time`, `random`, `uuid4`,
`asyncio.sleep`, `httpx`, or a Temporal client.

Failures: the poll activity uses a 60 s timeout and a 3-attempt `RetryPolicy`; the selector uses
120 s and 3 attempts. After retries, an `ActivityError` is logged (no titles or secrets) and the
loop continues: a failed poll advances only the counters, and a failed selection marks that batch
processed so a poisoned batch is not retried forever. Cancellation exceptions inside an
`ActivityError` are re-raised. A duplicate child start (`WorkflowAlreadyStartedError`, because child
starts have no conflict policy) is logged and ignored.

Candidate children: `workflow.start_child_workflow(StoryCandidateWorkflow.run, StoryCandidateInput(
candidate=..., review_timeout_seconds=...), id=candidate_id_for(source, external_id),
task_queue=CANDIDATE_TASK_QUEUE, parent_close_policy=workflow.ParentClosePolicy.ABANDON,
id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE, search_attributes=...,
memo=...)`. `ABANDON` is mandatory: continue-as-new and parent cancellation must not close awaiting
candidates. Search attributes and memo come from shared pure helpers in `story_candidate.py` so the
ingestion path and `TemporalCandidateGateway` cannot drift. The ingestion workflow itself gets no
`CandidateStatus`/`CandidateSource` attributes and is therefore never listed by the inbox.

## Acceptance criteria

- [ ] `source_ingestion.py` defines frozen, `extra="forbid"`, bounded `SourceIngestionInput`,
      `SourceIngestionState` and `SourceIngestionResult`, the versioned workflow id, the new task
      queue, and `SourceIngestionWorkflow`. The workflow polls via the registry activity name,
      uses Temporal timers, `workflow.now()` and `workflow.continue_as_new()`, and contains no I/O,
      wall-clock, randomness, `asyncio.sleep`, or Temporal-client calls.
- [ ] Cursor and processed-ID state are bounded and preserved across continue-as-new; the cursor is
      the HN max numeric external ID, processed IDs are a unique 1000-entry FIFO, `polls_in_run`
      resets while `total_polls` accumulates, and continue-as-new happens after `max_polls_per_run`
      (default 50) or `is_continue_as_new_suggested()`. A maximum bounded input round-trips through
      `pydantic_data_converter` under 256 KB.
- [ ] Only unprocessed events reach the selector; `prefilter_events` handles dedupe/order/cap; an
      empty batch skips the selector; selection and poll failures are non-fatal; a selection failure
      marks its batch processed. Selected events start `StoryCandidateWorkflow` children with the
      deterministic candidate ID, `CANDIDATE_TASK_QUEUE`, `ABANDON`, `REJECT_DUPLICATE` and the
      shared search-attribute/memo helpers, at most `max_candidates`, and duplicate
      `WorkflowAlreadyStartedError` is ignored.
- [ ] `SourceIngestionService.ensure()` over a `SourceIngestionGateway` starts exactly one workflow
      per enabled registry source with `source-ingestion-v1-<source>`; the Temporal adapter uses
      `USE_EXISTING` and `ALLOW_DUPLICATE` and is idempotent; `just ingest` and the documented prod
      `docker compose exec worker ...` form run the thin entry point; worker startup does not;
      `SOURCE_ENABLED=none` starts none; and `SourceIngestionInput` maps `SOURCE_POLL_LIMIT`,
      `SOURCE_POLL_INTERVAL_S` (new, default 300, bounds 30-3600) and
      `CANDIDATE_REVIEW_TIMEOUT_S`.
- [ ] The worker registers `SourceIngestionWorkflow` plus `poll_hn_source` and
      `select_story_candidates` on the ingestion queue without changing the chart/candidate queues
      or `DailyChartWorkflow`. The ingestion workflow is never listed by
      `CandidateService.list_pending`, an HN-fixture run creates a pending candidate visible through
      `CandidateService.list_pending`/`show`, `TemporalChartRunGateway.count_started_since` still
      returns zero chart runs, and existing chart/candidate tests pass unchanged.
- [ ] Time-skipping tests cover multiple polls, empty selections, repeated top-story IDs, no
      re-evaluation within the processed bound, duplicate candidate suppression, selection caps,
      poll retry, persistent poll failure, selector failure, cursor/state across continue-as-new,
      stop and cancellation, and the no-I/O source guard. The end-to-end visibility assertion uses
      `start_local` because the time-skipping server lacks `ListWorkflowExecutions` (044's finding);
      all HN/LLM calls use fixtures. `just lint` and `just test` pass.

## Files to touch

- `src/ai_trend_pipeline/source_ingestion.py` (new): constants, `source_ingestion_workflow_id`,
  `SourceIngestionInput`, `SourceIngestionState`, `SourceIngestionResult`, pure
  `filter_new_events` / `advance_cursor` / `append_processed_ids`, and
  `SourceIngestionWorkflow`.
- `src/ai_trend_pipeline/services/ingestion.py` (new): `SourceIngestionGateway` protocol,
  `SourceIngestionService.ensure()`, and `source_ingestion_input(settings, source)`.
- `src/ai_trend_pipeline/ingestion_starter.py` (new): thin `python -m` / `just ingest` entry point
  (load settings, connect, build the gateway, call the service, print only ensured IDs).
- `src/ai_trend_pipeline/story_candidate.py` (modify): add pure
  `candidate_search_attributes(candidate)` and `candidate_memo(candidate)`; reuse existing models,
  `candidate_id_for`, `CANDIDATE_TASK_QUEUE`, and attribute keys.
- `src/ai_trend_pipeline/services/temporal_gateway.py` (modify): add
  `TemporalSourceIngestionGateway` (start with `USE_EXISTING` + `ALLOW_DUPLICATE`, catch
  `WorkflowAlreadyStartedError`), and use the two shared helper functions in
  `TemporalCandidateGateway.start`; no candidate behaviour change.
- `src/ai_trend_pipeline/worker.py` (modify): add the ingestion worker on
  `SOURCE_INGESTION_TASK_QUEUE` with `INGESTION_WORKFLOWS = [SourceIngestionWorkflow]` and
  `ingestion_activities(...)` (`poll_hn_source` via `HNSourceActivities` with
  `make_source_hn_client(settings)`, `select_story_candidates` via `StorySelectionActivities`);
  do not call the service or the starter.
- `src/ai_trend_pipeline/config.py` (modify): `source_poll_interval_s: int = 300` (30-3600) and
  `SOURCE_POLL_INTERVAL_S`.
- `justfile` (modify): `ingest` recipe.
- `tests/test_source_ingestion_models.py` (new)
- `tests/test_source_ingestion.py` (new, time-skipping workflow tests)
- `tests/test_source_ingestion_e2e.py` (new, `start_local` visibility/service-boundary test)
- `tests/test_ingestion_service.py` (new, fake gateway)
- `tests/test_ingestion_starter.py` (new, real Temporal gateway against a test environment)
- `tests/test_story_candidate_models.py` (modify: helper tests only)
- `tests/test_worker.py`, `tests/test_config.py` (modify)
- `tests/fixtures/llm_select_story_candidates_ingestion.json` (new: one selection for HN id `2001`;
  reuse `tests/fixtures/hn_source_poll.json` for the HN poll)
- `docs/architecture/tech.md` (workflow/task queue/state/operator command, module map, settings
  table, worker wiring, test note)
- `docs/architecture/README.md` (module-map and state-table rows; a short ingestion-flow note)
- `README.md` (Configuration and a short continuous-ingestion/operator note)
- `CLAUDE.md` (commands table: `just ingest`)
- `docs/runbooks/deploy.md` (one line: run the idempotent ensure command after deploy; 049 expands)

## Tests to write

`tests/test_source_ingestion_models.py`

- `test_input_defaults_and_bounds`: source, poll limit, interval, max candidates, max polls, review
  timeout, cursor, empty processed IDs; reject every lower/upper violation and unknown fields.
- `test_processed_ids_must_be_unique_and_bounded`: duplicate IDs and 1001 entries raise; blank and
  over-long IDs raise.
- `test_input_round_trips_through_pydantic_data_converter_under_256kb`: 1000 max-length IDs, max
  cursor and max text/settings survive and serialize below 256 KB.
- `test_filter_new_events_skips_processed_and_preserves_order`: processed IDs are excluded; first
  occurrence wins; input order is preserved.
- `test_filter_new_events_caps_the_batch`: more than `MAX_BATCH_EVENTS` fresh events truncate.
- `test_advance_cursor_takes_max_numeric_id`: numeric IDs advance monotonically; no numeric IDs and
  a non-numeric cursor are preserved.
- `test_append_processed_ids_keeps_fifo_bound`: duplicates are not re-added and the last `limit`
  IDs are kept (use a small explicit limit).
- `test_workflow_id_is_versioned_and_source_specific`: prefix plus source value; differs per source.
- `test_state_and_result_models_are_frozen_bounded_and_forbid_extra_fields`.

`tests/test_source_ingestion.py` (one `start_time_skipping()` environment per test; register the
candidate search attributes; fake activities are registered under the exact names
`poll_hn_source` and `select_story_candidates`; use the `stop` signal or a fake activity counter so
no test depends on wall-clock time). If the time-skipping server answers `UNIMPLEMENTED` to the
search-attribute operator RPC (044's fallback), use `start_local` with the attributes pre-registered
for the child-start tests and keep the timer/continue-as-new tests in time-skipping; record the
fallback in the handoff.

- `test_polls_multiple_times_on_temporal_timers`: two or more polls; the fake activity is called
  again only after the interval; `last_poll_at` advances with `workflow.now()`.
- `test_empty_selection_starts_no_candidate_and_advances_state`: selector returns zero; batch IDs
  become processed; no child workflow; loop continues.
- `test_repeated_top_story_ids_are_not_reselected`: the same poll output twice calls the selector
  once and starts one candidate.
- `test_processed_ids_prevent_reevaluation_within_the_bound`: seed `processed_ids` with one story;
  the selector sees only the fresh story and starts only its candidate.
- `test_duplicate_candidate_start_is_ignored_and_one_candidate_remains`: pre-start the candidate,
  then run ingestion; `WorkflowAlreadyStartedError` is swallowed, the run is not failed, and the
  candidate stays awaiting review.
- `test_selection_caps_candidate_starts`: real `StorySelectionActivities` with the over-cap fixture
  and `max_candidates=2`; exactly two candidate workflows start.
- `test_poll_activity_retry_then_success`: the fake poll fails twice and succeeds on the third
  attempt; the workflow processes the successful output and starts the candidate.
- `test_persistent_poll_failure_keeps_the_loop_alive`: the fake poll always fails; the workflow
  counts attempts, keeps polling, and a later `stop` returns a stopped result instead of failing.
- `test_selector_failure_skips_the_batch_and_continues`: a non-retryable selector error marks the
  batch processed, starts no candidate, and is not retried for the same batch.
- `test_continue_as_new_preserves_cursor_and_processed_ids`: `max_polls_per_run=1`; the next run
  receives the cursor and processed IDs, `polls_in_run` resets, `total_polls` accumulates, and a
  repeated story is neither re-selected nor duplicated.
- `test_stop_signal_returns_stopped_result`: after at least one poll, `stop` returns
  `SourceIngestionResult(reason="stopped")` and no further poll occurs.
- `test_cancellation_stops_ingestion_and_leaves_candidates_running`: cancel the ingestion workflow
  and assert it is cancelled while an awaiting candidate is still queryable (`ABANDON`).
- `test_ingestion_state_query_tracks_counts`: source, cursor, processed count, `polls_in_run`,
  `total_polls`, `last_poll_at` and the last event/new/selected counts.
- `test_workflow_source_has_no_io_wall_clock_or_random_calls`: AST check of `source_ingestion.py`
  for no `httpx`, `anthropic`, `temporalio.client`, `services`, `web`, `sources.hn`, `hn`,
  `daily_chart`, `datetime.now`, `time.time`, `random.`, `uuid.uuid4`, `asyncio.sleep`; assert
  `workflow.now`, `workflow.wait_condition`, `workflow.continue_as_new` and
  `start_child_workflow` are present.

`tests/test_source_ingestion_e2e.py` (`WorkflowEnvironment.start_local` with the candidate search
attributes pre-registered; two workers: ingestion queue with the workflow plus real
`poll_hn_source` over the `httpx.MockTransport` handler pattern from `tests/test_sources_hn.py`
using `hn_source_poll.json`, and real `select_story_candidates` over the new recorded fixture with
the fake Anthropic client pattern from `tests/test_story_selection.py`; `CANDIDATE_TASK_QUEUE` with
`StoryCandidateWorkflow`)

- `test_hn_fixture_becomes_a_pending_candidate_through_candidate_service`: poll until
  `CandidateService(TemporalCandidateGateway, Settings).list_pending` returns one candidate; assert
  its title/source/angle, `show` returns `awaiting_review`, and the source link is the fixture URL.
- `test_ingestion_workflow_is_not_a_candidate_or_chart_run`: while the source workflow runs,
  `list_pending` contains only the candidate; `WorkflowType = 'SourceIngestionWorkflow'` matches the
  source but has no candidate attributes; `TemporalChartRunGateway.count_started_since` returns 0;
  `pending_candidates_query()` names only `StoryCandidateWorkflow`.
- `test_end_to_end_run_stops_cleanly`: send `stop`, await the result, and assert no new candidate
  appears after the stop.

`tests/test_ingestion_service.py` (fake `SourceIngestionGateway`, no Temporal)

- `test_source_ingestion_input_maps_settings`: poll limit, interval, review timeout, default
  `max_candidates`, empty cursor/processed IDs and zero counters.
- `test_ensure_starts_one_workflow_per_enabled_source`: one gateway `start` per registry source in
  registry order, with the expected input.
- `test_ensure_starts_none_when_no_sources_enabled`: `source_enabled=frozenset()`; no gateway
  calls; empty result.
- `test_ensure_returns_ensured_ids_and_uses_configured_settings`.
- `test_service_reports_gateway_errors_without_swallowing_them`.

`tests/test_ingestion_starter.py` (`WorkflowEnvironment.start_local`; real
`TemporalSourceIngestionGateway`; a unique task queue is not needed because the workflow is not
executed)

- `test_gateway_starts_one_workflow_per_enabled_source`: `describe` the `source-ingestion-v1-hn`
  workflow on `SOURCE_INGESTION_TASK_QUEUE`.
- `test_gateway_is_idempotent`: ensure twice; the same first run id and one execution per source.
- `test_gateway_starts_none_when_no_sources_enabled`: `list_workflows` finds no
  `SourceIngestionWorkflow`.
- `test_starter_main_prints_only_ensured_ids_and_raises_on_connect_failure` (patch `connect`; no
  source text or secrets printed).

`tests/test_story_candidate_models.py` (extend)

- `test_candidate_search_attributes_helper_sets_awaiting_status_and_source`.
- `test_candidate_memo_helper_has_only_the_three_compact_keys`.

`tests/test_worker.py` (extend)

- `test_ingestion_workflow_and_activities_registered_on_ingestion_queue`: `INGESTION_WORKFLOWS`,
  queue name, and activity names `poll_hn_source` / `select_story_candidates`.
- `test_worker_startup_does_not_start_ingestion`: AST/source check that `worker.py` does not
  import or call `SourceIngestionService`, `TemporalSourceIngestionGateway`, or
  `ingestion_starter`.
- Existing `test_daily_chart_activities_names` and candidate-queue test pass unchanged.

`tests/test_config.py` (extend)

- `test_source_poll_interval_default_and_bounds`: default 300; 30 and 3600 accepted; 29, 3601,
  `abc`, `2.5` rejected; empty means default; `Settings()` equals `load_settings({})`.

Existing tests that must pass unchanged: `tests/test_daily_chart.py` (including the replay test),
`tests/test_temporal_gateway.py`, `tests/test_candidate_gateway.py`,
`tests/test_candidate_service.py`, `tests/test_sources_*.py`, `tests/test_story_selection.py`, and
the web candidate tests. Do not edit `hn.py`, `daily_chart.py`, `chart_loop.py`, or the chart
routes.

## Implementation notes

- **Workflow models.** `SourceIngestionInput` (frozen, `extra="forbid"`): `source: SourceName`;
  `poll_limit` 1-100 default 30; `poll_interval_seconds` 30-3600 default 300; `max_candidates`
  1-20 default `DEFAULT_MAX_CANDIDATES`; `max_polls_per_run` 1-500 default 50;
  `review_timeout_seconds` 1-604800 default `DEFAULT_CANDIDATE_REVIEW_TIMEOUT_S`; `cursor`
  optional, stripped, max 256; `processed_ids` tuple of 1-128-char IDs, max 1000, unique;
  `polls_in_run` and `total_polls` non-negative. `SourceIngestionState` has `source` (typed
  `SourceName | None = None` so the query is safe before the first task), `cursor`,
  `processed_ids`, `polls_in_run`, `total_polls`, `last_poll_at: datetime | None`,
  `last_event_count`, `last_new_event_count` and `last_selected_count`.
  `SourceIngestionResult` has `source`, `reason: Literal["stopped"]`, `total_polls`, `cursor`.
- **Imports.** Put Pydantic, `sources.models`, `sources.registry`, `story_candidate` and
  `story_selection` (`SelectStoryCandidatesInput`, `SelectStoryCandidatesOutput`,
  `prefilter_events`, `DEFAULT_MAX_CANDIDATES`, `MAX_CANDIDATES`, `MAX_BATCH_EVENTS`) inside
  `workflow.unsafe.imports_passed_through()`. Import `StoryCandidateWorkflow` there too. Never
  import `sources.hn`, `services`, `web`, `daily_chart`, or `worker` into the workflow module.
  Import `RetryPolicy`, `WorkflowIDReusePolicy`, `WorkflowAlreadyStartedError`, `ActivityError` and
  `is_cancelled_exception` from the SDK. The search attributes themselves come from
  `candidate_search_attributes`.
- **Activity calls.** Use `workflow.execute_activity(definition.poll_activity, SourcePollInput(...),
  start_to_close_timeout=timedelta(seconds=60), retry_policy=RetryPolicy(maximum_attempts=3),
  result_type=SourcePollOutput)` and the same shape with
  `SELECT_STORY_CANDIDATES_ACTIVITY = "select_story_candidates"`, 120 s and
  `result_type=SelectStoryCandidatesOutput`. Do not import the activity functions. The string-name
  overload is typed `Any` in this SDK; bind each result with
  `cast(SourcePollOutput, await ...)` / `cast(SelectStoryCandidatesOutput, await ...)` and a short
  comment rather than leaving an unexplained `Any`.
- **Child starts.** Child starts cannot set `WorkflowIDConflictPolicy` in this SDK; use
  `id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE` and catch
  `WorkflowAlreadyStartedError`. Do not await the child result. Build `StoryCandidate` from the
  matched `SourceEvent` plus `SelectedStory.summary/proposed_angle/selection_reason`; skip a
  selection whose `external_id` is not in the batch (log a warning). Use
  `candidate_search_attributes` and `candidate_memo` from `story_candidate.py`, and the
  `candidate_id_for` ID. Set no execution or run timeout on the ingestion workflow.
- **Loop.** Check `self._stop_requested` before the first poll, after each poll and before
  continue-as-new. `_next_input` copies the configuration fields and current state and resets
  `polls_in_run`. `workflow.continue_as_new(next_input)` is a no-return call in this SDK; do not
  fall through to another poll. `workflow.wait_condition` is the timer; `stop` wakes it.
- **Service and gateway.** `SourceIngestionService` takes a `SourceIngestionGateway` and
  `Settings`. `ensure()` iterates `enabled_sources(settings.source_enabled)` in registry order,
  builds `source_ingestion_input(settings, source)` (passes `source_poll_limit`,
  `source_poll_interval_s` and `candidate_review_timeout_s`; lets `max_candidates` use the model
  default), calls `gateway.start(source, input)`, and returns the ensured IDs. Do not catch gateway
  errors in the service; the command must fail visibly. `TemporalSourceIngestionGateway.start`
  computes the ID, calls `client.start_workflow` with
  `id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING`,
  `id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE` and
  `task_queue=SOURCE_INGESTION_TASK_QUEUE`, catches `WorkflowAlreadyStartedError` defensively,
  and returns the ID. The thin `ingestion_starter.main()` loads settings, calls `connect(settings)`,
  builds the service and prints only the ensured IDs; it raises on error and prints no source text
  or secrets.
- **Worker.** Add `make_source_hn_client(settings)` to the `async with` block and a fourth
  `Worker` on `SOURCE_INGESTION_TASK_QUEUE` with `INGESTION_WORKFLOWS` and
  `ingestion_activities(anthropic_client, source_hn_client)`. Keep the existing hello, chart and
  candidate workers and `daily_chart_activities` unchanged. Do not call the service or the
  ingestion starter from `main()`.
- **Settings.** Add `source_poll_interval_s: int = Field(default=300, ge=30, le=3600)` and parse
  `SOURCE_POLL_INTERVAL_S` in `load_settings` with the existing "empty means default" style.
- **Docs and mechanism.** Document the operator command, the new setting, the task queue, the
  workflow id, the state location, and the prod `docker compose exec worker` form. Say explicitly
  that worker startup does not start ingestion.
- **No UI.** No templates, CSS, view models, routes, or frontend-design work in this task.

## Out of scope

- Per-source budgets, structured operational logs, runbooks (beyond the one-line deploy note),
  second-source registration, and source enable/disable lifecycle (stop a disabled source's
  existing workflow): task 049.
- Changing 042 contracts or connectors (`sources/models.py`, `sources/hn.py`, `normalize.py`,
  `registry.py`, `fake.py`), 043's selector/prefilter semantics or fixtures, or 044's candidate
  workflow/gateway/service behaviour.
- Postgres tables, migrations, a cursor/processed-ID table, or an inbox table.
- Web routes, templates, CSS, view models, navigation, or the candidate decision flow (already
  045-047).
- Chart generation, publishing, Bluesky, media, or any `DailyChartWorkflow`/chart activity/route
  change.
- Semantic cross-source deduplication; a fake/second source; Google Trends or RSS.
- Starting ingestion from worker startup or from a web request.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

- **planner**: Wrote the spec. No UI, so the frontend-design skill was not used. Chose the explicit
  idempotent operator command, not worker startup (external HTTP/LLM side effects should be
  deliberate). Assumptions not in the plan: queue `source-ingestion-task-queue`, id
  `source-ingestion-v1-<source>`, `SOURCE_POLL_INTERVAL_S=300` (30-3600), `MAX_PROCESSED_IDS=1000`,
  `max_polls_per_run=50`, HN cursor = max numeric ID, candidate children use `ABANDON`, poll/selector
  failures are non-fatal, a `stop` signal exists, e2e visibility uses `start_local`. Next: human approves.
- **builder**: Added ingestion workflow/models/helpers, service/gateway/starter, worker/config wiring,
  shared candidate attribute/memo helpers, `just ingest` and docs. Poll/selector failures are non-fatal;
  `ABANDON`/`REJECT_DUPLICATE` children and the 1000-ID state bound are covered. `wait_condition` raises
  `TimeoutError` on timeout (caught); time-skipping tests step timers with `env.sleep(...)`.
  `just lint` and `just test` pass (654 tests; Anthropic base-url vars unset). Next: verifier.
