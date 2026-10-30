# Build history

How the MVP and the Topic Studio web app were built, one line per task. The per-task specs and
handoff logs were removed after merge. The full text is still in git history: run
`git log --all -- docs/tasks/<file>` to list a task's commits, then
`git show <sha>:docs/tasks/<file>` to read it at any commit before the purge.

PRs are on GitHub. Merge dates are UTC, taken from the PR list.

## Phase 0: Foundation

| Task | What was done | PR | Merged |
| --- | --- | --- | --- |
| 001 | Project skeleton: uv, Python 3.12, Pydantic, Temporal SDK, Ruff, Pyright strict, pytest, justfile | #3 | 2026-10-21 |
| 002 | CLAUDE.md with project rules and the review checklist | #5 | 2026-10-21 |
| 003 | Local stack: compose Postgres and Temporal dev server, `just dev`, hello workflow | #6 | 2026-10-21 |
| 004 | CI: GitHub Actions runs `just lint` and `just test` on every PR | #7 | 2026-10-21 |

## Phase 1: Daily chart

| Task | What was done | PR | Merged |
| --- | --- | --- | --- |
| 005 | Activity: fetch Hacker News top stories | #10 | 2026-10-21 |
| 006 | Activity: LLM picks a chartable topic (forced tool call) | #11 | 2026-10-21 |
| 007 | Activity: fetch the matching Our World in Data (OWID) dataset | #12 | 2026-10-21 |
| 008 | Activity: LLM writes Vega-Lite spec and caption; render PNG (`vl-convert-python`) | #13 | 2026-10-21 |
| 009 | Activity: LLM critique, one regenerate on failure (`chart_loop`) | #14 | 2026-10-21 |
| 010 | `DailyChartWorkflow`: wires 005-009, approval signal, CLI approve/reject | #15 | 2026-10-23 |
| 011 | Activity: post the approved chart to Bluesky, idempotent | #17 | 2026-10-23 |

## Fixes

| Task | What was done | PR | Merged |
| --- | --- | --- | --- |
| 017 | LLM activities use `tool_choice` auto (the model rejected a forced tool) | #18 | 2026-10-23 |
| 018 | Topic picker chooses from the OWID catalog titles; `match_dataset` matches titles | #19 | 2026-10-23 |
| 019 | Manual topic override: `just chart --topic "..."` | #20 | 2026-10-23 |

## Phase 2-4: Topic Studio web app and sharing

| Task | What was done | PR | Merged |
| --- | --- | --- | --- |
| plan | Topic Studio plan added to `docs/mvp-plan.md` | #21 | 2026-10-26 |
| 020 | Service layer (`services/charts.py`) and `Settings` in `config.py` | #22 | 2026-10-26 |
| 021 | Web skeleton: FastAPI, Jinja, vendored htmx, `/health`, `just web` | #23 | 2026-10-28 |
| 022 | Home page: topic box and Generate; `POST /runs` | #24 | 2026-10-28 |
| 023 | Run page: live status (htmx polling), PNG preview, Approve/Reject, Temporal link | #25 | 2026-10-28 |
| 025 | Cloudflare Tunnel: `cloudflared` in prod compose, `.env.prod`, prod ports, runbook | #29 | 2026-10-28 |
| 026 | `current_user`: requester and approver stored on each run and shown on the run page | #32 | 2026-10-28 |
| 027 | Per-person daily cap (`DAILY_CHART_CAP`, default 10, UTC day) | #34 | 2026-10-28 |

## Phase 5: Your style

| Task | What was done | PR | Merged |
| --- | --- | --- | --- |
| 028 | Style profiles: palette, font, tone, hashtags, signature; `/settings`; stored in Postgres | #35 | 2026-10-29 |
| 029 | Per-post options: chart type, time range, countries, tone, instructions | #36 | 2026-10-29 |

## Phase 7-8: Production and look

| Task | What was done | PR | Merged |
| --- | --- | --- | --- |
| 034 | Prod stack with docker-compose: `Dockerfile`, `compose.prod.yaml`, `just deploy`, deploy runbook | #28 | 2026-10-28 |
| 035 | UI refresh: editorial theme tokens, light and dark, cards, badges, progress bar | #33 | 2026-10-28 |

## Phase 9: Real-time story candidates

| Task | What was done | PR | Merged |
| --- | --- | --- | --- |
| 042 | Source contracts: bounded `SourceEvent`/`SourcePollInput`/`SourcePollOutput`, static registry, shared normalization, fake connector, `poll_hn_source` activity | #48 | 2026-10-29 |
| 043 | Batch LLM editorial selection: pure prefilter and `select_story_candidates` activity (may select none, cap enforced in code) | #49 | 2026-10-29 |
| 044 | `StoryCandidateWorkflow` and Temporal gateway: deterministic ids, Approve/Reject signals, expiry, visibility attributes | #50 | 2026-10-29 |
| 045 | Candidate inbox page at `/candidates`: paging, empty and unavailable states, source badges | #51 | 2026-10-29 |
| 046 | Candidate detail and decisions at `/candidates/{id}`: safe source link, metrics, approve/reject, end states | #52 | 2026-10-29 |
| 047 | Candidate navigation and visual polish: Candidates in the header with an active state and pending badge | #53 | 2026-10-29 |
| 048 | Continuous HN ingestion: one `SourceIngestionWorkflow` per enabled source, cursor and processed ids, timers, continue-as-new, `just ingest` | #54 | 2026-10-29 |
| 049 | Hardening and docs: per-source UTC-day candidate budget, structured `candidate_ingestion.*` logs, fake second source and two-source proof, operator runbooks | #55 | — |

