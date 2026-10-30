# 056: Story-generation end-to-end hardening and docs

Status: pr-open

## Goal

Prove the committed story feature end to end through the real Temporal workflows, gateways, services
and web routes using recorded HTTP/LLM fixtures, then document its bounded budgets, sanitized
`story_generation.*` logs, five-status semantics, 48-hour presentation retention, search-attribute
setup, failure recovery and the two human decisions so a fresh operator can run and troubleshoot it
from the repo alone. No new workflow, database table, setting or publishing behaviour is added.

## Budget design

"Bounded budgets" for story generation means layered, deterministic caps enforced in existing code,
not a new global or per-day counter. A cross-workflow daily story quota would need shared mutable
state (a new budget workflow or a Postgres table), which the milestone rules forbid; human approval
already gates every story, and each candidate maps to at most one story forever.

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

No new environment variable, `Settings` field, global budget workflow or table is added. The daily
bound is the existing per-source candidate budget; the per-story bound is the activity/retry/revision
cap above; the time bound is the strict 48-hour cutoff. Document this exact decision in `README.md`,
`tech.md`, `business.md` and the new runbook.

## Log design

Add a small pure logging layer, mirroring `ingestion_logging.py`, and emit all story events from
`StoryGenerationWorkflow` itself. Activities stay log-free, so replay-safe `workflow.logger` is the
only writer and no untrusted text can enter a record.

- New `src/ai_trend_pipeline/operational_logging.py`: `MAX_LOG_VALUE_LENGTH = 256`, the shared
  `FORBIDDEN_LOG_FIELDS`, the `StructuredLogger` protocol and `check_log_field(name, value, *,
  forbidden)`. `ingestion_logging.py` imports and re-exports its existing public names unchanged, so
  `tests/test_ingestion_logging.py` stays green.
- New `src/ai_trend_pipeline/story_logging.py`: `StoryLogEvent` and `FORBIDDEN_STORY_LOG_FIELDS`
  (`FORBIDDEN_LOG_FIELDS` plus `excerpt`, `evidence`, `draft`, `critique`, `package`, `note`,
  `decided_by`, `email`, `token`, `authorization`, `cookie`, `context`, `fact`, `story_text`,
  `status_detail`, `reason`). `log_story_event(logger, event, **fields)` sets the message to the
  event value, adds `event`, rejects forbidden names case-insensitively, rejects non-primitives,
  truncates strings to 256 and maps levels.

| Event | Level | Fields besides `event` |
| --- | --- | --- |
| `story_generation.started` | INFO | `candidate_id`, `story_id`, `status` |
| `story_generation.status` | INFO | `status`, `previous_status`, `candidate_id` |
| `story_generation.research_completed` | INFO | `status`, `context_count`, `evidence_count`, `failure_count` |
| `story_generation.facts_completed` | INFO | `status`, `fact_count` |
| `story_generation.draft_completed` | INFO | `round`, `revision_applied`, `note_count` |
| `story_generation.check_completed` | INFO | `round`, `passed`, `finding_count` |
| `story_generation.revision` | WARNING | `round`, `candidate_id` |
| `story_generation.activity_failed` | ERROR | `activity`, `error_type`, `status` |
| `story_generation.review_waiting` | INFO | `timeout_seconds`, `status` |
| `story_generation.decision` | INFO | `decision` (`approved`/`rejected`), `has_note` |
| `story_generation.ignored_signal` | WARNING | `signal`, `status` |
| `story_generation.finished` | INFO | `status`, `candidate_id`, `story_id` |

Never log titles, URLs, summaries, angles, reasons, prompts, model responses, article text, evidence
rows, drafts, packages, critiques, decision notes, emails or secrets; never log exception text
(`error_type` only). Logs are greppable with `grep story_generation`. Update the docs and add guard
tests.

## E2E proof design (deterministic, no live calls)

Add `tests/test_story_generation_e2e.py` using `WorkflowEnvironment.start_local` (visibility is
needed for the list pages) plus `httpx.MockTransport` for HN, article/OWID and Anthropic. Register
all nine search attributes exactly as the worker does. Run the real
`SourceIngestionWorkflow` + `StoryCandidateWorkflow` + `StoryGenerationWorkflow`, the real
activities (`research_story`, `calculate_facts`, `draft_story`, `critique_story`), the real
`CandidateService`/`StoryStatusService`/`StoryDetailService` over `Temporal*Gateway`, and the real
FastAPI app via `httpx.ASGITransport` with only the three service dependencies overridden. No
worker boot, network, database or clock-dependent wait beyond a 1-2 s review timeout.

