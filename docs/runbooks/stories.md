# Stories runbook

Story generation starts only after a person approves a candidate, and it never publishes. This
runbook covers the two decisions, the local walkthrough, search attributes, bounded budgets,
failure recovery, 48-hour retention and replay, and the sanitized operational log.

Run every command from the repo root.

```sh
dev-temporal()  { docker compose -f infra/compose.dev.yaml exec temporal temporal "$@"; }
prod-temporal() { env -i PATH="$PATH" HOME="$HOME" docker compose -f infra/compose.prod.yaml \
  --env-file .env.prod exec temporal temporal "$@"; }
```

## Two human decisions

1. **Candidate approval starts generation.** Approving a candidate in `/candidates` (or through
   `CandidateService`) starts exactly one `StoryGenerationWorkflow` child with the deterministic id
   `story-v1-<candidate-hash>`. It never starts a second one, even if Approve is posted again. The
   candidate result and `review_state` carry `story_workflow_id`.
2. **Final story approval marks the package `ready` and never publishes.** The story detail's final
   Approve/Reject is separate from candidate approval. Approve sets the workflow result to `ready`
   with the `StoryPackage`; Reject sets `rejected`. Nothing is posted, charted or rendered.

The first final decision wins; later signals are ignored and logged as
`story_generation.ignored_signal`. A review window that closes with no decision ends `expired`.
Review expiry is terminal: there is no reopen path.

Publishing is a separate third click. A Ready story shows **Publish to Bluesky**, and only that
button starts a `StoryPublishingWorkflow`. Final approval above still never publishes. Setup,
recovery and the manual visual check are in [publishing.md](publishing.md).

## Local manual walkthrough

In `.env`:

```sh
SOURCE_ENABLED=hn
SOURCE_CANDIDATE_BUDGET=5
SOURCE_POLL_INTERVAL_S=60
CANDIDATE_REVIEW_TIMEOUT_S=3600
```

Then run, in three terminals:

```sh
just dev      # terminal 1: compose stack and the worker
just ingest   # terminal 2: ensure the source ingestion workflow
just web      # terminal 3: http://127.0.0.1:8000
```

Open `http://127.0.0.1:8000/candidates`, approve a candidate, open `/stories`, and follow the
detail page through Writing -> Review. Approve the final story and confirm Ready plus the
"Nothing was published" copy. Check the worker log with `grep story_generation`. Inspect Temporal
directly with:

```sh
dev-temporal workflow list --query 'WorkflowType = "StoryGenerationWorkflow"'
dev-temporal workflow query --workflow-id <story-id> --type story_state
dev-temporal workflow describe --workflow-id <story-id>
```

The live selector and live article may legitimately select zero stories or produce an
`unsupported` story, so the deterministic proof is:

```sh
uv run pytest tests/test_story_generation_e2e.py -q
```

To include the test-only fake source in the inbox, use `SOURCE_ENABLED=hn,fake`; never enable
`fake` in production.

## Search attributes

On a fresh stack, run the nine create commands in
[search-attributes.md](search-attributes.md), then verify them:

```sh
dev-temporal operator search-attribute list
```

The worker registers the same nine attributes on start. Verify stories with:

```sh
dev-temporal workflow list --query 'WorkflowType = "StoryGenerationWorkflow" AND StoryStatus = "review"'
dev-temporal workflow list --query 'StoryCandidateId = "candidate-v1-..."'
```

