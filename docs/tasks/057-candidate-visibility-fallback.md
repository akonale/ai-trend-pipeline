# 057: Pre-053 candidate visibility fallback

Status: reviewed

## Goal

Make `/candidates` and `/stories` visible to the 28 `StoryCandidateWorkflow` runs created before 053,
which carry `CandidateStatus`/`CandidateSource` but no `StoryCreatedAt`, by using the candidate
workflow's `StartTime` (the original detection time) for the rolling 48-hour cutoff. Generation rows
keep using `StoryCreatedAt`.

## Problem and context

054 filters both pages strictly on `StoryCreatedAt > now - 48h`. Pre-053 candidate executions never
received that attribute, so their pending rows are invisible and unreviewable even though the workflow
is still awaiting a decision. The ingestion poll starts the candidate workflow at detection time, so
`StartTime` is exactly the same instant for these rows. 054's assumption that pre-053 rows are outside
the window is superseded by this task.

No backfill, history rewrite, new search attribute, or workflow change. This is a read-path fix only;
the Temporal histories remain untouched.

## Query design

Use **one** visibility query per scan page, with one parenthesized OR branch per workflow type, and
keep one `list_workflows` iterator/cursor. Temporal visibility supports mixed `AND`/`OR` with
parentheses, and the single cursor preserves the existing opaque page token and the overflow-token fix
without adding a second cursor to the token. Separate queries were considered and rejected for that
reason.

`services/stories.py::story_visibility_query(filter, cutoff)` becomes (same UTC second stamp):

```
((WorkflowType = 'StoryCandidateWorkflow' AND StartTime > '<cutoff>')
 OR (WorkflowType = 'StoryGenerationWorkflow' AND StoryCreatedAt > '<cutoff>'))
```

and the existing filter predicate is appended with `AND` exactly as today. `StartTime` is a built-in
system attribute; it is not registered.

`services/temporal_gateway.py::pending_candidates_query(cutoff)` becomes:

```
WorkflowType = 'StoryCandidateWorkflow'
AND CandidateStatus = 'awaiting_review'
AND StartTime > '<cutoff>'
```

`StoryCreatedAt` must not appear in either candidate predicate.

### Effective timestamp per row

- **Candidate execution row:** use `WorkflowExecution.start_time` as `StoryVisibilityRow.created_at`.
  Do not use `StoryUpdatedAt` or the `candidate_observed_at` memo for the retention clock. Keep the
  memo read for missing `StoryTitle` only. Existing `CandidateSource`/`CandidateStatus` fallbacks and
  the workflow-id fallback for `StoryCandidateId` stay.
- **Generation execution row:** use only the `StoryCreatedAt` attribute. Do not fall back to
  `StartTime`; a generation row without `StoryCreatedAt` remains outside the list.
- **Collapse:** unchanged. Group by `StoryCandidateId`, prefer the generation row, so a candidate with
  both rows still yields exactly one row. A pre-053 candidate has `StoryCandidateId` fallback to its
  workflow id, matching the child story's inherited candidate id.

### Boundary and counts

Both paths keep the strict `>` rule and the existing second-precision cutoff stamp: visible at
47:59:59, absent at exactly 48:00:00. In `TemporalCandidateGateway.list_pending`, additionally skip
rows where `not retained(workflow_execution.start_time, cutoff)` so the code enforces the boundary
even when the query stamp is truncated; this is the only new in-code filter on that path. The
`StoryStatusGateway` already re-applies `retained(row.created_at, cutoff)`; with candidate rows using
`StartTime`, that filter now covers pre-053 rows too. Pending counts and the nav badge continue to be
derived from the already-fetched page, so they include restored rows automatically.

No per-row workflow query is added anywhere; only visibility fields (`typed_search_attributes`,
`start_time`) are read.

## Acceptance criteria

- [ ] `/candidates` lists a pre-053 `StoryCandidateWorkflow` with no `StoryCreatedAt` when its
      `StartTime` is 1 hour old, and excludes the same workflow at exactly 48 hours; the pending
      query uses `StartTime > '<cutoff>'`, contains no `StoryCreatedAt`, and keeps the existing
      `CandidateStatus = 'awaiting_review'` filter, pagination tokens, and page-derived badge/count.
- [ ] `/stories` lists the same pre-053 candidate as one `Pending` row under All and Pending: the
      candidate visibility branch uses `StartTime`, `StoryVisibilityRow.created_at` for candidate rows
      is `StartTime`, and the strict window is visible at 47:59:59 and absent at 48:00:00.
- [ ] Generation rows are unchanged: they are filtered and retained by `StoryCreatedAt` only, a
      generation row with no `StoryCreatedAt` is never resurrected by its `StartTime`, and generation
      still inherits original detection time rather than resetting the clock.
- [ ] One parenthesized visibility query per scan page covers both workflow types and all six filters;
      de-duplication by `StoryCandidateId` (generation preferred) yields no duplicate row when a
      candidate has both a candidate row and a story row; the overflow-token pagination, filter
      matching, and counts remain correct.
- [ ] The regression tests reproduce the production case, `just lint` and `just test` pass, and no
      workflow, search-attribute registration, backfill, history rewrite, memo, template, CSS or UI
      code changes.

## Files to touch

- `src/ai_trend_pipeline/services/stories.py` (`story_visibility_query`: per-workflow-type cutoff
  branches; no model or filter-predicate changes)
