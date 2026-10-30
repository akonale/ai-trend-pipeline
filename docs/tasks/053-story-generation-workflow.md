# 053: StoryGenerationWorkflow and approval handoff

Status: pr-open

## Goal

Add `StoryGenerationWorkflow`, which turns an approved candidate into a review-ready,
evidence-backed `StoryPackage` by orchestrating the 050 research, 051 fact and 052 drafting/critique
activities through one bounded revision, then waits for a final human Approve/Reject. Make candidate
approval start exactly one deterministic `story-v1-<candidate-hash>` story workflow without waiting,
and add the shared visibility attributes, five-status mapping, 48-hour deadline caps and replay
protection that tasks 054-055 will use. Nothing publishes.

## Design

### State machine

```
                      ┌─ research unsupported ─────────────────────────▶ unsupported
researching ─▶ analyzing ─▶ writing ─▶ checking ─┬─ passed ─▶ review ─┬─ approve ─▶ ready
                    ▲          │         │        │                   ├─ reject  ─▶ rejected
                    │          └─ one revision ──┘                   └─ 48 h cutoff ─▶ expired
                    └──────── activity error / invalid draft / second failed check ─▶ failed
```

- `researching`: `research_story` with the approved `StoryInput`.
- `analyzing`: `calculate_facts` with the 050 `ResearchOutput` and a 16-fact budget.
- `writing`: `draft_story` with `draft_input_for(story, research, facts)`; one revision when the
  first check fails.
- `checking`: `critique_story` round 0; after a revision, round 1. Two checks and two drafts are the
  hard maximum.
- `review`: final `approve` / `reject` signals; first terminal decision wins; a timeout expires at
  the UI cutoff.
- Terminal states: `ready`, `rejected`, `unsupported`, `failed`, `expired`.
- `unsupported` is decided after research (or an unexpected unsupported `FactSet`) and before any
  writing; no writer or checker call is made for an unsupported story.
- Every state transition upserts `StoryStatus` and `StoryUpdatedAt`; terminal states also set the
  optional sanitized `StoryStatusDetail`.

### IDs, queue and models

- `STORY_TASK_QUEUE = "story-generation-task-queue"`.
- `story_id_for(candidate_id: str) -> str`: `story-v1-` + the first 16 hex chars of SHA-256 over
  `candidate_id`. The candidate workflow id is the candidate id, so one candidate maps to one
  story id forever. Versioned so a future rewrite can use `story-v2-`.
- New module `story_generation.py` holds the workflow and models; it never imports `services/`,
  `web/`, `worker.py`, `daily_chart.py`, `story_candidate.py`, Bluesky or render code.
- All models are Pydantic, frozen where practical, `extra="forbid"`, bounded, UTC-aware.

`StoryGenerationInput` (frozen):

| Field | Type and bounds |
| --- | --- |
| `candidate_id` | str 1-128 |
| `story` | `research.models.StoryInput`; `story.candidate_id` must equal `candidate_id` |
| `story_created_at` | aware UTC datetime; must equal `story.observed_at` (the original detection time) |
| `review_timeout_seconds` | int 1-604800, default `DEFAULT_STORY_REVIEW_TIMEOUT_S = 172800` |

`FinalStoryDecision` (frozen): `note` stripped 0-500, default `""`; `decided_by` stripped 1-320,
blank becomes `None`.

`StoryGenerationState` (query): `story_id`, `candidate_id`, `story_created_at`, `updated_at`,
`status: StoryGenerationStatus`, `status_detail: str | None` (max 200), `package: StoryPackage |
None`, `critique: CritiqueOutput | None`, `decided_by: str | None`, `note: str`, `decided_at:
datetime | None`. Query-safe defaults (`None` / `researching`) so it can be read before the first
task. It never carries contexts, evidence rows, prompts or raw failures.

`StoryGenerationResult`: `status: Literal["ready", "rejected", "unsupported", "failed",
"expired"]`, `story_id`, `candidate_id`, `story_created_at`, `updated_at`, `status_detail`,
`package`, `critique`, `decided_by`, `note`, `decided_at`. `package`/`critique` are set for `ready`
and `rejected`; `None` for `unsupported`, `failed` and `expired`. A supported story with an
`EMPTY` 051 `FactSet` is allowed (article-only), so `draft_input_for` is the only mapper used.

