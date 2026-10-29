# 049: Candidate-inbox hardening and docs

Status: needs-fix

## Goal

Add a per-source candidate budget, stable structured operational logs, and operator runbooks, then
prove source pluggability by running a fake second source through the complete poll -> select ->
inbox -> review path with the candidate workflow and the 045-047 UI unchanged. Update `README.md`,
the architecture docs and `docs/history.md`, including the stale "nothing starts a run on its own"
statement where it is about candidate ingestion.

No UI work: this task changes no template, CSS, view model, route or JavaScript. The 045-047 pages
and the `SourceName.FAKE` -> `Test source` label already exist and are the boundary the proof runs
through. The `frontend-design` skill is loaded but not applied.

## Budget design: per source, per UTC day

A source can be noisy or broken; the budget keeps its share of the inbox reviewable and bounds its
LLM spend. It is separate from the existing per-poll caps (`SOURCE_POLL_LIMIT`, `max_candidates`).

| Concern | Decision |
| --- | --- |
| Unit | Newly started `StoryCandidateWorkflow` children per source per UTC day. Selections, skipped starts and duplicate starts do not count. |
| Setting | `Settings.source_candidate_budget`, env `SOURCE_CANDIDATE_BUDGET`, default `20`, bounds `1-500`. Each enabled source gets its own full budget, so the inbox ceiling is `budget x enabled sources`. |
| Where enforced | `SourceIngestionWorkflow` (workflow code), because it is the only place candidate children start. The service only maps the setting into `SourceIngestionInput`. The activities never start candidates. |
| Input state | `SourceIngestionInput` gains `candidate_budget: int = 20` (1-500), `budget_day: date \| None = None`, `candidates_started: int = 0` (at least 0). All have defaults so 048-era histories and inputs replay. |
| Day rollover | On every poll, `budget_day_for(workflow.now())` (UTC date). If it differs from `budget_day`, reset `candidates_started` to 0 and store the new day. `workflow.now()` only; no wall clock. |
| Enforcement point 1 | Before selection: `remaining = max(0, candidate_budget - candidates_started)` when the stored day is today. If `remaining == 0`, skip `select_story_candidates` entirely, do **not** mark the batch processed, log `budget_exhausted` once for that day, and continue the loop. This saves LLM calls and keeps stories eligible after midnight. |
| Enforcement point 2 | Otherwise call the selector with `max_candidates=min(data.max_candidates, remaining)` and pass the same limit to `_start_candidates`; stop starting at the limit. The selector's own cap stays unchanged. |
| Processed ids | A batch whose selector call ran is marked processed in full, even when the budget truncated starts. Those stories are intentionally dropped rather than re-evaluated (LLM cost over completeness). A skipped-budget batch is not marked processed. |
| Duplicates | `WorkflowAlreadyStartedError` still suppresses the start, consumes no budget, and logs `candidate_duplicate`. |
| Continue-as-new | `_next_input` carries `budget_day` and `candidates_started`; the new run keeps the same day's count. Terminating and re-ensuring a workflow starts a fresh chain and therefore a fresh day count (documented). |
| Query | `SourceIngestionState` gains `candidate_budget`, `budget_day`, `candidates_started` and `budget_remaining` (computed from stored fields, i.e. "as of the last poll"). |
| Helpers | Pure, in `source_ingestion.py`: `budget_day_for(now: datetime) -> date` and `budget_remaining(budget, started, day, now) -> int`. Testable without Temporal; `workflow.now()` is aware UTC. |
| Service | `source_ingestion_input(settings, source)` passes `candidate_budget=settings.source_candidate_budget`; the starter and `ensure()` are otherwise unchanged. |

Config change procedure (also in the runbook): the budget, poll limit, interval and review timeout
are baked into the input when the workflow starts. `just ingest` uses `USE_EXISTING`, so editing an
env var does not change a running workflow. Stop the source workflow, then re-run `just ingest`.

## Structured operational logs

Add `src/ai_trend_pipeline/ingestion_logging.py`, pure and importable from workflow code (it imports
only `sources.models` and the stdlib).

```python
class IngestionLogEvent(StrEnum): ...


def log_event(
    logger: StructuredLogger,
    event: IngestionLogEvent,
    source: SourceName,
    **fields: str | int | float | bool | None,
) -> None: ...
```

- The log message is `event.value`; payload goes in `extra={"event": event.value, "source":
  source.value, **fields}`. `StructuredLogger` is a minimal Protocol for the SDK `LoggerAdapter`
  types so Pyright strict stays satisfied with no `Any` and no `# type: ignore`.