- `src/ai_trend_pipeline/services/temporal_gateway.py` (`pending_candidates_query` uses `StartTime`;
  `list_pending` boundary guard; `_visibility_row` uses `StartTime` for candidate rows and
  `StoryCreatedAt` for generation rows; remove the visibility `created_at` memo fallback)
- `tests/test_story_status_service.py` (new regression tests and `FakeExecution.start_time`; update
  the exact query assertions and pagination query assertion)
- `tests/test_candidate_gateway.py` (exact pending-query assertion; pre-053 visibility and boundary
  regression tests; update the recorded-query assertion)
- `docs/architecture/tech.md` (candidate and story list cutoff wording)
- `docs/architecture/README.md` (state-table note for the retention timestamp)

## Tests to write

`tests/test_story_status_service.py` (no Temporal)

- `test_visibility_query_uses_start_time_for_candidates_and_story_created_at_for_generations` - update
  the existing exact-query test so every filter's base is the two-branch OR with `StartTime >` on the
  candidate branch and `StoryCreatedAt >` on the generation branch, and the filter predicates are
  unchanged.
- `test_pre_053_candidate_without_story_created_at_is_visible_as_pending` - a fake execution with only
  `CandidateStatus`/`CandidateSource`, `StoryCreatedAt=None`, `start_time=1h ago`, and a candidate memo
  appears once under All and Pending with status Pending, its workflow id as `detail_id`, and the memo
  title.
- `test_pre_053_candidate_boundary_is_strict_on_start_time` - `start_time` at 47:59:59 is listed;
  at exactly 48:00:00 it is absent.
- `test_generation_rows_still_use_story_created_at_only` - a generation row with recent `start_time`
  but `StoryCreatedAt=None` is dropped; one with `StoryCreatedAt=1h ago` is listed; one at exactly
  48:00:00 is dropped.
- `test_pre_053_candidate_and_its_generation_row_collapse_to_one_row` - same candidate id on both raw
  rows yields one row using the generation id/status, never two.
- `test_mixed_rows_keep_pagination_dedup_and_overflow_tokens` - a pre-053 candidate and its
  generation row straddling pages still paginate without duplicates and the existing overflow token
  still reaches the older row; update `FakeExecution` to accept `start_time` in all existing cases.
- Keep `test_created_at_never_comes_from_updated_at_or_start_time` for generation rows; do not weaken
  it.

`tests/test_candidate_gateway.py` (fake visibility client; no Temporal for the new cases)

- `test_pending_query_uses_start_time_cutoff` - exact `pending_candidates_query(cutoff)` string with
  `StartTime > '<stamp>'` and no `StoryCreatedAt`; update `test_list_pending_passes_the_cutoff` to
  assert the same query is sent.
- `test_list_pending_includes_pre_053_candidate_and_excludes_the_boundary` - a fake client returns one
  execution with only candidate attributes, a candidate memo, and `start_time=1h ago`, plus one at
  exactly 48h; only the first is returned, the page count is 1, and the query was recorded.
- `test_list_pending_uses_start_time_not_story_created_at` - an execution whose only timestamp is a
  recent `start_time` (no `StoryCreatedAt`) is listed; a 48h `start_time` with a recent
  `StoryCreatedAt` is not (the candidate path is StartTime-only).

## Out of scope

- Any change to `story_candidate.py`, `story_generation.py`, `story_visibility.py`,
  `story_status.py`, workflows, activities, worker, search-attribute registration, `config.py`,
  `temporal_client.py`, ingestion or the candidate/story models.
- Backfilling `StoryCreatedAt`, replaying or rewriting histories, deleting histories, adding a new
  search attribute or memo, adding a cleanup job, table or migration.
- Changing the detail pages, `/stories/{id}` semantics, view models, templates, CSS, copy, filters,
  the 10-second polling, or any UI/design work.
- Switching generation rows to `StartTime`, or using `StartTime` for generation retention; using
  `StoryUpdatedAt` for retention; adding per-row workflow queries.
- Adding a second visibility iterator/cursor or changing the page-token format.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: wrote the defect spec; no UI, so the `frontend-design` step was skipped. Chose the single
parenthesized OR visibility query to preserve the one-cursor overflow token, and candidate rows use
`StartTime` unconditionally (`StoryCreatedAt` no longer gates them). Next: orchestrator sets approved,
then the builder writes the regression tests first.

**builder**: candidate rows now read `StartTime` and generation rows keep `StoryCreatedAt` in both the
visibility query and the row mapping; `pending_candidates_query` uses `StartTime` plus an in-code
boundary guard. Added the six regression tests, updated exact-query/pagination assertions and both
docs. The parenthesized OR works on `start_local`, so no split query was needed. Next: verifier.

**verifier**: `just lint` clean; `just test` 1008 passed with the harness `ANTHROPIC_*` base-url vars unset (unset run was needed: with them set, 73 LLM tests failed with `anthropic.APIConnectionError`). Next: reviewer.

**reviewer**: Approved. Spec met: candidate rows use `StartTime` (query + mapping, plus the in-code boundary guard), generation rows keep `StoryCreatedAt`; exact prod case and strict boundary covered; de-dup/pagination/overflow tests intact. Scope minimal, lint + targeted tests green. Next: scribe.