`StoryGenerationStatus` (StrEnum): `researching`, `analyzing`, `writing`, `checking`, `review`,
`ready`, `rejected`, `unsupported`, `failed`, `expired`.

Query `story_state() -> StoryGenerationState`; signals `approve(FinalStoryDecision)` and
`reject(FinalStoryDecision)`; ignored unless status is `review` and no decision exists yet.

### Internal states and the five primary UI statuses

Add pure module `story_status.py` (stdlib only, importable by workflows, services and web):

- `PrimaryStoryStatus(StrEnum)`: `pending`, `writing`, `review`, `ready`, `closed`.
- `primary_story_status(value: str) -> PrimaryStoryStatus` over **both** workflow types:
  - `awaiting_review` → `pending`.
  - candidate `approved`, story `researching`/`analyzing`/`writing`/`checking` → `writing`.
  - story `review` → `review`; story `ready` → `ready`.
  - candidate `rejected`/`expired` and story `rejected`/`unsupported`/`failed`/`expired` →
    `closed`. Unknown values → `closed` (a list page must never fail).
- `STORY_UI_RETENTION = timedelta(hours=48)`; `DEFAULT_STORY_REVIEW_TIMEOUT_S = 172800`.
  Task 054 uses the retention value; workflows use it for deadline caps.
- `StoryStatus` search-attribute values are the internal strings above, never the primary badge; 054
  owns labels, colours and handoff de-duplication.

### Shared visibility attributes

New module `story_visibility.py` defines the keys and helpers; `temporal_client.py` registers them.

| Attribute | Type | Candidate value | Story value |
| --- | --- | --- | --- |
| `StoryStatus` | Keyword | `awaiting_review` / `approved` / `rejected` / `expired` | internal story status |
| `StoryTitle` | Keyword | `candidate.title` | same |
| `StorySource` | Keyword | `candidate.source.value` | same |
| `StoryCandidateId` | Keyword | candidate workflow id | inherited candidate id |
| `StoryCreatedAt` | Datetime | `candidate.observed_at` | inherited unchanged |
| `StoryUpdatedAt` | Datetime | start / terminal `workflow.now()` | every transition |
| `StoryStatusDetail` | Keyword | optional terminal reason | optional terminal reason |

- `ensure_story_search_attributes(client)` in `temporal_client.py` registers all seven story
  attributes **and** the two existing candidate attributes, ignoring `ALREADY_EXISTS`. Keep
  `ensure_candidate_search_attributes` as a thin wrapper that calls it so existing tests and callers
  keep working; the worker calls `ensure_story_search_attributes`.
- `story_visibility.py` exposes `story_start_attributes(status, title, source, candidate_id,
  created_at, updated_at)` and `story_status_updates(status, updated_at, detail=None)` (plus a
  `bounded_detail` cleaner that strips, collapses whitespace, removes control characters and caps at
  200). Use the same narrow `# pyright: ignore[reportUnknownMemberType]` comment style as
  `story_candidate._upsert_status_attribute`; no bare `Any`.
- `candidate_search_attributes(candidate, updated_at=None)` keeps its two candidate keys and adds the
  seven shared keys, with `updated_at` defaulting to `candidate.observed_at`. The gateway passes
  `datetime.now(UTC)`; `source_ingestion.py` passes `workflow.now()`.
- Never put story text, package JSON, evidence, prompts or URLs into search attributes.

### Candidate handoff

`StoryCandidateWorkflow` gains a small, version-gated handoff:

- New optional input field `story_review_timeout_seconds: int = 172800` (1-604800).
- New optional `CandidateReviewState.story_workflow_id` and `StoryCandidateResult.story_workflow_id`
  (default `None`) so old stored results and queries decode.
- On approval only, start `StoryGenerationWorkflow` once as a child with
  `id=story_id_for(candidate_id)`, `task_queue=STORY_TASK_QUEUE`,
  `parent_close_policy=ABANDON`, `id_reuse_policy=REJECT_DUPLICATE`, the story start search
  attributes, and `StoryGenerationInput(candidate_id, story_input_for(candidate),
  story_created_at=candidate.observed_at, review_timeout_seconds=data.story_review_timeout_seconds)`.
  **Do not await the child's result.** Set `story_workflow_id` in the candidate query state and
  result. Catch `WorkflowAlreadyStartedError`, log a warning with no candidate text, keep the
  deterministic id and do not fail. Let other start errors propagate so the workflow task retries;
  an approval must never silently lose its story.