- String field values are truncated to 256 characters. Non-primitive values raise `ValueError`.
- These field names raise `ValueError` (case-insensitive): `title`, `url`, `summary`,
  `proposed_angle`, `selection_reason`, `instructions`, `prompt`, `response`, `payload`, `body`,
  `text`, `api_key`, `password`, `secret`. The guard, not reviewer discipline, is what keeps
  source text and secrets out.
- Workflow code uses `workflow.logger`; poll activities use `activity.logger`. The selector
  activity emits no success log (the workflow has the batch and budget context); the workflow logs
  its failure type.
- Replay safety: `workflow.logger` suppresses duplicates during replay. No `print()`.

| Event value | Emitted from | Level | Fields (in addition to `event` and `source`) |
| --- | --- | --- | --- |
| `candidate_ingestion.started` | workflow, start of `run` | INFO | `polls_in_run`, `total_polls` |
| `candidate_ingestion.poll` | workflow, after a successful poll | INFO | `poll`, `event_count`, `new_event_count`, `batch_size`, `cursor`, `budget_remaining` |
| `candidate_ingestion.poll_failed` | workflow, after retries are exhausted | ERROR | `poll`, `error_type` |
| `candidate_ingestion.events_normalized` | `poll_hn_source` and `poll_fake_source` | INFO | `requested_limit`, `event_count` |
| `candidate_ingestion.selection` | workflow, after the selector returns | INFO | `batch_size`, `selected_count`, `max_candidates`, `budget_remaining` |
| `candidate_ingestion.selection_failed` | workflow, after retries are exhausted | ERROR | `batch_size`, `error_type` |
| `candidate_ingestion.selection_outside_batch` | workflow | WARNING | `external_id` |
| `candidate_ingestion.candidate_started` | workflow | INFO | `external_id`, `candidate_id`, `budget_remaining` |
| `candidate_ingestion.candidate_duplicate` | workflow | WARNING | `external_id`, `candidate_id` |
| `candidate_ingestion.budget_exhausted` | workflow, once per budget day | WARNING | `day`, `budget`, `candidates_started` |
| `candidate_ingestion.continue_as_new` | workflow, before `continue_as_new` | INFO | `total_polls`, `processed_count`, `cursor` |
| `candidate_ingestion.stopped` | workflow, before returning | INFO | `total_polls` |

`error_type` is `type(exc).__name__` only; never `str(exc)` or a traceback in our events. `poll` and
`cursor` are bounded primitives. Nothing logs titles, URLs, summaries, angles, reasons, prompts,
model output, full source payloads, settings, credentials or complete exception messages. Duplicate
raw events within a poll are visible as `event_count - new_event_count`; already-started candidates
have their own event.

## Fake second source and the pluggability proof

- Register `SourceName.FAKE` in `sources/registry.py`: label `Test source`, poll activity
  `poll_fake_source`. Registry order is `hn`, then `fake`. The registry still imports only models.
- Keep `FakeSourceConnector`. In `sources/fake.py` add:
  - `DEFAULT_FAKE_ITEMS`: two fixed `RawSourceItem`s, ids `fake-1001` and `fake-1002`, plausible
    short headlines, `https://example.com/fake-source/...` URLs, small metrics, no `published_at`.
    Fixed ids mean the source goes quiet after its stories are processed, and duplicate polls can
    never create a second inbox row.
  - `FakeSourceActivities(items: Sequence[RawSourceItem] | None = None)` with
    `@activity.defn async def poll_fake_source(self, data: SourcePollInput) -> SourcePollOutput`:
    uses `datetime.now(UTC)` for `observed_at` (activity context, allowed), honours `data.limit`,
    reuses `normalize_events`, and logs `events_normalized` with counts only.
- Worker: add `FakeSourceActivities().poll_fake_source` to `ingestion_activities` (no client and no
  new env var; it is always registered). Enabling is then only configuration.
- Enablement: `SOURCE_ENABLED` still defaults to `{hn}`, so fake never runs by default and must
  never be put in `.env.prod`. `SOURCE_ENABLED=hn,fake` validates and makes `just ingest` start
  `source-ingestion-v1-hn` and `source-ingestion-v1-fake`. No worker restart is needed for `fake`
  because both activities are registered.
- The deterministic proof is a committed `WorkflowEnvironment.start_local` test with real
  `SourceIngestionWorkflow`, the real HN poll activity over `hn_source_poll.json` via
  `httpx.MockTransport`, the real fake poll activity, the real `select_story_candidates` over a
  mock Anthropic client that returns the HN fixture for HN batches and the new fake fixture for fake
  batches, the real `StoryCandidateWorkflow`, and `CandidateService` over
  `TemporalCandidateGateway`. It asserts one inbox page with exactly one `hn` and one `fake`
  candidate, both `awaiting_review`; approves the fake candidate and rejects the HN candidate; then
  asserts the pending list is empty and both results are terminal. `story_candidate.py`, the
  gateway, the service and all web files are unchanged.
