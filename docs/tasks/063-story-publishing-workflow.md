# 063: StoryPublishingWorkflow and gateway

Status: pr-open

> Plan row **062** in `docs/mvp-plan.md` ("`StoryPublishingWorkflow` and gateway"), renumbered to
> **063** (repo numbers are plan row + 1, see `docs/tasks/058-media-foundation.md`). It builds on
> repo 058-062, mainly `publishing/` (`publication_key`, `format_publication`,
> `capabilities_for`, `BlueskyStoryActivities.publish_story_to_bluesky`) and the carry-forward note
> in `docs/architecture/tech.md` "Story publishing contracts". Plan row 063 is repo **064** (the
> Publish button, `published` badge and docs). Cross-references below use repo numbers.

## Goal

Add `StoryPublishingWorkflow`, one per story, destination and approved content hash. It validates
the approved package in workflow code, runs the 062 Bluesky activity with bounded retries, and
returns a typed result. A service starts it only on an explicit request for a Ready story, and reads
its state. The worker registers the workflow and the activity. There is no UI and no
approval-to-publish path.

## Design

### Workflow module (new `src/ai_trend_pipeline/story_publishing.py`)

It sits beside `story_media.py`. Pure imports go through `workflow.unsafe.imports_passed_through()`:
`publishing.models`, `publishing.registry`, `publishing.formatter`, `story_drafting.models`,
`story_visibility`, `story_generation.DecidedByText` and `STORY_ID_PREFIX`. It must **not** import
`publishing/bluesky_story.py`, httpx, `services/` or `web/`. The activity is called by name.

Constants:

- `STORY_PUBLISHING_TASK_QUEUE = "story-publishing-task-queue"`
- `PUBLISH_STORY_ACTIVITY = "publish_story_to_bluesky"`
- `PUBLISH_ACTIVITY_START_TO_CLOSE = timedelta(seconds=60)`
- `PUBLISH_ACTIVITY_SCHEDULE_TO_CLOSE = timedelta(minutes=20)`. This allows one maximum 15-minute
  rate-limit delay and still ends.
- `PUBLISH_MAX_ATTEMPTS = 4`
- `PUBLISH_RETRY_POLICY = RetryPolicy(initial_interval=2s, backoff_coefficient=2.0,
  maximum_interval=60s, maximum_attempts=PUBLISH_MAX_ATTEMPTS, non_retryable_error_types=[every
  PublicationErrorCode value not in RETRYABLE_CODES])`. This is the bound the 062 carry-forward
  asks for: an unexpected 4xx (`UnexpectedDestinationResponse`) is tried at most 4 times.
- `PUBLISH_WORKFLOW_TIMEOUT = timedelta(minutes=30)` (execution timeout, set by the gateway).

Models (all `frozen=True, extra="forbid"`, aware datetimes normalized with `ensure_aware_utc`):

- `PublicationStatus(StrEnum)`: `PUBLISHING = "publishing"`, `PUBLISHED = "published"`,
  `FAILED = "publish_failed"`. These values are written to the existing `StoryStatus` search
  attribute.
- `StoryPublishingInput`: `destination: DestinationName`, `story_workflow_id` (starts with
  `STORY_ID_PREFIX`, max 64), `story_id` (equals `package.story_id`, max
  `MAX_MEDIA_STORY_ID_CHARS`), `candidate_id` (max 128), `story_created_at`, `package:
  StoryPackage`, `content_hash` (SHA-256 pattern), `requested_by: DecidedByText | None = None`.
  Validator: `package.story_id == story_id`.
- `StoryPublishingState` (the query snapshot, with query-safe defaults so a query before `run`
  starts still works): `publication_id: str = ""`, `destination: DestinationName | None = None`,
  `story_workflow_id`, `story_id`, `candidate_id` (all `""`), `story_created_at: datetime | None`,
  `content_hash: str | None`, `status: PublicationStatus = PUBLISHING`, `status_detail: str | None`
  (max 200), `error_code: PublicationErrorCode | None`, `retryable: bool = False`,
  `requested_by: str | None`, `updated_at: datetime | None`, `publication: PublicationResult |
  None`. It never contains the post text, package, tokens or response bodies.