New fixtures:

- `tests/fixtures/hn_source_poll_story_e2e.json`: stories 3001 (life-expectancy title, URL
  `https://news.example/story`), 3002 (URL `https://news.example/missing`), 3003 (rejected
  candidate).
- `tests/fixtures/hn_source_poll_story_e2e_errors.json`: stories 4001 and 4002 (supported), 4003
  (URL `https://news.example/retry`).
- `tests/fixtures/llm_select_story_candidates_story_e2e.json` and
  `llm_select_story_candidates_story_e2e_errors.json`: select all three stories in each batch.
- `tests/fixtures/owid_story_e2e.csv`: `World,,2019,72.0` and `World,,2020,72.5`, so the existing
  `llm_draft_story_clean.json`/`llm_draft_story_revision.json` fact and evidence ids validate.
- Reuse `research/article_supported.html`, `llm_draft_story_clean.json`,
  `llm_draft_story_revision.json`, `llm_critique_story_pass.json` and
  `llm_critique_story_missing_caveat.json`. Dispatch Anthropic responses by the tool name and by
  `external_id`/`Round:` in the request body: 4002 gets one failed round-0 check then a passing
  round-1 check; 4003's article mock returns HTTP 500 on every attempt so research exhausts three
  attempts and ends `failed`.

Scenarios and assertions:

1. **Success, unsupported, candidate rejection.** Approve 3001 and 3002, reject 3003. Assert the
   inbox shows Pending before the decisions; `/stories` shows Writing immediately after approval,
   then Review, then Ready after final approval; 3002 ends Closed with the "original article is no
   longer available" reason; 3003 has no `StoryGenerationWorkflow`. Assert `/stories/{id}` shows
   exactly one status stamp from `{Pending, Writing, Review, Ready, Closed}` and never an internal
   state name as a badge. Assert exactly two workflows for 3001 (candidate + story), two for 3002,
   and one for 3003; assert repeated approvals create no third story. With `now = created_at + 48 h
   + 1 s`, assert `StoryStatusService.list_stories`, `CandidateService.list_pending` and the
   view-model counts are empty while `describe()` still returns the completed candidate and story
   (presentation retention, no history deletion). Assert the story workflow imports/calls no
   Bluesky/chart/render activity.
2. **Story rejection, critique revision, failed after retry.** Approve 4001, 4002 and 4003. 4001
   reaches Review and is rejected through `POST /stories/{id}/decide` -> Closed/rejected. 4002
   reaches Review after exactly 2 draft and 2 critique calls and its package headline comes from the
   revision fixture -> final Approve -> Ready. 4003 ends Closed/failed with the research-retry detail
   after exactly 3 article attempts. Assert no duplicate rows across all filters.
3. **Review expiry.** Start one supported candidate directly with
   `StoryCandidateInput(story_review_timeout_seconds=1)`, approve it, and assert the story reaches
   `review` and then `expired` (Closed, "The review window closed.") through the real service and
   UI. The 48-hour cutoff itself is already proven by the time-skipping
   `test_review_expires_at_the_ui_cutoff_not_the_configured_timeout`; keep it unchanged.

Stop the ingestion workflow with its `stop` signal at the end of each test and assert
`reason="stopped"`.

## Runbooks and exact commands

Add `docs/runbooks/stories.md` with these sections and commands (wrappers copied from
`search-attributes.md`):

```sh
dev-temporal()  { docker compose -f infra/compose.dev.yaml exec temporal temporal "$@"; }
prod-temporal() { env -i PATH="$PATH" HOME="$HOME" docker compose -f infra/compose.prod.yaml \
  --env-file .env.prod exec temporal temporal "$@"; }
```

- **Two human decisions.** Candidate approval starts exactly one story; final story approval marks
  the package `ready` and never publishes. The first decision wins; review expiry closes the item.
- **Local manual walkthrough.** In `.env`, set `SOURCE_ENABLED=hn`,
  `SOURCE_CANDIDATE_BUDGET=5`, `SOURCE_POLL_INTERVAL_S=60` and `CANDIDATE_REVIEW_TIMEOUT_S=3600`.
  Run `just dev` (terminal 1), `just ingest` (terminal 2), `just web` (terminal 3). Open
  `http://127.0.0.1:8000/candidates`, approve a candidate, open `/stories`, follow the detail page
  through Writing -> Review, approve the final story, and confirm Ready and the "Nothing was
  published" copy. Check the worker log with `grep story_generation`; check the Temporal UI or:
  `dev-temporal workflow list --query 'WorkflowType = "StoryGenerationWorkflow"'`,
  `dev-temporal workflow query --workflow-id <story-id> --type story_state`,
  `dev-temporal workflow describe --workflow-id <story-id>`. Note that the live selector and live
  article may legitimately produce zero selections or an `unsupported` story; the deterministic
  proof is `uv run pytest tests/test_story_generation_e2e.py -q`.