- One source failing must not affect the other: a test registers a non-retryable failing
  `poll_fake_source` and a healthy HN poll, and asserts the HN candidate reaches the inbox while the
  fake workflow logs `poll_failed` and keeps looping.
- Assumption recorded: the manual dev-stack demo runs the real LLM selector, so it may select zero
  fake stories. The committed e2e test is the deterministic proof; the runbook explains how to
  confirm the fake source polled even when the selector chooses nothing.

## Runbooks

Two new files under `docs/runbooks/`, linked from `docs/runbooks/deploy.md`,
`docs/architecture/README.md` and `README.md`. Both define the dev and prod CLI wrappers:

```sh
# dev (Temporal container from `just dev`; the worker runs on the host)
dev-temporal()  { docker compose -f infra/compose.dev.yaml exec temporal temporal "$@"; }
# prod
prod-temporal() { env -i PATH="$PATH" HOME="$HOME" docker compose -f infra/compose.prod.yaml \
  --env-file .env.prod exec temporal temporal "$@"; }
```

If the server image has no `temporal` binary, install the Temporal CLI on the host and pass
`--address <host>:7233`; the commands are otherwise identical.

### `docs/runbooks/search-attributes.md`

1. What/why: keyword `CandidateStatus` and `CandidateSource` drive the pending inbox visibility
   query; the worker registers both at startup through `ensure_candidate_search_attributes`, and
   `ALREADY_EXISTS` is ignored. Missing attributes make candidate starts fail and the inbox show
   its unavailable state.
2. Create:

   ```sh
   dev-temporal operator search-attribute create --name CandidateStatus --type Keyword
   dev-temporal operator search-attribute create --name CandidateSource --type Keyword
   ```

   Use `prod-temporal` in prod. Verify with `dev-temporal operator search-attribute list` and expect
   both names with type `Keyword`.
3. If the worker cannot start, restart it after registering; the worker is the normal path.
4. Dev `start-dev` is in memory, so attributes disappear with `just down`; the worker recreates them
   on the next `just dev`.
5. Tests pre-register the attributes with
   `WorkflowEnvironment.start_local(..., search_attributes=[CANDIDATE_STATUS_ATTRIBUTE,
   CANDIDATE_SOURCE_ATTRIBUTE])`; the time-skipping server has no operator RPC.

### `docs/runbooks/ingestion.md`

1. **Ensure the workflows** (after every deploy and after a source change):

   ```sh
   just ingest
   # prod
   env -i PATH="$PATH" HOME="$HOME" docker compose -f infra/compose.prod.yaml --env-file .env.prod \
     exec worker python -m ai_trend_pipeline.ingestion_starter
   ```

   Verify with
   `dev-temporal workflow describe --workflow-id source-ingestion-v1-hn`, the same for `-fake`, and
   `dev-temporal workflow list --query 'WorkflowType = "SourceIngestionWorkflow"'`. Read state with
   `dev-temporal workflow query --workflow-id source-ingestion-v1-hn --type ingestion_state`.
   `SOURCE_ENABLED=none` ensures nothing and exits 0. Worker startup never starts ingestion.
2. **Enable or disable a source.** Enable: set `SOURCE_ENABLED=hn,fake` in `.env` (never
   `.env.prod` for `fake`), then `just ingest`; no worker restart is needed for a registered
   activity. Disable: remove the name from `SOURCE_ENABLED`, run `just ingest` (it starts nothing
   new but never stops a running workflow), then stop the old workflow:

   ```sh
   dev-temporal workflow signal --workflow-id source-ingestion-v1-fake --name stop
   ```

   The workflow returns `SourceIngestionResult(reason="stopped")` on its next loop. If it is stuck
   in an activity, terminate it instead:

   ```sh
   dev-temporal workflow terminate --workflow-id source-ingestion-v1-fake --reason "source disabled"
   ```

   Pending candidates are child workflows started with `ParentClosePolicy.ABANDON`; they stay
   reviewable and decidable. Re-enabling and running `just ingest` starts a new run of the same id
   (`ALLOW_DUPLICATE`). Also document that changing `SOURCE_POLL_LIMIT`,
   `SOURCE_POLL_INTERVAL_S`, `CANDIDATE_REVIEW_TIMEOUT_S` or `SOURCE_CANDIDATE_BUDGET` requires a
   stop + `just ingest` because settings are baked into the workflow input.