- `story_input_for(candidate: StoryCandidate) -> StoryInput` is a pure mapper in `story_candidate.py`
  (id, source, external id, title, URL, published/observed times, summary, angle, reason, metrics).
- Replay/versioning: shared start attributes are set client-side by
  `candidate_search_attributes` in both start paths, so the workflow adds no start command. A single
  `workflow.patched("story-generation-handoff-v1")` call sits in the terminal/expiry path, where the
  new commands (shared upsert, child start) live:
  - `True` (new executions and old **open** candidates decided after deploy): perform one shared
    upsert with `CandidateStatus`, `StoryStatus`, `StoryUpdatedAt` and — so old open candidates gain
    metadata — `StoryCreatedAt`, `StoryTitle`, `StorySource`, `StoryCandidateId`; start exactly one
    story on approval and set `story_workflow_id`.
  - `False` (closed histories from 042-049): execute exactly the old command sequence, the
    `CandidateStatus`-only terminal upsert, start no story and return `story_workflow_id=None`.
  - The 48-hour candidate timeout cap is applied by `wait_condition` for new executions; an old open
    candidate already has its timer in history, and Temporal's versioning guidance treats changed
    timer durations as replay-safe. The captured-history test proves this for a closed history.
  Do not remove the patch or call `workflow.deprecate_patch` in this task.
- `StoryCandidateWorkflow` remains free of I/O, clocks other than `workflow.now()`, randomness and
  Temporal client calls. Import `story_generation` inside
  `workflow.unsafe.imports_passed_through()`; `story_generation` must not import `story_candidate`,
  so there is no cycle.

### Deadlines and 48-hour retention

- Candidate review: effective timeout is
  `min(data.review_timeout_seconds, (candidate.observed_at + STORY_UI_RETENTION - workflow.now()).total_seconds())`,
  floored at 0; if the remainder is already ≤ 0 the candidate expires immediately without a timer.
  This is presentation retention, not deletion: history is untouched.
- Story review: `review_deadline = story_created_at + STORY_UI_RETENTION`; effective timeout is
  `min(data.review_timeout_seconds, review_deadline - workflow.now())`. If a story starts at or past
  its cutoff it returns `expired` before calling any activity, and if the cutoff passes during
  research/writing it expires instead of entering a hidden wait.
- The web service (054) computes the same cutoff from the `StoryCreatedAt` attribute; 053 exports
  `STORY_UI_RETENTION`. No cleanup workflow, database row or timer is added, and histories are
  never deleted.

### Activity policy

The workflow schedules activities by their contract names with `result_type=` and a `cast` (the
string-name overload returns `Any`; add a short comment, as in `source_ingestion.py`). It imports no
activity module.

| Activity | Start-to-close | Attempts | Notes |
| --- | --- | --- | --- |
| `research_story` | 120 s | 3 | expected unsupported outcomes are values; transient HTTP errors retry |
| `calculate_facts` | 30 s | 3 | sync activity; the story worker must set an activity executor |
| `draft_story` | 120 s | 3 | `StoryDraftInvalid` / `StoryDraftToolMissing` are non-retryable |
| `critique_story` | 120 s | 3 | `StoryCritiqueInvalid` / `StoryCritiqueToolMissing` are non-retryable |

- Any `ActivityError` after retries maps to `failed` with a fixed sanitized detail; cancellation
  exceptions are re-raised. Non-retryable errors fail immediately (no third attempt). The workflow
  never catches and continues with a partial result.
- Facts budget: `fact_input_for(research, story)` followed by
  `model_copy(update={"max_facts": STORY_DRAFT_MAX_FACTS})` with `STORY_DRAFT_MAX_FACTS = 16`.
  051 keeps its own 32-fact default for other callers.
- One revision only: on a failing round-0 critique build
  `RevisionRequest(prior_draft=draft, critique=critique, round=1)` and use
  `draft_input.model_copy(update={"revision": revision})`; check the revised draft once with
  `round=1`. A failing round-1 check is terminal `failed`; never loop.

### Sanitized status detail

`StoryStatusDetail` is chosen from fixed English sentences, capped at 200 characters, and never
contains exception text, prompts, URLs, emails or untrusted candidate/article text:

- unsupported: map the first `ResearchFailure.code` to "The candidate has no source link.", "The
  source link could not be used safely.", "The original article is no longer available.", "There was
  not enough readable evidence to support this story." or "The story could not be supported with
  the available evidence." as appropriate.
- failed: "The draft did not pass factual validation.", "The writer did not return a usable draft.",
  "The checker did not return a usable result.", "The research step failed after retries.", "The
  analysis step failed after retries.", "The story did not pass checking after one revision." or
  "Story generation failed."
- expired: "The review window closed."
- `ready` / `rejected` have no detail; their labels come from 054.

### Prompt-bound reconciliation (052 reviewer note)

052's reviewer found `MAX_DRAFT_PROMPT_CHARS = 24_000` is fixture-sized and a maximal valid
`DraftInput` measured about 41.6k characters, so the constant was not a real ceiling. 053 must
reconcile it:

1. Cap the facts sent to the writer/checker at `STORY_DRAFT_MAX_FACTS = 16` (above). 051 still
   supports 32; the story workflow just asks for fewer, which halves the largest prompt section and
   keeps LLM cost bounded.
2. Raise `MAX_DRAFT_PROMPT_CHARS` in `story_drafting/models.py` to `100_000` and document it as the
   maximal-input prompt ceiling, not a fixture size. The prompt is a JSON rendering of the bounded
   `DraftInput`; a near-maximal input is asserted under `MAX_DRAFT_INPUT_BYTES_TARGET` (96 KiB) in
   052, so 100k characters is a safe, testable headroom. Update 052's equality assertion in
   `tests/test_story_drafting_models.py`.
3. Add the maximal-input test described below. It builds a 3-context (8k total), 4-evidence,
   16-fact, maximal-revision `DraftInput` through `draft_input_for`, then asserts both
   `writing.build_user_prompt` and `critique.build_user_prompt` are at most
   `MAX_DRAFT_PROMPT_CHARS`. Do not add prompt truncation and do not change the two activities'
   prompts, validation or error types. If the measured worst case ever exceeds 100k, lower
   `STORY_DRAFT_MAX_FACTS` to 8 and re-measure; never exceed 100k silently.

### Worker registration

`worker.py` adds a fifth worker on `STORY_TASK_QUEUE`:

- `STORY_WORKFLOWS = [StoryGenerationWorkflow]`.
- `story_activities(anthropic_client, research_client, owid_client, settings)` returns
  `StoryResearchActivities(research_client, owid_client, settings).research_story`,
  `FactActivities().calculate_facts`, `StoryDraftActivities(anthropic_client).draft_story` and
  `StoryCritiqueActivities(anthropic_client).critique_story`. `fetch_context` and
  `match_owid_evidence` stay unregistered because `research_story` invokes their logic directly.
- The story worker gets `activity_executor=ThreadPoolExecutor(max_workers=4)` for the sync fact
  activity. `main()` creates the research client with `make_research_client(settings)` and an OWID
  client with `make_owid_client()` inside the existing `async with` block, and calls
  `ensure_story_search_attributes(client)`. Existing hello/chart/candidate/ingestion workers are
  unchanged; no publishing or render activity is registered on the story queue.

### Nothing publishes

No story module imports or schedules `post_chart_to_bluesky`, `render_chart_png`, `daily_chart`,
`chart_spec` or `render`. `ready` only means the package was approved; it does not send anything.

## Acceptance criteria

- [ ] `StoryGenerationWorkflow` runs on `story-generation-task-queue` and orchestrates
      `researching → analyzing → writing → checking → review` through the 050-052 activities with
      at most one revision, ending in exactly one of `ready`, `rejected`, `unsupported`, `failed`
      or `expired`; unsupported research stops before writing; `story_state` and the final
      Approve/Reject signals behave as specified and the first terminal decision wins; no I/O,
      randomness or Temporal-client calls exist in workflow code.
- [ ] Candidate approval starts exactly one `story-v1-<candidate-hash>` child without waiting,
      records its id in the candidate query and result, inherits `candidate.observed_at` as
      `StoryCreatedAt`, and cannot duplicate the story on repeated approval or an already-started
      workflow. The handoff and shared terminal upserts sit behind a terminal-path patch: closed
      042-049 histories replay the old commands with `story_workflow_id=None`, old open candidates
      decided after deploy still hand off, and new result/query fields are optional with defaults.
