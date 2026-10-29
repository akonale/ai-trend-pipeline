# 044: StoryCandidateWorkflow and Temporal gateway

Status: built

## Goal

Add one durable Temporal workflow per selected story candidate, with deterministic
`candidate-v1-<hash>` workflow IDs, a `review_state` query, Approve/Reject signals, a configurable
review timeout, and terminal states `approved`, `rejected`, `expired`. Add the service/gateway
boundary that starts candidates idempotently, lists pending candidates with pagination, reads an
open candidate, and signals a decision, and index `CandidateStatus` / `CandidateSource` through
Temporal visibility. No source polling or UI in this task.

## Acceptance criteria

- [ ] `StoryCandidateWorkflow` runs on `CANDIDATE_TASK_QUEUE = "story-candidate-task-queue"`;
      `run(StoryCandidateInput) -> StoryCandidateResult` starts `awaiting_review`, completes
      `approved` on the `approve` signal, `rejected` on `reject`, and `expired` after
      `review_timeout_seconds` with no decision. `review_state` returns the current
      `CandidateReviewState`. A decision after the first one, or after any terminal state, is
      ignored and logged: the first terminal decision wins.
- [ ] `candidate_id_for(source, external_id)` returns `candidate-v1-` plus the first 16 hex chars of
      SHA-256 over `f"{source.value}\x1f{external_id}"`; the same pair always yields the same ID and
      different pairs differ. Starting the same source/external ID twice (while running or after it
      closed) leaves exactly one workflow run.
- [ ] `CandidateStatus` and `CandidateSource` are Keyword search attributes registered
      idempotently. A candidate is started with `CandidateStatus=awaiting_review` and
      `CandidateSource=<source>`; the terminal transition upserts the final status. Pending
      candidates are listed with a visibility query and opaque pagination tokens.
- [ ] `CandidateService` over the `CandidateGateway` protocol provides `start`, `list_pending`,
      `show`, and `decide`. Missing candidates map to `CandidateNotFound`; candidates that are no
      longer `awaiting_review` (including closed and lost races) map to `CandidateClosed`. The
      review timeout comes from `Settings.candidate_review_timeout_s`.
- [ ] Every workflow, signal, result, summary, and page model is Pydantic, bounded, and
      `extra="forbid"`. A max-length `StoryCandidateInput` round-trips through
      `pydantic_data_converter` and serializes to less than 4 KB.
- [ ] The worker registers the candidate workflow and search attributes. `DailyChartWorkflow` and
      its activities are unchanged; the existing DailyChart replay test still passes; `just lint`
      and `just test` pass.

## Files to touch

- `src/ai_trend_pipeline/story_candidate.py` (new): models, `CandidateStatus`,
  `DEFAULT_CANDIDATE_REVIEW_TIMEOUT_S`, `candidate_id_for`, the search-attribute keys, and
  `StoryCandidateWorkflow`.
- `src/ai_trend_pipeline/services/candidates.py` (new): `CandidateGateway` / `CandidateHandle`
  protocols, `CandidateService`, request/result/page models, and typed service errors.
- `src/ai_trend_pipeline/services/temporal_gateway.py` (modify): add `TemporalCandidateGateway`
  and `TemporalCandidateHandle`; keep the existing chart gateway behaviour intact.
- `src/ai_trend_pipeline/config.py` (modify): add `candidate_review_timeout_s` and its env var.
- `src/ai_trend_pipeline/temporal_client.py` (modify): add
  `ensure_candidate_search_attributes(client)`, importing the key constants from
  `story_candidate.py`.
- `src/ai_trend_pipeline/worker.py` (modify): call `ensure_candidate_search_attributes`, expose
  `CANDIDATE_WORKFLOWS = [StoryCandidateWorkflow]`, and add a third worker on
  `CANDIDATE_TASK_QUEUE` (no activities).
- `tests/test_story_candidate_models.py` (new)
- `tests/test_story_candidate.py` (new, time-skipping workflow tests)
- `tests/test_candidate_gateway.py` (new, real adapter against the test environment)
- `tests/test_candidate_service.py` (new, fake gateway)
- `tests/test_worker.py` (modify)
- `tests/test_config.py` (modify)

## Implementation notes