3. **Recover a failed or silent source.** Decision order:
   - Check `describe`/`show` and `ingestion_state`. `FAILED`, `TERMINATED` or `CANCELED`: run
     `just ingest`; with `ALLOW_DUPLICATE` it starts a fresh run.
   - `RUNNING` but `last_poll_at` is stale: confirm the worker is up and on
     `source-ingestion-task-queue`; check the Temporal UI; restart the worker.
   - `budget_remaining: 0`: not a failure; wait for the UTC reset or raise the budget with the
     stop + ensure procedure.
   - Repeated `candidate_ingestion.poll_failed`: the poll activity retries 3 times and the loop
     continues; check the source service and worker logs; no restart needed.
   - `candidate_ingestion.selection_failed`: check `ANTHROPIC_API_KEY`/quota; the failed batch is
     marked processed by design, so new stories continue on later polls.
   - Search-attribute or inbox-unavailable errors: follow the search-attributes runbook; review
     decisions are unaffected.
   - Logs: dev worker output is the `just dev` terminal (or its redirected file); prod is
     `env -i ... logs -f worker`. Filter with `grep candidate_ingestion`.
4. **Replay and continue-as-new upgrades.** Rules: add input/state fields only with defaults; keep
   activity names and loop order; bump `SOURCE_INGESTION_WORKFLOW_ID_PREFIX` to
   `source-ingestion-v2-` for an incompatible loop change (not done in 049). Continue-as-new is
   automatic at `max_polls_per_run` (50) or when the server suggests it and carries cursor,
   processed ids, counters and the budget day/count; no operator action. 049's new fields have
   defaults, so 048-era histories replay. If a deploy produces a non-determinism
   `WorkflowTaskFailed`, roll the worker back first; otherwise stop or terminate the v1 chain and
   `just ingest` a new one. Terminating loses cursor/processed ids/budget-day state, may
   re-evaluate stories, but deterministic candidate ids still prevent duplicate inbox rows.
   Diagnose with `dev-temporal workflow show --workflow-id source-ingestion-v1-hn`.
5. **Two-source local demo.** In `.env`: `SOURCE_ENABLED=hn,fake` and
   `SOURCE_CANDIDATE_BUDGET=5`; then `just dev`, `just ingest`, `just web`, and open
   `http://127.0.0.1:8000/candidates`. Both source workflows appear, the inbox labels fake stories
   `Test source`, and decisions are unchanged. The fake source has two fixed stories and goes quiet
   once processed. If the selector picks none, `ingestion_state` and the `poll`/`selection` events
   still show the fake poll. The deterministic proof is the committed two-source test.

## Doc updates

- `README.md`: add `SOURCE_CANDIDATE_BUDGET` to Configuration; correct the "registry lists `hn`
  only; fake is not registered" paragraph; add a short "Structured ingestion logs" paragraph and
  the log grep; add the two-source demo and runbook links; note Google Trends is the next connector
  once API/provider access is chosen. Keep "ingestion is not started by worker startup", but make
  clear it polls on Temporal timers once ensured.
- `docs/architecture/README.md`: replace "There is no scheduler: nothing starts a run on its own"
  with a qualified sentence: chart runs are started by a person, while ensured source ingestion
  workflows poll continuously. Update the continuous-ingestion section (both sources, budget, logs,
  runbooks), add `ingestion_logging.py` to the module map, update the `sources/` row, add budget
  state to the state table, and add both runbooks to "Which doc to read for what".
- `docs/architecture/business.md`: add a "Story candidates" subsection (poll -> prefilter -> batch
  LLM selection -> inbox -> approve/reject only; nothing is published; budget per source per UTC
  day; fake source is a dev/test source; Google Trends is a follow-up). Fix the Known limitations
  bullet "Nothing starts a run on a schedule; runs are started by a person" so it is about chart
  runs only.
- `docs/architecture/tech.md`: source-ingestion workflow section (budget fields, reset, processed
  batch rule, logs), activities table (`poll_fake_source`), sources section (fake registered,
  test-only, default off), worker wiring (three activities), settings table
  (`SOURCE_CANDIDATE_BUDGET`), the structured-log event table, test notes, runbook links.
- `docs/history.md`: add a "Phase 9: Real-time story candidates" table with rows 042-049 and short
  "what was done" text. PR links: 042 #48, 043 #49, 044 #50, 045 #51, 046 #52, 047 #53, 048 #54;
  fill merge dates from `git log --merges --date=short`; leave the 049 PR/date for the scribe. Add
  gotchas: budget is per source per UTC day and baked into the workflow input; fake is test-only;
  disabling a source does not stop its workflow or its pending reviews; operational logs never
  carry source text.