- **Search attributes.** Run the create commands from `search-attributes.md` on a fresh stack, then
  `dev-temporal operator search-attribute list`; the worker registers the same nine on start.
  Verify stories with
  `dev-temporal workflow list --query 'WorkflowType = "StoryGenerationWorkflow" AND StoryStatus = "review"'`
  and `dev-temporal workflow list --query 'StoryCandidateId = "candidate-v1-..."'`.
- **Bounded budgets.** Copy the table above; explain that changing `SOURCE_CANDIDATE_BUDGET` or
  `SOURCE_POLL_INTERVAL_S` requires stop + `just ingest`, because they are baked into the ingestion
  input, and that per-story caps are code constants, not runtime settings.
- **Failure recovery.** Symptom -> check -> action table: candidate approved but no story
  (`review_state.story_workflow_id`, `describe`, worker on `story-generation-task-queue`, search
  attributes, `story_generation.started`, restart worker); story stuck (logs,
  `story_generation.activity_failed`, `describe`, Anthropic/HTTP health); `failed` after retries
  (terminal, fix upstream, new candidate); `unsupported` (expected evidence gap, fixed reason); UI
  503 (`just web`, Temporal connectivity, `StoryStatusUnavailable`); duplicate story
  (`StoryCandidateId` query must show exactly two workflows); review expired (terminal, no reopen).
- **Retention and replay.** Explain that 48 hours is presentation only: rows leave both pages,
  pending counts and filters, but `describe`/`show` still work and no history is deleted. Replay
  rules: add optional input fields with defaults, keep activity names/order, keep the
  `story-generation-handoff-v1` patch, and use a `story-v2-` id prefix only for an incompatible
  rewrite. Commands:
  `dev-temporal workflow show --workflow-id <story-id>`,
  `dev-temporal workflow list --query 'StoryStatus = "failed"'`.
- **Structured logs.** List the events and forbidden fields; dev is the worker terminal, prod is
  `env -i PATH="$PATH" HOME="$HOME" docker compose -f infra/compose.prod.yaml --env-file .env.prod logs -f worker | grep story_generation`.

Extend `docs/runbooks/search-attributes.md` with a "Story generation checks" section using the two
`workflow list --query` commands above, the note that `StoryStatus` stores internal states and never
the five UI badges, and that `StoryCreatedAt` (inherited) drives retention while `StoryUpdatedAt` is
sorting only. Extend `ingestion.md` with a one-line "approved candidates start stories; see
stories.md" cross-link. Extend `deploy.md` with the `stories.md` link and the optional research keys
`RESEARCH_FETCH_TIMEOUT_S`, `RESEARCH_MAX_RESPONSE_BYTES`, `RESEARCH_MAX_REDIRECTS`,
`RESEARCH_USER_AGENT` and `RESEARCH_EXTRA_CONTEXT_HOSTS`.

## Doc updates

- `README.md`: add "Story generation after approval" under Story candidates: the two human
  decisions, the internal stages, the five statuses, 48-hour presentation retention (not deletion),
  the budget table in prose, the `story_generation.*` log set and `grep` command, the search-
  attribute and `stories.md` links, the deterministic e2e command, and a short manual walkthrough.
- `docs/architecture/README.md`: expand "Story generation after candidate approval" with budgets,
  logs, runbook links and the e2e proof; add `operational_logging.py` and `story_logging.py` to the
  module map; add `stories.md` to "Which doc to read for what".
- `docs/architecture/tech.md`: add a "Story generation budgets and operational logs" subsection
  with the budget and event tables, enforcement points, forbidden fields, and the `stories.md`
  link; add the start_local e2e test to the Development tests paragraph.
- `docs/architecture/business.md`: add a "Story generation" subsection under Story candidates with
  the two decisions, five statuses, 48-hour retention, no publishing, and the budget envelope.
- `docs/history.md`: out of scope; the scribe records the merged-task entry.

## Acceptance criteria