- **Models** in `story_candidate.py`:
  - `StoryCandidate` (frozen, `extra="forbid"`): `source: SourceName`, `external_id` (1-128),
    `title` (1-200), `url: HttpUrl1024 | None`, `published_at: datetime | None`,
    `observed_at: datetime`, `summary`, `proposed_angle`, `selection_reason` (each stripped,
    1-500), `metrics` (at most 4, reusing `MetricKey`/`MetricValue`). Reuse
    `ensure_aware_utc`, `HttpUrl1024`, `MAX_*` from `sources/models.py`. Define the 500-character
    constant locally (do not import `story_selection.py`, which pulls in Anthropic).
  - `StoryCandidateInput`: `candidate: StoryCandidate`, `review_timeout_seconds: int = 172800`
    (`ge=1`, `le=604800`). Export `DEFAULT_CANDIDATE_REVIEW_TIMEOUT_S = 172800` and let
    `config.py` import it so the two defaults cannot drift.
  - `CandidateDecision` (frozen): `note` (default `""`, max 500), `decided_by` (optional, max 320,
    blank stripped to `None`).
  - `CandidateReviewState`: `status: CandidateStatus`, `candidate`, `note: str = ""`,
    `decided_by: str | None = None`, `decided_at: datetime | None = None`.
  - `StoryCandidateResult`: `status: Literal["approved", "rejected", "expired"]`, `candidate`,
    `note`, `decided_by`, `decided_at`.
  - `CandidateStatus(StrEnum)`: `AWAITING_REVIEW = "awaiting_review"`, `APPROVED = "approved"`,
    `REJECTED = "rejected"`, `EXPIRED = "expired"`.
- **Workflow**: import Pydantic and the source models inside
  `workflow.unsafe.imports_passed_through()` as `daily_chart.py` does. `decided_at` uses
  `workflow.now()`. Upsert only `CANDIDATE_STATUS_ATTRIBUTE` on the terminal transition; the
  gateway sets both attributes at start (see below). No activities, I/O, clocks, or randomness in
  workflow code. Signal handlers stay synchronous and ignore decisions when
  `self._decision is not None` or the state is not `awaiting_review` (log with
  `workflow.logger.warning`).
- **Search attributes**: constants
  `CANDIDATE_STATUS_ATTRIBUTE = SearchAttributeKey.for_keyword("CandidateStatus")` and
  `CANDIDATE_SOURCE_ATTRIBUTE = SearchAttributeKey.for_keyword("CandidateSource")` live in
  `story_candidate.py`. `ensure_candidate_search_attributes(client)` in `temporal_client.py` calls
  `client.operator_service.add_search_attributes(AddSearchAttributesRequest(namespace=client.namespace, search_attributes={"CandidateStatus": INDEXED_VALUE_TYPE_KEYWORD, ...}))`
  and ignores `RPCError` with `RPCStatusCode.ALREADY_EXISTS`. The worker calls it once after
  connecting; tests call it once per environment. If the time-skipping test server answers
  `UNIMPLEMENTED` to this operator RPC, use
  `WorkflowEnvironment.start_local(search_attributes=[CANDIDATE_STATUS_ATTRIBUTE, CANDIDATE_SOURCE_ATTRIBUTE])`
  for the SA-dependent tests and `review_timeout_seconds=1` for expiration; record the fallback in
  the handoff.
- **Start (gateway)**: call `client.start_workflow` with `id=candidate_id`,
  `task_queue=CANDIDATE_TASK_QUEUE`, `id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING`,
  `id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE`, and
  `search_attributes=TypedSearchAttributes([SearchAttributePair(CANDIDATE_STATUS_ATTRIBUTE, "awaiting_review"), SearchAttributePair(CANDIDATE_SOURCE_ATTRIBUTE, candidate.source.value)])`.
  Catch `WorkflowAlreadyStartedError` and treat it as success: the existing candidate is the
  idempotent result. Memo keys (compact list data, no per-row workflow query):
  `candidate_title` (str), `candidate_proposed_angle` (str), `candidate_observed_at` (ISO 8601 str).
- **List**: `pending_candidates_query()` returns
  `WorkflowType = 'StoryCandidateWorkflow' AND CandidateStatus = 'awaiting_review'`. Fetch one
  page with `client.list_workflows(...).fetch_next_page()`, read `current_page` and
  `next_page_token`, and build `CandidateSummary(candidate_id, source, title, proposed_angle,
  observed_at, status)`. Source/status come from the typed search attributes; title/angle/observed
  time come from memo (defaults: empty string / `None` when absent). Encode the server token as
  URL-safe base64 (no padding) for `CandidatePage.next_token`; a malformed token raises
  `InvalidPageToken`. Default page size 20, max 100.
- **Service errors**: `CandidateServiceError` base with `CandidateNotFound(candidate_id)`,
  `CandidateClosed(candidate_id, status)`, `InvalidPageToken`. `CandidateService` has
  `start(candidate: StoryCandidate, review_timeout_seconds: int | None = None) ->
  StartCandidateResult`, `list_pending(ListCandidatesRequest) -> CandidatePage`,
  `show(candidate_id) -> CandidateReviewState`, and `decide(DecideCandidateRequest) ->
  StoryCandidateResult`. `show` and `decide` first query `review_state`; a NOT_FOUND from the query
  is `CandidateNotFound`, a non-`awaiting_review` state is `CandidateClosed`. A NOT_FOUND from the
  signal/result after the pre-check is also `CandidateClosed` (lost race). `decide` sends the
  matching signal and returns `handle.result()`.