`StoryStatus` stores the internal workflow state (`researching`, `analyzing`, `writing`,
`checking`, `review`, `ready`, `rejected`, `unsupported`, `failed`, `expired`), never the five UI
badges. `StoryCreatedAt` (inherited from the candidate's detection time) drives the 48-hour
retention cutoff; `StoryUpdatedAt` is for sorting only.

## Bounded budgets

These caps are enforced in existing code. There is no global or per-day story quota workflow, table
or setting: a cross-workflow counter would need shared mutable state, and human approval already
gates every story.

| Budget | Value / setting | Where enforced |
| --- | --- | --- |
| Candidate starts per source per UTC day | `SOURCE_CANDIDATE_BUDGET`, 1-500, default 20 | `SourceIngestionWorkflow._roll_budget`, `budget_remaining`, `_start_candidates`; zero skips the selector, spends no LLM credit, leaves the batch unprocessed and logs once per day |
| Story starts per candidate | exactly 1, ever | `candidate-v1-<hash>` and `story-v1-<hash>` deterministic ids; `REJECT_DUPLICATE`; duplicate approvals are ignored |
| Activity calls per story | 1 `research_story`, 1 `calculate_facts`, at most 2 `draft_story`, at most 2 `critique_story` (round 0 plus one revision) | `StoryGenerationWorkflow.run`; no loop exists after round 1 |
| Attempts per activity | 3 | `ACTIVITY_MAX_ATTEMPTS = 3` in `story_generation.py`; non-retryable draft/checker error types fail immediately |
| Activity time | research 120 s, facts 30 s, draft 120 s, critique 120 s | `RESEARCH_ACTIVITY_TIMEOUT`, `FACTS_ACTIVITY_TIMEOUT`, `DRAFT_ACTIVITY_TIMEOUT`, `CRITIQUE_ACTIVITY_TIMEOUT` |
| Article HTTP | timeout `RESEARCH_FETCH_TIMEOUT_S` over 0 and at most 60, default 10.0; response `RESEARCH_MAX_RESPONSE_BYTES` 1024-4194304, default 524288; redirects `RESEARCH_MAX_REDIRECTS` 0-5, default 3 | `research/fetch.py`; at most 1 primary article + 2 approved extra contexts + 1 OWID fetch per story |
| Context / evidence | at most 3 contexts, 8 000 chars each and 16 000 total; 4 evidence items; 300 dataset rows; 20 countries; 8 failures and 8 notes | `research/models.py`, `research/activities.py` |
| Facts | workflow requests 16 (`STORY_DRAFT_MAX_FACTS`); contract maximum 32; at most 4 series entities; 96 KiB output target | `analysis/models.py`, `analysis/calculations.py`, `story_generation.py` |
| Draft / check text | prompt ceiling 100 000 chars; 4 096 draft tokens and 2 048 critique tokens; headline 160, summary 600, body 6 000, 5 key points, 4 source links, 5 caveats, post text 300; exactly one revision | `story_drafting/models.py`, `story_drafting/writing.py`, `story_drafting/critique.py` |
| Review wait | `min(review_timeout_seconds, story_created_at + 48 h - workflow.now())`; at or past the cutoff expires before or instead of waiting | `story_generation.py`; `STORY_UI_RETENTION = 48 h` in `story_status.py` |

Changing `SOURCE_CANDIDATE_BUDGET` or `SOURCE_POLL_INTERVAL_S` requires stopping the ingestion
workflow and running `just ingest` again, because both are baked into the workflow input. The
per-story caps above are code constants, not runtime settings.

## Failure recovery

| Symptom | Check | Action |
| --- | --- | --- |
| Candidate approved but no story appears | `review_state.story_workflow_id`, `workflow describe`, a worker on `story-generation-task-queue`, the nine search attributes, `story_generation.started` | Register missing attributes, start the worker, then approve again only if the candidate is still awaiting review |
| Story stuck in research/analyze/write/check | worker output, `story_generation.activity_failed`, `workflow describe`, Anthropic/HTTP health | Fix the upstream dependency; activity retries finish on their own or the story ends `failed` |
| Story ends `failed` after retries | `story_generation.finished`, `status_detail` | Terminal. Fix the upstream cause and start a new candidate; a `failed` story is not retried |
| Story ends `unsupported` | `story_generation.research_completed` and the detail | Expected evidence gap (missing article, unsafe URL, too little text). Terminal; no writing runs |
| `/stories` returns 503 | `just web`, Temporal connectivity, `StoryStatusUnavailable` | Restart the web process or restore Temporal; no history is lost |
| `/candidates` returns 503 | `just web`, Temporal connectivity, `CandidateUnavailable` | Restart the web process or restore Temporal; no history is lost |
| Duplicate story suspected | `dev-temporal workflow list --query 'StoryCandidateId = "candidate-v1-..."'` | There must be exactly two workflows (candidate and story). The deterministic id makes a third impossible |
| Review expired | `story_generation.finished` with `status=expired` | Terminal; there is no reopen. Start a new candidate if the story still matters |

## Retention and replay

48 hours is presentation only. At and past `StoryCreatedAt + 48 h`, rows leave `/stories` and
`/candidates`, all filters, pagination and pending counts, but the workflow histories remain and
`describe`/`show` still answer. No history is deleted and there is no cleanup timer.

Replay rules for a running or archived history:

- Add new workflow input fields as optional fields with defaults.
- Keep activity names and order (`research_story`, `calculate_facts`, `draft_story`,
  `critique_story`).
- Keep the `story-generation-handoff-v1` patch and the deterministic `story-v1-` ids.
- Use a `story-v2-` id prefix only for an incompatible rewrite; never reuse `story-v1-` for
  different semantics.

Diagnose with:

```sh
dev-temporal workflow show --workflow-id <story-id>
dev-temporal workflow list --query 'StoryStatus = "failed"'
```

Prefer rolling the worker back to the matching code. Do not terminate or reset a live history
unless the runbook you are following says so; terminating loses state and cannot be undone.

## Structured logs

The workflow, never the activities, writes `story_generation.*` through the pure
`story_logging.py` guard. The event set is fixed:

`story_generation.started`, `story_generation.status`,
`story_generation.research_completed`, `story_generation.facts_completed`,
`story_generation.draft_completed`, `story_generation.check_completed`,
`story_generation.revision`, `story_generation.activity_failed`,
`story_generation.review_waiting`, `story_generation.decision`,
`story_generation.ignored_signal`, `story_generation.finished`.

Forbidden fields include titles, URLs, summaries, angles, prompts, model responses, article text,
excerpts, evidence, drafts, critiques, packages, notes, `decided_by`, `status_detail`, reasons,
emails, tokens, authorization and cookies. Non-primitive values are rejected and strings truncate
at 256 characters; logs never contain exception text, only `error_type`.

Dev logs are the worker terminal:

```sh
grep story_generation
```

Prod logs:

```sh
env -i PATH="$PATH" HOME="$HOME" docker compose -f infra/compose.prod.yaml --env-file .env.prod \
  logs -f worker | grep story_generation
```