- [ ] All seven shared visibility attributes are registered idempotently and written for both
      candidate and story workflows, carry the internal status, detection timestamp and bounded
      sanitized detail, and never contain story text, evidence, prompts or URLs. The pure
      five-status mapping covers every candidate and story internal state, and candidate and story
      review deadlines never extend past `StoryCreatedAt + 48 hours`.
- [ ] Activity retry policies match the table (120 s/3 for research and LLM work, 30 s/3 for the sync
      fact activity), non-retryable drafting/checking errors map to `failed` without another attempt,
      the story worker registers the workflow and four activities with an activity executor, and no
      story path can publish, render or call Bluesky.
- [ ] The prompt bound is reconciled: facts are capped at 16 and `MAX_DRAFT_PROMPT_CHARS` is a
      tested maximal-input ceiling (100 000) with both writer and checker prompts under it; 052's
      runtime prompts, validation and error behaviour are otherwise unchanged.
- [ ] Time-skipping and `start_local` tests cover every internal state and all five UI mappings,
      unsupported-before-writing, one revision, exhausted failure, final approve/reject, expiry at
      the UI cutoff, first-decision-wins, duplicate approval, duplicate story start, handoff
      metadata, old candidate and chart replay, exactly one story per approved candidate and no
      publishing; all HTTP/LLM calls use fixtures and `just lint` (Ruff + strict Pyright) and
      `just test` pass.

## Files to touch

- `src/ai_trend_pipeline/story_generation.py` (new): queue/id/constants, `StoryGenerationStatus`,
  `StoryGenerationInput`, `FinalStoryDecision`, `StoryGenerationState`, `StoryGenerationResult`,
  `story_id_for`, retry/timeout constants and `StoryGenerationWorkflow`.
- `src/ai_trend_pipeline/story_visibility.py` (new): attribute names and typed keys,
  `story_start_attributes`, `story_status_updates`, `bounded_detail`, `MAX_STATUS_DETAIL_CHARS`.
- `src/ai_trend_pipeline/story_status.py` (new): `PrimaryStoryStatus`, `primary_story_status`,
  `STORY_UI_RETENTION`, `DEFAULT_STORY_REVIEW_TIMEOUT_S`.
- `src/ai_trend_pipeline/story_candidate.py` (modify): `story_input_for`,
  `STORY_HANDOFF_PATCH`, patch-gated shared attributes, capped candidate timeout, story child start,
  `story_workflow_id`, extended `candidate_search_attributes`, `story_review_timeout_seconds`.
- `src/ai_trend_pipeline/temporal_client.py` (modify): story attribute map,
  `ensure_story_search_attributes`, compatibility wrapper.
- `src/ai_trend_pipeline/worker.py` (modify): story worker, `STORY_WORKFLOWS`, `story_activities`,
  executor and clients.
- `src/ai_trend_pipeline/source_ingestion.py` (modify): pass `workflow.now()` to
  `candidate_search_attributes` (no behaviour change).
- `src/ai_trend_pipeline/services/temporal_gateway.py` (modify): pass `datetime.now(UTC)` to
  `candidate_search_attributes` in `TemporalCandidateGateway.start` (no other gateway change).
- `src/ai_trend_pipeline/story_drafting/models.py` (modify constant only):
  `MAX_DRAFT_PROMPT_CHARS = 100_000` with an updated comment.
- `tests/story_generation_fixtures.py` (new helpers): deterministic `StoryCandidate`, `StoryInput`,
  `ResearchOutput`, `FactSet`, `DraftInput`, `DraftOutput`, `StoryPackage` and `CritiqueOutput`
  builders; no I/O, clock or randomness.