- `StoryPublishingResult`: the same fields, but `status: Literal["published", "publish_failed"]`
  and the IDs, `story_created_at`, `content_hash` and `updated_at` are required. Validator:
  `publication` is set if and only if `status == "published"`.

Fixed detail text (sanitized, at most 200 characters, never containing story text):

- `PUBLISHING_CHECKING_DETAIL = "Checking the approved story."`
- `PUBLISHING_POSTING_DETAIL = "Posting to Bluesky."` (from a `DESTINATION_LABELS` map keyed by
  `DestinationName`)
- `PUBLISHED_DETAIL = "Published to Bluesky."`
- `FAILURE_DETAILS: Mapping[PublicationErrorCode, str]` has one fixed sentence for **every** code.
  Examples: `PostTextTooLong` gives "The post text is longer than Bluesky allows.",
  `PublicationMediaMissing` gives "The approved image file is missing.", `DestinationAuthFailed`
  gives "Bluesky rejected the account credentials.", `DestinationRateLimited` gives "Bluesky is
  limiting posts. Try again later.", `DestinationUnavailable` gives "Bluesky could not be reached.
  Try again.", `UnexpectedDestinationResponse` gives "Bluesky sent an unexpected response. Try
  again.", and `DestinationRejected` gives "Bluesky rejected the post." Write the others in the
  same style.

`@workflow.defn class StoryPublishingWorkflow`:

1. `run(data: StoryPublishingInput) -> StoryPublishingResult`. Set the state from the input and
   transition to `PUBLISHING` with `PUBLISHING_CHECKING_DETAIL`. Every transition sets
   `updated_at = workflow.now()` and calls
   `workflow.upsert_search_attributes(story_status_updates(status, updated_at, detail))`, the same
   pattern as `StoryGenerationWorkflow._transition`.
2. If `workflow.info().workflow_id != publication_key(data.story_id, data.destination,
   data.content_hash)`, raise a non-retryable `ApplicationError("Publication key mismatch",
   type="PublicationKeyMismatch")`. This is a programming error, not a user path.
3. Capability validation in workflow code (it is pure):
   `format_publication(data.package, data.content_hash, capabilities_for(data.destination))`.
   `PublicationRejected(code)` finishes as `FAILED` with that code and **does not** run the
   activity.
4. Transition with `PUBLISHING_POSTING_DETAIL`, then `workflow.execute_activity(
   PUBLISH_STORY_ACTIVITY, PublicationInput(destination, story_id, candidate_id,
   story_created_at, requested_at=workflow.now(), package, content_hash),
   result_type=PublicationResult, start_to_close_timeout=..., schedule_to_close_timeout=...,
   retry_policy=PUBLISH_RETRY_POLICY)`. The activity does the existing-record lookup, the upload
   (static fallback for GIF) and the create.
5. On success, check that the result's `idempotency_key`, `content_hash`, `story_id` and
   `destination` match the input. If they do not, finish `FAILED` with
   `UnexpectedDestinationResponse`. Otherwise finish `PUBLISHED` with `PUBLISHED_DETAIL` and
   `publication=result`.
6. On `ActivityError`: if `is_cancelled_exception(exc)`, re-raise. Otherwise map the cause. An
   `ApplicationError` whose `type` is a known `PublicationErrorCode` keeps that code. Any other
   type becomes `UnexpectedDestinationResponse`. A `TimeoutError` cause becomes
   `DestinationUnavailable`. Finish `FAILED` with `FAILURE_DETAILS[code]` and
   `retryable = code in RETRYABLE_CODES`. Expected failures return a typed result. They never fail
   the workflow, so the story stays publishable.
7. `@workflow.query publication_state() -> StoryPublishingState`.

The workflow never signals, queries or changes `StoryGenerationWorkflow`. It never changes the
`StoryPackage`, and it reads no clock other than `workflow.now()`.

### Retention metadata inheritance

At start, the gateway sets `story_start_attributes(status="publishing", title, source,
candidate_id, created_at=<the original StoryCreatedAt from the generation result>,
updated_at=now)`. It copies `StoryTitle` and `StorySource` from the generation workflow's
`describe().typed_search_attributes`, or uses `""` if they are missing. Publishing therefore never
resets the 48-hour window. No new search attribute and no new table are added. The `/stories`
visibility query stays limited to the candidate and generation workflow types, so publishing rows
do not appear in the list until repo 064. `primary_story_status` is unchanged.