- [ ] `tests/test_story_generation_e2e.py` passes with no network or live API calls and demonstrates
      HN poll -> selector -> pending candidate -> candidate approval -> research/analyze/write/check
      -> story review -> final approval -> `ready`, plus unsupported, failed-after-retry,
      critique-revision, candidate rejection, story rejection and review expiry through the real
      workflows, gateways, services and web routes. It asserts only the five UI statuses are shown,
      exactly one candidate row and one story workflow per approved candidate (two workflows total),
      48-hour retention by a future clock, empty pending counts/filters after the cutoff, and that
      histories still answer `describe()` after the UI hides them.
- [ ] `story_generation.*` events are emitted at start, every status transition, research/facts/
      draft/check completion, revision, activity failure, review wait, decision, ignored signal and
      finish, through a pure guard module. Forbidden fields, non-primitives and over-long strings are
      rejected/truncated; tests prove no title, URL, summary, prompt, article text, draft, critique,
      note, email or secret reaches a record.
- [ ] The budget decision is implemented as documentation plus tests, not new state: the per-source
      UTC-day `SOURCE_CANDIDATE_BUDGET`, the per-story activity/revision/attempt caps, the HTTP/text/
      fact caps and the 48-hour review cap are stated with exact names and bounds and covered by a
      test; no global quota workflow, table or new setting exists.
- [ ] `README.md`, `docs/architecture/README.md`, `tech.md`, `business.md`, the new
      `docs/runbooks/stories.md` and the extended `search-attributes.md`, `ingestion.md` and
      `deploy.md` contain the two human decisions, five-status table, 48-hour retention statement,
      search-attribute setup/verification, budget table, `story_generation.*` event table and grep
      commands, the manual local-stack procedure and the failure-recovery commands;
      `tests/test_story_docs.py` guards the required headings, commands and links.
- [ ] `just lint` and `just test` pass, `tests/test_story_generation_replay.py`,
      `tests/test_daily_chart.py`, `tests/test_chart_loop.py` and existing candidate/chart/web tests
      pass unchanged, and no publishing, chart or render activity is registered or called on the
      story path.
- [ ] Only the files listed below change; no new table, workflow type, settings field, UI/CSS change,
      cleanup timer, history deletion, publishing control, JavaScript framework, CDN asset or live
      API call is introduced.

## Files to touch

- `src/ai_trend_pipeline/operational_logging.py` (new: shared truncation/guard primitives)
- `src/ai_trend_pipeline/ingestion_logging.py` (import and re-export the shared names; behaviour
  unchanged)
- `src/ai_trend_pipeline/story_logging.py` (new: `StoryLogEvent`, story forbidden-field set,
  `log_story_event`)
- `src/ai_trend_pipeline/story_generation.py` (emit the fixed events; no state-machine, id, timeout
  or behaviour change)
- `tests/test_story_logging.py` (new)
- `tests/test_story_generation_logs.py` (new)
- `tests/test_story_generation_budget.py` (new)
- `tests/test_story_generation_e2e.py` (new)
- `tests/test_story_docs.py` (new)
- `tests/fixtures/hn_source_poll_story_e2e.json` (new)
- `tests/fixtures/hn_source_poll_story_e2e_errors.json` (new)
- `tests/fixtures/llm_select_story_candidates_story_e2e.json` (new)
- `tests/fixtures/llm_select_story_candidates_story_e2e_errors.json` (new)
- `tests/fixtures/owid_story_e2e.csv` (new)
- `README.md`
- `docs/architecture/README.md`
- `docs/architecture/tech.md`
- `docs/architecture/business.md`
- `docs/runbooks/stories.md` (new)
- `docs/runbooks/search-attributes.md`
- `docs/runbooks/ingestion.md`
- `docs/runbooks/deploy.md`

## Tests to write

- `tests/test_story_logging.py`: event namespace/uniqueness, INFO/WARNING/ERROR mapping, forbidden
  names are case-insensitive, non-primitives raise, strings truncate at 256, `event` is always set.
- `tests/test_story_generation_logs.py`: time-skipping success, revision, activity-failure and
  ignored-signal runs emit the fixed event set; no title/URL/summary/note/secret sentinel appears in
  the message or record fields.
- `tests/test_story_generation_budget.py`: assert the constants/timeouts/settings bounds from the
  budget table and that a retryable draft failure is attempted exactly 3 times before `failed`; no
  path exceeds 1 research, 1 facts, 2 drafts or 2 critiques.