- **Worker**: run three workers concurrently; expose `CANDIDATE_WORKFLOWS = [StoryCandidateWorkflow]`
  so the wiring test can assert it, and give the candidate worker no activities. Keep the existing
  chart and hello worker wiring unchanged.

## Tests to write

- `tests/test_story_candidate_models.py`:
  - `test_candidate_id_is_deterministic_and_versioned` (prefix, 16 hex chars, repeat calls equal).
  - `test_candidate_id_changes_with_source_or_external_id`.
  - `test_story_candidate_input_round_trips_through_pydantic_data_converter_under_4kb` (max-length
    text, URL and metrics).
  - `test_story_candidate_rejects_extra_fields_overlong_text_bad_urls_and_too_many_metrics`.
  - `test_candidate_decision_strips_decided_by_and_bounds_note`.
  - `test_page_token_round_trips_and_invalid_token_raises`.
- `tests/test_story_candidate.py` (time-skipping environment, one worker, no activities):
  - `test_ensure_candidate_search_attributes_is_idempotent` (call twice, then
    `list_search_attributes` shows both names).
  - `test_approve_completes_approved_and_sets_terminal_status_attribute`.
  - `test_reject_completes_rejected`.
  - `test_no_decision_expires_after_review_timeout`.
  - `test_first_terminal_decision_wins` (send approve and reject without awaiting the first;
    tolerate a NOT_FOUND on the second because the workflow may already be closed, and assert the
    result is the decision that was sent first; repeat with reject first).
  - `test_decisions_after_terminal_or_expiry_are_ignored` (invalid-signal cases: a late signal may
    be rejected by the server; tolerate that, and assert the stored result/query is unchanged).
  - `test_review_state_query_tracks_awaiting_and_final`.
  - `test_visibility_status_changes_from_awaiting_to_terminal`.
- `tests/test_candidate_gateway.py` (real `TemporalCandidateGateway` against a time-skipping
  environment; poll visibility briefly for indexing lag):
  - `test_start_is_idempotent_for_same_source_and_external_id` (one workflow, same candidate ID).
  - `test_start_after_close_returns_existing_candidate` (still one run).
  - `test_list_pending_paginates_and_excludes_decided`.
  - `test_show_maps_missing_and_closed_to_typed_errors`.
  - `test_decide_maps_missing_and_closed_and_returns_result`.
- `tests/test_candidate_service.py` (fake gateway):
  - `test_start_derives_id_and_uses_settings_timeout`.
  - `test_start_twice_returns_same_id_and_one_gateway_start`.
  - `test_list_pending_passes_limit_and_page_token`.
  - `test_show_and_decide_raise_typed_errors`.
- `tests/test_worker.py`:
  - `test_candidate_workflow_registered_on_candidate_queue`.
- `tests/test_config.py`:
  - `test_candidate_review_timeout_default_and_bounds` (default 172800, empty means default,
    rejects 0, 604801, and non-numeric values).
- Existing `tests/test_daily_chart.py::test_closed_approved_run_replays_with_post_uri` must pass
  unchanged; do not edit `daily_chart.py`.

## Out of scope

- No source polling, ingestion workflow, cursor state, or calls to the 043 selector (task 048).
- No web routes, templates, view models, `web/dependencies.py`, CSS, or navigation (tasks 045-047).
- No Postgres tables, candidate/source-event/cursor/inbox persistence, or migrations.
- No chart generation, media, publishing, or Bluesky work from a candidate workflow.
- No per-source budgets, operational logging, runbooks, or second-source work (task 049).
- Do not change `DailyChartWorkflow`, `daily_chart.py`, its task queue, or its activities.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

- **planner**: Wrote the spec. No UI, so the frontend-design skill was not used. Next: human
  approves; builder starts from the tests. Assumption: `CandidateSource` SA holds
  `SourceName.value`; list rows come from 3 memo keys; the worker registers search attributes
  idempotently; the review timeout is in seconds (default 48 h) so tests can use short timers.
- **builder**: Added candidate models/workflow, SA registration, Temporal gateway, service, and
  worker wiring with tests. The time-skipping test server lacks ListWorkflowExecutions, so
  visibility/gateway tests use `start_local` with SAs pre-registered; expiry uses time-skipping
  with a 1 s timeout. Frontend-design skill not used (no UI). Next: verifier.