### Service (new `src/ai_trend_pipeline/services/publishing.py`)

- `PublishStoryRequest`: `detail_id` (the generation workflow ID, max `MAX_DETAIL_ID_CHARS`),
  `content_hash` (SHA-256 pattern, the hash the user saw), `destination: DestinationName =
  BLUESKY`, `requested_by: DecidedByText | None = None`.
- Errors: `StoryNotPublishable(StoryDetailError)` and `StoryPublishStale(StoryDetailError)`. Reuse
  `StoryDetailNotFound`, `StoryDetailExpired`, `StoryDetailMalformed` and
  `StoryDetailUnavailable` from `services/stories.py`.
- `StoryPublishingGateway(Protocol)`: `story_state(wf_id) -> StoryGenerationState`,
  `story_result(wf_id) -> StoryGenerationResult`, `publication(publication_id) ->
  StoryPublishingState | None`, `start(data: StoryPublishingInput) -> None`.
- `StoryPublishingService(gateway)`:
  - `publish(request, *, now) -> StoryPublishingState`. `now` must be aware.
    `story_detail_kind(detail_id)` must be `GENERATION`, otherwise `StoryNotPublishable`. Query
    `story_state` first. Never `await result()` on a running story. Check retention on
    `story_created_at` (`StoryDetailExpired`). The status must be `READY`, otherwise
    `StoryNotPublishable`. Then read `story_result`: a missing package or hash is
    `StoryDetailMalformed`, and a request hash that differs from `result.content_hash` is
    `StoryPublishStale`. Compute the key with `publication_key(package.story_id, destination,
    content_hash)`. If `gateway.publication(key)` is `publishing` or `published`, return it **without
    starting** (this covers a double click and an already-published story). Otherwise call
    `gateway.start(...)` and return `gateway.publication(key)`, or a `PUBLISHING` state if it is not
    visible yet.
  - `publication_for(detail_id, *, now, destination=BLUESKY) -> StoryPublishingState | None`.
    It applies the same kind and retention checks. It returns `None` when the story is not Ready
    or has never been published. This is the read operation for repo 064.
- Nothing else calls `publish`. `StoryDetailService.decide` and `StoryCandidateWorkflow` are
  unchanged.

### Temporal gateway (`services/temporal_gateway.py`)

Add `TemporalStoryPublishingGateway(client)`:

- `story_state` and `story_result` work as in `TemporalStoryDetailGateway` (use `_detail_call`).
- `start(data)` calls `client.start_workflow(StoryPublishingWorkflow.run, data,
  id=publication_key(...), task_queue=STORY_PUBLISHING_TASK_QUEUE,
  id_conflict_policy=USE_EXISTING, id_reuse_policy=ALLOW_DUPLICATE,
  execution_timeout=PUBLISH_WORKFLOW_TIMEOUT, search_attributes=<inherited, see above>)`.
  `USE_EXISTING` joins a run that is still going (concurrent starts). `ALLOW_DUPLICATE` allows a
  retry after a `publish_failed` run. A restart after a run that already published is still safe,
  because the fixed rkey makes the activity return `already_published=True`. Treat
  `WorkflowAlreadyStartedError` as success.
- `publication(id)` calls `describe()`. `NOT_FOUND` gives `None`. While the run is `RUNNING`, query
  `publication_state`. When it is `COMPLETED`, map `handle.result()` (a `StoryPublishingResult`)
  to a `StoryPublishingState`. When it is `FAILED`, `TERMINATED`, `CANCELED` or `TIMED_OUT`, return
  a `publish_failed` state with the detail "Publishing stopped before it finished. Try again.",
  `error_code=None` and `retryable=True`. Other RPC errors give `StoryDetailUnavailable`.
  Validation errors give `StoryDetailMalformed`.

### Worker (`worker.py`)

Add `PUBLISHING_WORKFLOWS = [StoryPublishingWorkflow]`,
`PUBLISHING_WORKER_MAX_CONCURRENT_ACTIVITIES = 2`, and
`publishing_activities(bluesky_client, credentials, settings) -> list[Callable[..., Any]]`, which
returns `[BlueskyStoryActivities(bluesky_client, credentials,
Path(settings.media_dir)).publish_story_to_bluesky]`. Run a sixth worker on
`STORY_PUBLISHING_TASK_QUEUE` that reuses the existing `bluesky_client`. There are no new env vars.