- `docs/runbooks/deploy.md`: link both runbooks, add the ingestion env vars to the `.env.prod` key
  list, and keep the ensure-after-deploy step.
- `CLAUDE.md`: no command or rule change expected; do not edit unless the commands table becomes
  inaccurate.

## Acceptance criteria

- [ ] The per-source UTC-day budget is configurable (`SOURCE_CANDIDATE_BUDGET`, default 20,
      1-500), mapped through `Settings` -> `source_ingestion_input`, enforced before selection and
      before every candidate start, reset on a new UTC day with `workflow.now()`, preserved across
      continue-as-new, consumed only by new starts, and visible in `ingestion_state`; an exhausted
      budget makes no selector or candidate-start calls.
- [ ] Structured logs use the fixed `candidate_ingestion.*` event names and fields above, are
      emitted for polls, normalized events, selections, duplicates, candidate starts, budget
      exhaustion, continue-as-new, stop and failures, and contain no titles, URLs, summaries,
      angles, reasons, prompts, payloads, settings, credentials or exception text; the helper
      rejects forbidden fields and non-primitive values, and tests capture the records.
- [ ] `fake` is registered as `Test source`/`poll_fake_source`, its activity is registered on the
      ingestion worker, `SOURCE_ENABLED` still defaults to `hn`, and a `start_local` two-source test
      drives real poll -> select -> inbox -> decide for `hn` and `fake` through
      `CandidateService.list_pending`/`show`/`decide` with no change to `story_candidate.py`,
      `services/candidates.py`, `services/temporal_gateway.py` or any web file.
- [ ] `docs/runbooks/search-attributes.md` and `docs/runbooks/ingestion.md` contain all five
      procedures with the exact commands above, are linked from the deploy runbook and architecture
      README, and a docs test checks the required headings/commands; a fresh operator can enable,
      disable, verify, recover and upgrade a source from them alone.
- [ ] `README.md`, `docs/architecture/README.md`, `business.md`, `tech.md` and `docs/history.md`
      are updated; the old "nothing starts a run on its own" claim is qualified so it applies only
      to chart runs; the fake source's test-only status, the budget, the logs and Google Trends as a
      follow-up are documented.
- [ ] `just lint` (Ruff + strict Pyright) and `just test` pass with no live HN, Anthropic or
      Temporal-client calls; existing chart, candidate and web tests pass except the explicitly
      updated registry/config/worker expectations; no chart, publishing, template, CSS, view-model
      or route behaviour changes.

## Files to touch

- `src/ai_trend_pipeline/ingestion_logging.py` (new): `IngestionLogEvent`, `StructuredLogger`,
  `log_event`, forbidden-field/primitive guards, truncation.
- `src/ai_trend_pipeline/source_ingestion.py`: budget constants/helpers, three input fields, four
  state fields, `_roll_budget`, enforcement in `run`/`_select`/`_start_candidates`, `_next_input`,
  and all structured log calls (replacing the current free-text warns/errors).
- `src/ai_trend_pipeline/sources/registry.py`: register `SourceName.FAKE` with label `Test source`
  and activity `poll_fake_source`; registry order `hn`, `fake`.
- `src/ai_trend_pipeline/sources/fake.py`: `DEFAULT_FAKE_ITEMS`, `FakeSourceActivities` with
  `poll_fake_source`, normalized-event log; update the module docstring.
- `src/ai_trend_pipeline/sources/hn.py`: log `events_normalized` in `poll_hn_source`; connector
  unchanged.
- `src/ai_trend_pipeline/sources/models.py`: update the `SourceName` docstring (both names are now
  registered; fake is test-only).
- `src/ai_trend_pipeline/config.py`: `source_candidate_budget` field and
  `SOURCE_CANDIDATE_BUDGET` parsing.
- `src/ai_trend_pipeline/services/ingestion.py`: map `candidate_budget`.
- `src/ai_trend_pipeline/worker.py`: register `poll_fake_source` in `ingestion_activities`.
- `docs/runbooks/search-attributes.md` (new)
- `docs/runbooks/ingestion.md` (new)
- `docs/runbooks/deploy.md`
- `README.md`
- `docs/architecture/README.md`
- `docs/architecture/business.md`
- `docs/architecture/tech.md`
- `docs/history.md`
- `tests/test_ingestion_logging.py` (new)
- `tests/test_ingestion_budget.py` (new)
- `tests/test_sources_fake.py` (new)
- `tests/test_source_ingestion_two_sources.py` (new)
- `tests/test_ingestion_docs.py` (new)
- `tests/test_source_ingestion.py` (extend budget/log workflow tests)
- `tests/test_source_ingestion_models.py` (extend budget fields and round-trip)
- `tests/test_sources_registry.py` (fake is now registered; replace the "unregistered" assertions)
- `tests/test_config.py` (budget bounds; `SOURCE_ENABLED` now accepts `fake`)
- `tests/test_ingestion_service.py` (budget mapping; two enabled sources)
- `tests/test_worker.py` (expected ingestion activity names include `poll_fake_source`)
- `tests/fixtures/llm_select_story_candidates_fake_source.json` (new: selects `fake-1001`)