## Phase 10: Story generation end to end

The milestone landed: a person-approved candidate becomes a bounded, evidence-backed story package
that waits for a second human decision and never publishes on its own.

| Task | What was done | PR | Merged |
| --- | --- | --- | --- |
| 050 | Bounded story research: Pydantic contracts, safe bounded HTTP fetch, OWID evidence matching, `research_story` with explicit `supported`/`unsupported` | #57 | 2026-10-30 |
| 051 | Deterministic facts: versioned `Fact`/`FactSet`, pure `build_fact_set` and the `calculate_facts` activity | #58 | 2026-10-30 |
| 052 | Story drafting: `StoryPackage` contract, `draft_story` writer and `critique_story` checker, with fact ids, numbers and URLs validated in code | #59 | 2026-10-30 |
| 053 | `StoryGenerationWorkflow`: research → facts → draft → check with one revision, final review, deterministic candidate handoff, seven shared visibility attributes, five-status mapping and 48-hour caps | #60 | 2026-10-30 |
| 054 | `/stories` ledger: five statuses, filters, forward paging, rolling 48-hour cutoff and an htmx refresh only while a row is active | #61 | 2026-10-30 |
| 055 | `/stories/{id}` detail: progress while active, full package with final Approve/Reject at review, read-only at ready and clear closed reasons | #62 | 2026-10-30 |
| 056 | Story generation end to end: recorded-fixture e2e over the real workflows, gateways, services and routes; sanitized `story_generation.*` logs; budget and doc guards; stories runbook | — | — |


## Chores

| Change | PR | Merged |
| --- | --- | --- |
| Bootstrap script for existing servers | #1 | 2026-10-21 |
| Agentic MVP plan, subagents and `/run-task` | #2 | 2026-10-21 |
| "Your approval?" column in the task plan | #8 | 2026-10-21 |
| CLAUDE.md approval rule aligned with the plan | #9 | 2026-10-21 |
| `just` recipes load `.env` | #16 | 2026-10-23 |
| Ignore local Claude settings and worktrees | #27 | 2026-10-28 |
| Bump pinned image tags (Dockerfile, prod and dev compose) | #30 | 2026-10-28 |
| Deploy runbook: image tag upgrades and Postgres collation fix | #31 | 2026-10-28 |

## Planned but not built

Dropped when the MVP was closed. The descriptions are copied from `docs/mvp-plan.md` on main.

- 024: Runs list on the home page: recent runs with topic, status, and link (service wraps the Temporal list API).
- 030: Refine loop: a `refine` signal with an instruction ("make it a bar chart") regenerates spec and caption, re-runs critique, and waits for review again (max 5 rounds); `just refine ID "..."`.
- 031: Refine box on the run page, with the history of versions.
- 032: Topic matching: show the 5 closest OWID datasets for a typed topic and let you pick one.
- 033: Friendly run timeline on the run page: plain-language steps with timings, read from the workflow history via the service.

## Decisions and gotchas worth remembering

Access and environments
- Prod is compose project `ai-trend-prod`, publishing only `127.0.0.1:18000` (web) and
  `127.0.0.1:18233` (Temporal UI). Dev keeps 5432, 7233, 8233 and `just web` keeps 8000, so both run
  side by side. An old stack under the default name `infra` can be removed with
  `docker compose -p infra down`.
- Prod reads `.env.prod` only. `just deploy` runs compose under `env -i`, because `set dotenv-load`
  exports the dev `.env` and shell variables beat `--env-file`. Do the same by hand.
- The Cloudflare Tunnel is the only public path. With `AUTH_MODE=cloudflare` (a literal in prod
  compose), the web app trusts the `Cf-Access-Authenticated-User-Email` header and returns 401 on POST
  routes without it. The Access JWT is not verified, so the trust rests on the web port being bound to
  127.0.0.1. Local mode records everyone as `dev@localhost`.
- Set `TEMPORAL_UI_URL` in `.env.prod` to the public Temporal hostname; the compose default only works
  through the SSH tunnel. `APP_URL` is documentation only.
- The worker will not start without `ANTHROPIC_API_KEY`, `BLUESKY_HANDLE` and `BLUESKY_APP_PASSWORD`.

Runs, approvals and posting
- The requester is in the workflow input (`requested_by`) and the approver is in the signal
  (`decided_by`). Both are copied into the query state. There is no database. The CLI sends no user.