## Acceptance criteria

- [ ] Time-skipping tests, using the real `publish_story_to_bluesky` against the 062 fake PDS
      (`httpx.MockTransport`), cover: text-only, static and GIF (the static PNG is uploaded,
      `media_kind=static`); retry after a network failure; already-published recovery
      (`already_published=True`, no upload or create); permanent failure (auth 401, after which
      a corrected PDS and a second `publish` succeed); missing media (failed with
      `PublicationMediaMissing` and zero HTTP requests); and a `PostTextTooLong` package (failed in
      workflow code, with no activity run).
- [ ] At most one post exists. A double click and two concurrent `publish` calls (with
      `asyncio.gather`) give exactly one workflow run and one stored record. A success returns
      `uri`, `public_url` and `published_at` that match the PDS record.
- [ ] Retries are bounded. A PDS that always answers `getRecord` with an unexpected 4xx runs the
      activity exactly `PUBLISH_MAX_ATTEMPTS` times, then returns `publish_failed` with
      `UnexpectedDestinationResponse`. Non-retryable codes run once.
- [ ] The query shows `publishing` with sanitized detail during the run, and `published` or
      `publish_failed` at the end. The `StoryStatus`, `StoryUpdatedAt` and `StoryStatusDetail`
      upserts match. The start attributes inherit the original `StoryCandidateId` and
      `StoryCreatedAt`. No state, result, detail or log contains the post text, tokens or a raw
      response body.
- [ ] The service starts publishing only for a Ready generation story inside the 48-hour window
      with a matching hash. Pending, Review and Closed stories, candidate IDs, a stale hash and
      an expired story are refused, and none of them starts a workflow. No path from candidate or
      final approval starts publishing. The approved `StoryPackage` is never changed.
- [ ] The worker registers `StoryPublishingWorkflow` and `publish_story_to_bluesky` on
      `story-publishing-task-queue`. A captured published history replays with `Replayer`. The
      `/stories` list query is unchanged. `just lint` and `just test` pass.

## Files to touch

- `src/ai_trend_pipeline/story_publishing.py` (new)
- `src/ai_trend_pipeline/services/publishing.py` (new)
- `src/ai_trend_pipeline/services/temporal_gateway.py` (add `TemporalStoryPublishingGateway`)
- `src/ai_trend_pipeline/worker.py` (publishing worker and activities)
- `tests/publishing_fixtures.py` (move `FakePds` and its scripting helpers here from
  `tests/test_bluesky_story.py`, so both test files share them)
- `tests/test_bluesky_story.py` (import change only)
- `tests/test_story_publishing_workflow.py` (new)
- `tests/test_publishing_service.py` (new)
- `tests/test_temporal_gateway.py`, `tests/test_worker.py` (add cases)
- `docs/architecture/tech.md` (update the "Story publishing contracts" paragraph and the
  "Worker" paragraph: the workflow, the queue, retry bounds and retention inheritance)

## Tests to write

`tests/test_story_publishing_workflow.py` (time-skipping env, real activity, fake PDS, artifacts in
`tmp_path`)
- `test_text_only_story_publishes_and_returns_uri_and_public_url`
- `test_static_story_uploads_png_and_maps_result`
- `test_gif_story_publishes_static_fallback`
- `test_network_failure_is_retried_and_posts_once`
- `test_existing_record_is_recovered_as_already_published`
- `test_auth_failure_finishes_publish_failed_and_runs_activity_once`
- `test_unexpected_4xx_is_bounded_to_max_attempts`
- `test_missing_media_fails_without_http_requests`
- `test_post_text_too_long_fails_in_workflow_without_activity`
- `test_query_reports_publishing_then_terminal_state_with_sanitized_detail`
- `test_search_attributes_inherit_candidate_id_and_story_created_at`
- `test_workflow_id_must_equal_publication_key`
- `test_result_and_state_contain_no_post_text_or_secrets`
- `test_published_history_replays`
- `test_workflow_module_imports_no_httpx_services_or_web` (AST scan)