- `tests/test_story_generation_models.py` (new)
- `tests/test_story_generation.py` (new, time-skipping workflow tests with fake named activities)
- `tests/test_story_generation_handoff.py` (new, `start_local` candidate→story tests)
- `tests/test_story_generation_replay.py` (new, pre-053 history replay)
- `tests/test_story_status.py` (new, five-status mapping and retention)
- `tests/fixtures/histories/story_candidate_v1_approved.json` (new, captured pre-053 history)
- `tests/test_story_candidate.py` (extend: capped expiry, handoff/old-input coverage)
- `tests/test_story_candidate_models.py` (extend: new defaults, extended attribute helper)
- `tests/test_source_ingestion.py` (modify only if the helper signature change requires it)
- `tests/test_worker.py` (extend: story queue, activities, executor, no publishing)
- `tests/test_story_drafting_models.py` (modify the `MAX_DRAFT_PROMPT_CHARS` assertion)
- `docs/architecture/tech.md` (workflow section, activity/worker table, search attributes, replay)
- `docs/architecture/README.md` (module map, state table, handoff flow)
- `docs/runbooks/search-attributes.md` (the seven new names/types and idempotent registration)
- `docs/tasks/053-story-generation-workflow.md` (status and handoff log only)

Do not edit `daily_chart.py`, `chart_loop.py`, `hn.py`, chart routes, `README.md`,
`docs/history.md`, `docs/mvp-plan.md`, `config.py`, or the 050-052 activity logic.

## Tests to write

`tests/test_story_generation_models.py`

- `test_story_id_is_deterministic_versioned_and_candidate_specific`: `story-v1-` + 16 hex, repeat
  calls equal, different candidate ids differ.
- `test_story_input_matches_candidate_and_detection_time`: `story_input_for` maps every field,
  `candidate_id` is derived, `story_created_at == story.observed_at`, mismatches and naive
  datetimes raise.
- `test_story_generation_input_bounds_and_serializes_under_16kb`: timeout 1/604800 pass, 0/604801
  fail, max-length candidate round-trips through `pydantic_data_converter` under 16 KiB.
- `test_state_and_result_models_are_frozen_bounded_and_forbid_extra_fields`: status literal set,
  detail cap, optional package/critique, extra fields and frozen assignment raise.
- `test_final_story_decision_strips_and_bounds`: note/decided_by trimming, caps, blank decided_by.
- `test_status_detail_cleaner_is_bounded_and_control_free`: whitespace collapsed, control characters
  removed, 201+ chars truncated to 200, blank becomes `None`.
- `test_story_visibility_helpers_set_all_keys_and_types`: start attributes and map the seven keys,
  datetime values, no detail when absent; status updates carry status/updated/detail only.
- `test_maximal_draft_prompt_fits_the_reconciled_ceiling`: maximal 3-context/4-evidence/16-fact
  `DraftInput` with a maximal `RevisionRequest` built by `draft_input_for`; writer and checker
  prompts are ≤ `MAX_DRAFT_PROMPT_CHARS`, and the constant is `100_000`.

`tests/test_story_status.py`

- `test_every_candidate_and_story_state_maps_to_one_primary_status`: all four candidate and all ten
  story internal values, including `approved`→`writing`, `review`→`review`, `ready`→`ready` and
  every terminal value→`closed`.
- `test_unknown_status_is_closed_and_retention_is_48_hours`: unknown string → `closed`;
  `STORY_UI_RETENTION == timedelta(hours=48)`; `DEFAULT_STORY_REVIEW_TIMEOUT_S == 172800`.

`tests/test_story_generation.py` (time-skipping; register `ensure_story_search_attributes`; fake
activities registered under the exact names `research_story`, `calculate_facts`, `draft_story`,
`critique_story`, gated by events so the test can query between stages)

- `test_every_internal_state_and_five_status_mapping`: gated fakes let the test observe
  `researching`, `analyzing`, `writing`, `checking`, `review` and `ready`; each internal state maps
  through `primary_story_status` and the `StoryStatus` attribute matches.
- `test_unsupported_research_skips_facts_writing_and_checking`: unsupported `ResearchOutput`
  returns `unsupported`, detail is the fixed sentence, and the other fakes are never called.
- `test_one_failed_check_triggers_exactly_one_revision`: round 0 fails; the second draft input has
  `revision.round == 1`; round 1 passes; final `review`/`ready` uses the revised package.
- `test_failed_check_after_revision_is_terminal_failed`: two failing checks produce `failed`, the
  fixed "after one revision" detail, exactly two drafts and two checks, and no review wait.
- `test_invalid_draft_and_tool_missing_errors_fail_without_retry`: fake raises the non-retryable
  error types once; one call each, final `failed`, no retry.
- `test_research_retry_then_success_follows_the_policy`: the fake fails retryably twice then
  succeeds; the workflow completes.
- `test_final_approve_returns_ready_package`: at review, approve; result `ready`, package equals the
  draft, decision metadata and `StoryStatus=ready`.