## Tests to write

`tests/test_ingestion_budget.py`

- `test_budget_day_for_uses_utc`: aware datetimes on both sides of midnight map to the UTC date.
- `test_budget_remaining_defaults_resets_on_new_day_and_clamps_at_zero`: same day subtracts, a new
  day returns the full budget, over-consumption returns 0.
- `test_budget_input_fields_defaults_and_bounds`: 20/`None`/0 defaults; reject 0/501, negative
  starts, non-date day, unknown fields.
- `test_state_reports_budget_and_remaining`: state fields round-trip and `budget_remaining` is
  computed from the stored day/count.

`tests/test_source_ingestion.py` (extend the existing time-skipping harness; gate activities as it
already does)

- `test_budget_caps_candidate_starts`: selector returns five selections, budget 2 -> exactly two
  candidate workflows, two `candidate_started` events, one `budget_exhausted`.
- `test_exhausted_budget_skips_the_selector_and_does_not_mark_the_batch_processed`: budget 1,
  first poll starts one candidate; the next poll's fresh batch gets no selector call, stays
  unprocessed, and is re-evaluated after the UTC reset.
- `test_budget_skip_logs_exhausted_once_per_day`: repeated polls in the same day emit one
  `budget_exhausted` record; the next UTC day emits another.
- `test_budget_resets_on_a_new_utc_day`: after `env.sleep` past midnight, the next poll starts
  candidates again and `budget_day`/`candidates_started` reset.
- `test_continue_as_new_preserves_budget_day_and_count`: `max_polls_per_run=1`; the next run keeps
  the day and count and does not exceed the budget.
- `test_duplicate_candidate_start_does_not_consume_budget`: an already-started candidate logs
  `candidate_duplicate` and leaves `candidates_started` unchanged.
- `test_failure_logs_use_error_type_only`: poll and selector failures emit `*_failed` with
  `error_type`, never the exception message or fixture title.
- `test_ingestion_state_query_tracks_budget`: extend the existing state-query test.

`tests/test_ingestion_logging.py`

- `test_event_names_are_namespaced_and_unique`: every value starts `candidate_ingestion.` and none
  repeat.
- `test_log_event_sets_message_event_source_and_fields`: records carry the event name as the
  message and `event`/`source`/fields as attributes.
- `test_log_event_rejects_forbidden_source_text_and_secret_fields`: every forbidden key raises
  `ValueError`.
- `test_log_event_truncates_long_values_and_rejects_non_primitives`: 300 chars become 256; dict,
  list and `SecretStr` values raise.
- `test_workflow_emits_poll_selection_duplicate_candidate_and_budget_events`: capture
  `temporalio.workflow` records in a time-skipping run and assert the event names/fields.
- `test_workflow_and_activity_logs_contain_no_titles_or_secrets`: sentinel HN/fake titles and a
  sentinel API key never appear in any captured message or attribute.
- `test_poll_activities_log_normalized_event_counts_only`: real HN (MockTransport) and fake
  activities emit `events_normalized` with `requested_limit`/`event_count` and no title text.

`tests/test_sources_fake.py`

- `test_fake_registry_entry_and_activity_name`: label `Test source`, activity
  `poll_fake_source`; `hn` sorts first in `enabled_sources`.
- `test_fake_activity_returns_the_two_fixed_events`: ids `fake-1001`/`fake-1002`, unique, `http(s)`
  URLs, aware `observed_at`.
- `test_fake_activity_honours_limit_and_accepts_injected_items`: `limit=1` returns one event;
  constructor items override the defaults.
- `test_fake_source_is_off_by_default_and_on_by_config`: `Settings()` is `{hn}`;
  `load_settings({"SOURCE_ENABLED": "hn,fake"})` is `{hn, fake}`.

`tests/test_source_ingestion_two_sources.py` (`start_local`, search attributes pre-registered)

- `test_two_sources_feed_one_inbox`: HN fixture -> 2001, fake `poll_fake_source` -> `fake-1001`;
  `list_pending` returns exactly two rows with sources `{hn, fake}` and labels/titles intact.
- `test_reviews_are_unchanged_for_both_sources`: `show` returns `awaiting_review`; approve the fake
  and reject the HN candidate through `CandidateService.decide`; pending is empty and results are
  terminal.