`tests/test_publishing_service.py` (fake gateway, plus one time-skipping integration class)
- `test_publish_ready_story_starts_one_workflow_with_deterministic_id`
- `test_double_click_returns_existing_publishing_state_without_second_start`
- `test_concurrent_publish_creates_one_run_and_one_record` (Temporal env with the real gateway)
- `test_already_published_returns_result_without_start`
- `test_failed_publication_can_be_retried`
- `test_refuses_non_ready_candidate_id_stale_hash_and_expired_story`
- `test_publication_for_returns_none_before_publish_and_state_after`
- `test_no_approval_path_references_publishing` (AST scan: only `services/publishing.py`,
  `services/temporal_gateway.py` and `worker.py` import `story_publishing`)

`tests/test_temporal_gateway.py`
- `test_publishing_start_uses_use_existing_allow_duplicate_and_inherited_attributes`
- `test_publication_maps_running_completed_and_abnormal_close_and_not_found`

`tests/test_worker.py`
- `test_publishing_worker_registers_workflow_and_story_activity`

## Out of scope

- The Publish button, the `published` primary badge, `/stories` filter changes, web routes and
  dependencies, templates, CLI commands, and the runbook and README updates (all repo 064).
  There is no UI here, so the design step was skipped.
- Any change to `publishing/` models, formatter, registry or `bluesky_story.py`, to `bluesky.py`
  or the chart path, to `StoryGenerationWorkflow`, `StoryCandidateWorkflow`, `story_status.py`,
  `story_visibility.py` or `story_logging.py`.
- New search attributes, tables, env vars, compose or Dockerfile changes.
- Destinations other than Bluesky, re-encoding or regenerating media, editing post text, and any
  live API call.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: Wrote the spec for plan row 062 (repo 063). There is no UI, so the design step was skipped (the frontend-design skill was loaded but not needed).
Key choices: expected failures return a typed `publish_failed` result. The gateway uses USE_EXISTING + ALLOW_DUPLICATE, and the fixed rkey keeps a re-run from posting twice. Retries are bounded at 4 attempts and 20 minutes, which handles the 062 carry-forward. `StoryCreatedAt` and `StoryCandidateId` are inherited.
Note: `package.story_id` is the candidate ID, so the key is `publish-v1-<candidate-id>-bluesky-<hash>`. `FakePds` moves to `tests/publishing_fixtures.py`.
I have no shell, so there is no branch and no commit. The file is uncommitted in the main checkout. Next: the orchestrator creates `task/063-story-publishing-workflow`, commits only this file, and approves it (the row is `no`).

**builder**: Built `story_publishing.py`, `services/publishing.py`, `TemporalStoryPublishingGateway`, the sixth worker, tests and the replay fixture `story_publishing_v1_published.json`. `FakePds` moved to `tests/publishing_fixtures.py`. `just lint` clean and the full suite passes (1363).
Notes: the time-skipping server has no ListWorkflows, so the concurrency test counts run ids from a recording client instead of listing. Gateway `start` tolerates a missing generation workflow (blank title/source). Protocol params are named `workflow_id` to match the other gateways.
`tech.md` updated (Story publishing contracts, Worker). The frontend-design skill was loaded but not needed (no UI).

**verifier**: Ran `just lint` (ruff check, ruff format --check, pyright strict: 0 errors) and `just test` (1363 passed), with ANTHROPIC_* unset. All checks pass.
Next: reviewer checks the diff against the checklist.

**reviewer**: Approved. Diff matches spec and checklist; workflow is deterministic (no I/O, pure imports only), retries bounded at 4 with non-retryable codes, USE_EXISTING/ALLOW_DUPLICATE and service pre-check give at-most-one post, failures return typed results, no approval path, `.claude/settings.json` not in the diff.
Notes: (1) `pyright: ignore` on `upsert_search_attributes` in `story_publishing.py` has no inline reason; it copies the `story_generation.py` precedent, so optional to add a comment. (2) Builder's three deviations are acceptable.

**scribe**: Added the inline reason to the `pyright: ignore` on `upsert_search_attributes` in `story_publishing.py`. Lint, format and pyright are clean. No README change (the runbook is repo 064), and `tech.md` already covers this task.
PR: https://github.com/akonale/ai-trend-pipeline/pull/72 (status `pr-open`). Not merged; the human merges.