- `tests/test_story_generation_e2e.py`: start_local test 1 (success, unsupported, candidate
  rejection, five UI statuses, one row/workflow, 48-hour future-clock retention, histories still
  described); test 2 (story rejection, 4002 revision with 2 draft/2 critique calls, 4003 failed
  after exactly 3 article attempts); test 3 (1 s story review timeout -> expired through services
  and UI); stop ingestion and assert no duplicate `StoryCandidateId` rows.
- `tests/test_story_docs.py`: guard `README.md`/architecture docs for the two decisions, five
  statuses, 48 hours, budget table, `story_generation.*`, runbook links; guard `stories.md` for its
  headings and the exact `just dev`/`just ingest`/`just web`, `workflow query --type story_state`,
  `WorkflowType = "StoryGenerationWorkflow"`, `StoryCandidateId`, `operator search-attribute list`,
  `grep story_generation`, `SOURCE_ENABLED=hn,fake` and `story-v2-` strings; guard
  `search-attributes.md` for the story query and `deploy.md` for the new runbook link and research
  keys.

## Out of scope

- New workflow types, activities, tables, migrations, search attributes, settings or `just` recipes;
  a global/per-day story quota workflow; any cleanup timer or history deletion.
- Changing candidate approval semantics, story ids, the five-status mapping, the 48-hour cutoff, the
  review timeout, `StoryPackage`/`Critique` contracts, prompts, model choice, retry counts or
  activity order/names.
- Any publishing, Bluesky, chart, render, media, analytics, UI/template/CSS redesign or new UI
  feature; the e2e only asserts the existing pages.
- Deleting or rewriting workflow histories, backfills, or `--reset`/`--terminate` operator advice
  beyond the documented replay/rollback guidance.
- Live HN, OWID, article, Anthropic or Bluesky calls in tests; real credentials or account details.
- `docs/history.md` (the scribe handles the merged-task entry), `.env`/`.env.prod` changes, and
  Google Trends.

## Assumptions

- The only hard daily bound needed is the existing per-source candidate budget; a true global story
  quota is rejected because it would require shared state outside Temporal and human approval
  already gates each story. This is recorded in the docs rather than implemented as new code.
- The workflow, not the activities, owns all story logs, so `workflow.logger` remains replay-safe and
  no activity module needs a new logging dependency.
- The e2e success path uses a new World 72.0/72.5 OWID fixture so the existing clean/revision draft
  and pass/caveat critique fixtures validate unchanged; no production prompt or fixture is edited.
- `start_local` is used for visibility/UI assertions; the 48-hour expiry itself stays in the existing
  time-skipping test, and the e2e proves retention by passing a future `now` to the services.
- The review-expiry e2e starts one candidate directly with `story_review_timeout_seconds=1` because
  the candidate gateway does not expose that field; this still uses the real candidate workflow,
  child start, story workflow, services and UI and adds no production parameter.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: wrote the spec for 056. Decisions: no new global story quota (per-source candidate
budget + per-story deterministic caps), a fixed `story_generation.*` event set in a new
`story_logging.py` with a shared guard, and a start_local + fixture e2e over real services/routes.
Next: human approval, then builder (tests first). No UI/CSS work; existing pages are asserted only.

**builder**: added `operational_logging.py`/`story_logging.py`, wired the fixed `story_generation.*`
set into `StoryGenerationWorkflow`, and added the four test modules, five fixtures, `stories.md` and
the README/architecture/runbook updates. E2E uses start_local with recorded fixtures; lint, strict
Pyright and the full suite pass (harness `ANTHROPIC_*` vars unset). Next: verifier.

**verifier**: `just lint` clean (ruff, format, strict Pyright); `just test` 1001 passed with harness
`ANTHROPIC_*` vars unset (set, 73 pre-existing LLM tests fail with `anthropic.APIConnectionError`).
`tests/test_story_generation_e2e.py` green in 5/5 repeated runs. Next: reviewer.

**reviewer**: approved. `just lint` clean; full suite 1001 passed; `tests/test_story_generation_e2e.py` 3 passed. Notes: `stories.md` 503 row names `StoryStatusUnavailable` for `/candidates`, which actually raises `CandidateUnavailable`; e2e view-model count asserts pass empty pages (service-level assertions carry retention). Next: scribe.

**scribe**: fixed the `/candidates` 503 row to `CandidateUnavailable`, recorded the 050-056 phase in `docs/history.md`, and left README/architecture unchanged where 056 already documented them. PR: https://github.com/akonale/ai-trend-pipeline/pull/63. Next: human merge.