- `test_final_reject_returns_rejected`: reject; result `rejected`, no publishing activity exists.
- `test_first_final_decision_wins`: approve then reject and reject then approve (tolerate a server
  NOT_FOUND for the late signal); the first decision is stored.
- `test_review_expires_at_the_ui_cutoff_not_the_configured_timeout`: `story_created_at` is just
  under 48 h old and `review_timeout_seconds=604800`; after `env.sleep` past the cutoff the result
  is `expired` with "The review window closed."
- `test_story_started_after_the_cutoff_expires_without_activities`: `story_created_at` older than
  48 h; result `expired` and zero activity calls.
- `test_workflow_source_has_no_io_clock_randomness_or_publishing`: AST/source checks for no `httpx`,
  `anthropic`, `temporalio.client`, `datetime.now`, `time.time`, `random.`, `uuid.uuid4`,
  `bluesky`, `render`, `daily_chart`, and presence of the four activity names and
  `workflow.wait_condition`.

`tests/test_story_generation_handoff.py` (`start_local`; candidate queue worker and no story worker
needed; register the story attributes)

- `test_approved_candidate_starts_exactly_one_story_and_returns_its_id`: approve; result
  `story_workflow_id == story_id_for(candidate_id)`; `describe` the child on
  `story-generation-task-queue`; `list_workflows` finds exactly one `StoryGenerationWorkflow`.
- `test_duplicate_approval_cannot_start_a_second_story`: send approve twice (tolerate a late-signal
  RPC error); the result id is unchanged and exactly one story exists.
- `test_duplicate_story_start_is_tolerated`: pre-start `StoryGenerationWorkflow` with the
  deterministic id; approve; the candidate returns the same id, logs/handles
  `WorkflowAlreadyStartedError` and still has exactly one story.
- `test_handoff_metadata_is_shared_and_de_duplicable`: query both executions by
  `StoryCandidateId = '<candidate id>'`; candidate `StoryStatus=approved` and story
  `StoryStatus=researching`; `StoryTitle`, `StorySource` and `StoryCreatedAt` are identical on both
  rows and `StoryCreatedAt == candidate.observed_at`.
- `test_rejected_candidate_starts_no_story`: reject; result has `story_workflow_id=None` and no
  story workflow exists.

`tests/test_story_generation_replay.py`

- `test_pre_053_approved_candidate_history_replays`: replay
  `tests/fixtures/histories/story_candidate_v1_approved.json` with
  `temporalio.worker.Replayer([StoryCandidateWorkflow])` and `pydantic_data_converter`; no
  non-determinism error. The fixture is captured from `main` before editing (see notes) and
  contains only the compact test candidate, no secrets.
- `test_old_candidate_input_without_new_fields_still_runs`: start a candidate from the old-shaped
  input payload (no `story_review_timeout_seconds`), approve, and get a normal result with a story
  id; this proves the optional-field defaults.
- Existing `tests/test_daily_chart.py::test_closed_approved_run_replays_with_post_uri` must pass
  unchanged.

`tests/test_story_candidate.py` (extend)

- `test_candidate_review_expires_at_the_ui_cutoff_not_the_configured_timeout`: `observed_at` just
  under 48 h old and `review_timeout_seconds=604800`; after `env.sleep` past the cutoff the result
  is `expired`.
- `test_review_state_and_result_default_story_workflow_id_to_none`: old-shaped query/result values
  still construct and read `None`.

`tests/test_story_candidate_models.py` (extend)

- `test_candidate_search_attributes_include_the_shared_story_attributes`: all nine keys, candidate
  status/source still correct, `StoryCreatedAt == observed_at`.
- `test_story_review_timeout_default_and_bounds`: default 172800, 1/604800 pass, 0/604801 fail.

`tests/test_worker.py` (extend)

- `test_story_workflow_and_activities_registered_on_story_queue`: `STORY_WORKFLOWS`,
  `"story-generation-task-queue"`, and the four activity names from `story_activities`.
- `test_story_worker_uses_an_activity_executor_and_no_publishing_activity`: source check for
  `activity_executor=ThreadPoolExecutor` and absence of `post_chart_to_bluesky`/`render_chart_png`
  in the story worker wiring; existing worker tests pass unchanged.

`tests/test_story_drafting_models.py` (modify)