- `test_budget_is_per_source`: `Settings(source_candidate_budget=1)` -> one candidate per source,
  not one across both.
- `test_one_failing_source_does_not_block_the_other`: failing non-retryable `poll_fake_source` plus
  healthy HN; the HN candidate appears and the fake workflow logs `poll_failed` and stays alive.
- `test_duplicate_candidate_start_does_not_duplicate_inbox_rows`: pre-start the HN candidate through
  `CandidateService.start`, then run ingestion; `list_pending` still has one HN row.
- `test_stop_leaves_pending_reviews`: stop both ingestion runs; both candidates are still queryable
  and decidable (ABANDON).

`tests/test_ingestion_docs.py`

- `test_ingestion_runbook_covers_the_five_procedures`: headings and exact commands (`just ingest`,
  `workflow signal ... --name stop`, `operator search-attribute create`, `workflow query ... --type
  ingestion_state`).
- `test_search_attribute_runbook_has_both_attributes_and_types`.
- `test_architecture_docs_qualify_the_scheduler_claim_and_link_runbooks`: the old unqualified phrase
  is gone; both runbook links resolve; `history.md` has the candidate phase table.

`tests/test_sources_registry.py` (update)

- Replace `test_registry_contains_only_hn` with `test_registry_contains_hn_then_fake` and assert the
  fake label/activity.
- Replace the `get_source(FAKE)`/`enabled_sources({FAKE})` raises with assertions that both resolve
  in registry order; keep the read-only test.

`tests/test_config.py` (update)

- `test_source_candidate_budget_default_and_bounds`: 20 default; 1 and 500 accepted; 0, 501, `-1`,
  `abc`, `2.5` rejected; empty means default; `Settings()` equals `load_settings({})`.
- Update `test_source_enabled_unknown_raises` to use `google-trends`, expect `Registered sources:
  hn, fake`, and assert `SOURCE_ENABLED=fake`/`Settings(source_enabled={FAKE})` are valid.

`tests/test_ingestion_service.py` (extend)

- `test_source_ingestion_input_maps_candidate_budget`.
- `test_ensure_starts_both_sources_when_fake_is_enabled`: one `start` per source in `hn`, `fake`
  order with the configured budget.

`tests/test_worker.py` (update)

- Extend `test_ingestion_workflow_and_activities_registered_on_ingestion_queue` to expect
  `poll_fake_source`, `poll_hn_source`, `select_story_candidates`.

`tests/test_source_ingestion_models.py` (extend)

- `test_budget_fields_defaults_and_bounds`.
- Extend the pydantic-converter round-trip test with a set budget/day/count.

## Implementation notes

- New log calls replace the current free-text `workflow.logger.error/warning` calls in
  `_poll`, `_select` and `_start_candidates`. Keep `is_cancelled_exception` re-raise behaviour.
- `_poll` needs the poll number before `_record_poll`; log `poll=self._total_polls + 1` on failure.
  On success, log after `_record_poll` so `poll`, counts and `last_poll_at` agree.
- `_start_candidates` should take `limit: int`, start at most `limit`, increment a workflow field,
  and log per start/duplicate/outside-batch. It returns the number started (keeps
  `last_selected_count` semantics).
- Compute `remaining` after `_record_poll` and before the selector. Recompute after starts for the
  budget log and for `candidate_started.budget_remaining`.
- `_next_input` must add `budget_day=self._budget_day`, `candidates_started=self._candidates_started`
  while leaving `candidate_budget` copied from `data`.
- `SourceIngestionState` construction includes the budget fields; `budget_remaining` is
  `max(0, candidate_budget - candidates_started)`.
- `log_event` must be typed without `Any`; workflow code imports it inside
  `workflow.unsafe.imports_passed_through()` alongside the other project modules. It imports only
  `sources.models` and the stdlib.
- `FakeSourceActivities` uses a module-level default tuple; do not add I/O or settings. The worker
  constructs it without arguments. `poll_fake_source` must be a real `@activity.defn` so the name
  matches the registry.
- `sources/fake.py` is an activity module; never import it from workflow code. The registry exposes
  only the activity name string.
- Keep `sources/fake.py`'s `FakeSourceConnector` behaviour and the 042 contract tests unchanged.
- Config parsing follows the existing "empty means default" style:
  `int(env.get("SOURCE_CANDIDATE_BUDGET") or 20)`.
- No new dependencies, no OpenTelemetry, no metrics, no log shipping, no Postgres.
- `tests/test_ingestion_docs.py` should read files from the repo root with `Path(__file__)`, like
  `tests/test_claude_md.py`, and assert substrings rather than exact prose.

## Out of scope