- The first decision wins. Approve sets `posting` before the Bluesky call. No decision within 20 hours
  ends the run as `expired`, with nothing posted.
- Posting is idempotent: the record key is a TID built from `workflow.now()` and the workflow id, and
  `getRecord` is checked before any upload.
- The live post to the test account (011's manual check) is not recorded as done in the repo.

Daily cap
- Counted with Temporal's visibility list API: `DailyChartWorkflow` runs started since 00:00 UTC whose
  `requested_by` memo matches. Runs without the memo (CLI runs, and runs before 027) are not counted.
  The CLI is never capped.
- The dev server is in memory (`start-dev`), so restarting `just dev` resets dev counts. Everyone in
  dev is `dev@localhost`, so they share one cap.

Postgres and style profiles
- Styles live in schema `topic_studio` in the default `postgres` database. The schema and table are
  created on first read or save.
- Prod `DATABASE_URL` is built in compose from `POSTGRES_PASSWORD` in libpq key/value form, so the
  password may not contain spaces, quotes or backslashes. The worker gets no `DATABASE_URL`.
- Only starting a web run (its style lookup) and the settings page use Postgres. When it is down,
  `POST /runs` and `/settings` return 503, and the other run routes still work.
- The Postgres 16.4 to 16.15 bump needed `ALTER DATABASE ... REFRESH COLLATION VERSION` on the existing
  volume, or Temporal crash-loops on a collation mismatch. See `docs/runbooks/deploy.md`. A major
  version bump needs a dump and restore instead.

Charts, LLM and limits
- The image installs `fonts-liberation`. `vl-convert` bundles only Liberation Sans, so without it the
  serif and mono charts look the same as sans.
- Topic picking is limited to the six OWID `CATALOG` titles. Off-catalog picks are retried, and a
  `--topic` outside the catalog fails with `NoMatchingDataset`.
- The critique cannot tell a requested bar chart from a bad one.
- Per-post time presets are relative to the dataset's latest year, not to today.
- Numbers 012-016 are not in the plan. Prod pins the Temporal
  auto-setup image at 1.29.7; moving to the `temporalio/server` image is a possible follow-up.

Story candidates and ingestion
- The candidate budget is per source per UTC day (`SOURCE_CANDIDATE_BUDGET`, default 20) and is
  baked into the workflow input at start. Changing it, the poll limit, the interval or the review
  timeout requires stopping the source workflow and re-running `just ingest`; a running workflow
  keeps its old value. Exhausted budgets skip the selector, so no LLM credit is spent, and the batch
  is re-evaluated after the UTC reset.
- A batch whose selector ran is marked processed even when the budget truncated starts, so those
  stories are intentionally dropped rather than re-evaluated. Duplicate child starts consume no
  budget.
- `fake` is a test-only source. It is registered and its activity is always on the ingestion
  worker, but `SOURCE_ENABLED` defaults to `hn`; never put `fake` in `.env.prod`.
- Disabling a source does not stop its workflow or its pending reviews. Remove it from
  `SOURCE_ENABLED`, run `just ingest`, then signal stop or terminate the old workflow; candidates
  are `ABANDON`ed and stay reviewable and decidable.
- Operational logs are the fixed `candidate_ingestion.*` set. The helper rejects source text and
  secrets, so titles, URLs, summaries, angles, prompts, payloads and credentials never reach them.

Story generation
- There are two human decisions. Candidate approval starts exactly one deterministic
  `story-v1-<hash>` story and never a second; final story approval marks the package `ready` and
  still publishes nothing. The first decision wins, and a closed review window ends `expired`
  (terminal). Nothing is posted, charted or rendered on the story path.
- Users see only five statuses (`pending`, `writing`, `review`, `ready`, `closed`). Internal states
  such as `researching` live in `StoryStatus` for operators and are never shown as UI badges.
- The 48-hour window is presentation only. At `StoryCreatedAt + 48 h`, rows leave `/stories`,
  `/candidates`, filters, pagination and pending counts, but workflow histories remain and
  `describe`/`show` still answer. There is no cleanup timer and no history deletion.
- Budgets are layered in existing code: the per-source UTC-day `SOURCE_CANDIDATE_BUDGET`, one story
  per candidate forever, one research and one facts call, at most two drafts and two critiques,
  three attempts per activity, fixed timeouts, bounded article HTTP/context/facts/text, and a review
  wait capped at 48 hours. There is no global story quota, table or new setting. Changing ingestion
  budgets requires stopping the source workflow and re-running `just ingest`.
- Logs are the fixed `story_generation.*` set, written only by the workflow through the pure
  `story_logging.py` guard. Titles, URLs, summaries, prompts, article text, drafts, critiques, notes,
  emails and secrets never reach a record; filter with `grep story_generation`.
- The deterministic proof is `uv run pytest tests/test_story_generation_e2e.py -q` (`start_local`,
  recorded HN/OWID/Anthropic fixtures, real services and routes, no live calls). Operate and
  troubleshoot with `docs/runbooks/stories.md`.

Google Trends remains a follow-up connector until its API/provider access is chosen.