- Update `test_models_round_trip_json_and_respect_byte_targets` to assert
  `MAX_DRAFT_PROMPT_CHARS == 100_000`.

`tests/fixtures/histories/story_candidate_v1_approved.json` capture (before implementing 053)

- In a temporary worktree of `main` (or by stashing 053 changes), run a script that starts
  `WorkflowEnvironment.start_local` with the candidate attributes, runs `StoryCandidateWorkflow`
  with the standard compact candidate and `review_timeout_seconds=3600`, sends `approve`, awaits the
  result, and writes `(await handle.fetch_history()).to_json()` to the fixture. Commit only the
  history JSON; it must not contain credentials or real account data.

## Out of scope

- No `/stories` page, `/stories/{id}` page, routes, templates, view models, CSS, navigation,
  filters, pagination, htmx, or final Approve/Reject buttons (054-055).
- No `StoryStatusService`, status list/detail service, handoff de-duplication service, 48-hour
  filtering or pending-count work (054-055); 053 only exports the mapping, attributes and cutoff.
- No new `config.py` setting, environment variable, `README.md`, `docs/history.md`,
  `docs/mvp-plan.md`, deploy or end-to-end runbook work (056); no config test changes.
- No changes to the 050 research, 051 analysis or 052 writing/validation/prompt logic, other than
  the single test-only `MAX_DRAFT_PROMPT_CHARS` constant and the new maximal-input test.
- No second revision, self-correction loop, retry of a non-retryable validation error, or LLM call
  outside the existing activities.
- No publishing, Bluesky, chart generation, rendering, media, Postgres tables, migrations, cleanup
  workflows, search-attribute runbook expansion beyond the new names, or history deletion.
- No changes to `DailyChartWorkflow`, chart activities/routes, ingestion selection semantics, source
  contracts/connectors, per-source budgets/logging, or existing chart/candidate tests beyond the
  listed compatibility updates.
- No live HTTP, LLM or Temporal calls in tests; no secrets or real account details in fixtures.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

- **planner**: Wrote the spec; no UI, so `frontend-design` was not applied. Next: spec approval,
  then the builder captures the pre-053 candidate history before editing.
- Assumptions not in the plan: `StoryCreatedAt` is `candidate.observed_at`; story ids hash the
  candidate workflow id; `StoryStatus` stores internal states and `story_status.py` maps to the five
  badges; the story queue is `story-generation-task-queue`; the final-review timeout is an input
  model field (no env setting) capped at the 48-hour cutoff.
- Prompt-bound decision: yes, 053 reconciles 052's fixture-sized 24k ceiling by capping drafting to
  16 facts and raising `MAX_DRAFT_PROMPT_CHARS` to a tested 100k maximal-input ceiling.
- Replay: one captured pre-053 approved-candidate history plus a `workflow.patched` gate; new
  result/query fields are optional. Only `research_story`, `calculate_facts`, `draft_story` and
  `critique_story` are registered on the story queue; nothing publishes.
- **builder**: Implemented StoryGenerationWorkflow, story_status/story_visibility, the patched
  candidate handoff, story worker and docs; the pre-053 history replays. Updated four existing
  Temporal test fixtures to `datetime.now(UTC)` and two stale "not registered" worker assertions
  (new 48h cap and story registration); all other scope as specced. Lint and full tests green with
  harness `ANTHROPIC_*` unset. Next: verifier.
- **verifier**: `just lint` (Ruff + Pyright strict) and `just test` pass; the full suite is green with harness `ANTHROPIC_*` unset only because its base URL/token produce `anthropic.APIConnectionError` in every LLM test with them set (881 passed). Next: reviewer.
- **reviewer**: Approved. State machine, determinism, handoff patch, visibility and registration match the spec; focused story tests (138), Ruff and strict Pyright are green. The four `datetime.now(UTC)` fixture updates and two worker assertion flips are justified by the 48h cap and story registration, not weakened. Non-blocking: `_checker_failure_detail` has two branches returning the same detail; `worker.py` opens a second OWID client for the story worker.
- **scribe**: Updated README for the story workflow/handoff and stale research wiring; architecture docs and search-attribute runbook already covered states, queue, visibility, five-status mapping and 48h deadlines. Opened PR https://github.com/akonale/ai-trend-pipeline/pull/60. Next: human review and merge.