- Google Trends, RSS or any third source; Google Trends remains a follow-up until its API/provider
  is chosen.
- Semantic cross-source deduplication, per-source filters/sorting, bulk decisions, or an admin web
  page for sources/budgets. Budget and source enablement are environment configuration only.
- Changing `StoryCandidateWorkflow`, `story_candidate.py` model fields, the candidate service,
  gateway, routes, templates, CSS, view models, navigation or decision flow (045-047 are done).
- Changing 043 selector semantics, prompt, caps or fixtures beyond the new fake-source fixture;
  changing 042 contract models or the HN connector's fetch behaviour.
- Chart generation, OWID, rendering, critique, Bluesky/publishing, `DailyChartWorkflow` or chart
  routes.
- Postgres tables/migrations, a cursor or budget table, or moving ingestion state out of Temporal.
- Auto-starting ingestion at worker boot, a scheduler, or a web trigger.
- Metrics, tracing, OpenTelemetry, dashboards, log shipping or a logging dependency.
- Enabling `fake` by default or in production; putting it in `.env.prod` is forbidden.
- Reconstructing the missing 036-041 history entries.

## Assumptions

- The budget is per source per UTC day (default 20), not per poll; the per-poll caps already exist.
- `budget_day`/`candidates_started` are workflow state; terminating a chain resets the day count,
  which is acceptable because termination is a deliberate operator action.
- When the budget is exhausted the selector is skipped and the batch stays unprocessed; a batch
  whose selector ran is marked processed even if starts were truncated.
- The deterministic two-source proof is a `start_local` test; the manual dev-stack demo uses the
  live LLM selector and may show zero fake selections.
- Fake is registered and its activity is always registered, but default enablement stays `{hn}`;
  `fake` must never appear in `.env.prod`.
- Existing tests that asserted `fake` was unregistered are intentionally updated.
- The runbook CLI uses the `temporal` binary inside the server container, with a host-CLI fallback.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

- **planner**: Wrote the spec. No UI, so the `frontend-design` skill was not applied. Chose a
  per-source UTC-day budget (default 20, `SOURCE_CANDIDATE_BUDGET`), a fixed
  `candidate_ingestion.*` event set with a field guard, two runbooks with dev/prod CLI wrappers,
  and a `start_local` two-source proof; fake stays registered but off by default. Next: human
  approves.
- **builder**: Implemented the budget, guarded structured logs, registered test-only fake source,
  two-source `start_local` proof, both runbooks and all doc updates. `just lint` and `just test`
  (689) pass. Note: `budget_exhausted` suppression across continue-as-new infers "already logged"
  from `total_polls > 0` plus the carried day/count rather than adding another input field. Next:
  verifier.
- **verifier**: `just lint` and `just test` pass (689) with the harness `ANTHROPIC_*` base-url/auth vars unset; with them set, LLM tests fail with `anthropic.APIConnectionError`. One transient failure of `test_continue_as_new_preserves_budget_day_and_count` appeared in a vars-set full run (RPCError under load); it passes alone and in 3 repeat file runs. Next: reviewer.
- **reviewer**: Needs fix, 3 items. 1) `test_continue_as_new_preserves_budget_day_and_count` flakes: after `poller.release()` (tests/test_source_ingestion.py:807) `wait_for_total_polls` (line 282) queries during continue-as-new and gets `RPCError: Timeout expired` (~1 in 3 runs here); wait for `poller.wait_for_call(3)` before querying, as the 048-era CAN test does, and retry transient `RPCError` in the helper.
  2) `budget_exhausted` inference (source_ingestion.py:542-549) suppresses a real log: if the budget is spent in an earlier run without a skipped batch, every skip in the next run is silent for that day; carry a defaulted `budget_exhausted_day: date | None` in `SourceIngestionInput`/`_next_input` instead. No duplicate-emit path found.
  3) `infra/compose.prod.yaml` worker (lines 54-59) forwards only TEMPORAL/ANTHROPIC/BLUESKY vars, so the `SOURCE_ENABLED`/`SOURCE_CANDIDATE_BUDGET` keys added to docs/runbooks/deploy.md:23-28 are inert in prod; pass them into the worker `environment:` (or correct the runbooks).
- **builder (fix loop 1)**: Fixed all three. 1) Wait for `poller.wait_for_call(3)`; helper retries
  transient `RPCError`; CAN tests pass 10/10 repeats. 2) Added defaulted
  `budget_exhausted_day` to `SourceIngestionInput`, carried by `_next_input`, plus a CAN test.
  3) Wired all six source settings into the prod worker environment and covered them by a compose
  test. `just lint` and `just test` (691) pass. Next: verifier.
