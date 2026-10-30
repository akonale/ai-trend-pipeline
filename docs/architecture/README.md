# Architecture: start here

Topic Studio turns a topic into one chart post on Bluesky, with a human approving it first.
A person types a topic on the web page or passes `--topic` to the CLI; without a topic (CLI only,
`just chart`) the LLM picks one from Hacker News. Chart runs are started by a person: nothing starts
a chart on its own. Ensured source ingestion workflows do poll continuously on Temporal timers and
start story candidates. An LLM writes a Vega-Lite chart and caption from Our World in Data (OWID)
data, a second LLM call critiques it, the chart is rendered to a PNG, a human approves or rejects
it, and approval posts it to Bluesky. Everything is orchestrated by one Temporal workflow.

## System diagram

```
 browser ──▶ Cloudflare Access ──▶ cloudflared ──▶ web (FastAPI, :8000)  ──┐
 terminal ──▶ cli.py (just chart/show/approve/reject) ────────────────────┤
                                                                          ▼
                                     services/ (ChartService, StyleService)
                                          │ gateway            │ StyleStore
                                          ▼                    ▼
                              Temporal server  ◀──────  Postgres topic_studio.style_profiles
                                   │  (task queue daily-chart-task-queue)
                                   ▼
                         worker: DailyChartWorkflow ──▶ activities
                                   HN ─ topic ─ OWID ─ chart spec ─ critique ─ render ─ Bluesky
                                   │                                  │             │
                              (Anthropic API)                  PNG on volume     bsky.social
```

## One chart run, end to end

1. `POST /runs` (`web/routes/runs.py`) or `cli.py start` builds a `StartChartRequest` and calls
   `ChartService.start` (`services/charts.py`).
2. The service checks the daily cap (web only), loads the person's style profile
   (`StyleStore.get`, web only), merges per-post options (`merge_style`), and builds `DailyChartInput`.
3. `TemporalChartRunGateway.start` starts `DailyChartWorkflow` (`daily_chart.py`) with the
   workflow id and a `requested_by` memo.
4. The worker runs activities in order: `fetch_hn_top_stories`, `pick_topic` (skipped when a topic
   was given), `fetch_owid_dataset`, then `write_chart_spec` + `critique_chart` (one regenerate on
   failure, `chart_loop.py`), then `render_chart_png`.
5. The workflow sets status `awaiting_decision` and waits up to 20 hours for the `decide` signal.
6. The run page polls `GET /runs/{id}/status` (query `review_state`) and shows the PNG from
   `GET /runs/{id}/chart.png`. Approve/Reject posts `/runs/{id}/decision`, which signals `decide`.
7. On approval the workflow runs `post_chart_to_bluesky` (idempotent) and finishes `approved`.

## Continuous source ingestion

`SourceIngestionWorkflow` (one per enabled source, id `source-ingestion-v1-<source>`, task queue
`source-ingestion-task-queue`) polls the source activity on a Temporal timer, drops stories already
in its bounded processed-id set, prefilters the rest, calls `select_story_candidates`, and starts
one `StoryCandidateWorkflow` per selected story. Two sources are registered: `hn` (Hacker News) and
`fake` (Test source, test-only, off by default). Cursor, processed ids and the per-source UTC-day
candidate budget live only in workflow state and are carried through continue-as-new.

The budget (`SOURCE_CANDIDATE_BUDGET`, default 20) bounds newly started candidate workflows per
source per UTC day. When it is exhausted the workflow skips the selector entirely, so it spends no
LLM credit, leaves the batch unprocessed for the next UTC day, and logs once. Operational events
use the fixed `candidate_ingestion.*` names from `ingestion_logging.py`; a field guard keeps titles,
URLs, prompts, payloads and secrets out. Workers never start ingestion on boot: run `just ingest`
(or the production `docker compose exec worker python -m ai_trend_pipeline.ingestion_starter`
command) to ensure the workflows exist; repeated runs are idempotent. Operate it with the
[ingestion](../runbooks/ingestion.md) and
[search-attributes](../runbooks/search-attributes.md) runbooks.

## Story generation after candidate approval

Approving a candidate starts exactly one `StoryGenerationWorkflow` child without waiting for it.
Its id is deterministic and versioned, `story-v1-` plus the first 16 hex chars of SHA-256 over the
candidate workflow id, so repeated approvals cannot create a second story. The candidate result and
its `review_state` query include `story_workflow_id`.

The story workflow runs on `story-generation-task-queue`:

```
researching ─▶ analyzing ─▶ writing ─▶ checking ─┬─ passed ─▶ review ─┬─ approve ─▶ ready
     ▲              │           │          │      │                   ├─ reject  ─▶ rejected
     │              │           └ one revision ┘                     └─ 48 h    ─▶ expired
     └──────────────┴─ activity failure / invalid draft / failed revision ──────▶ failed
```

`researching` calls `research_story`; `analyzing` calls `calculate_facts` with a 16-fact budget;
`writing` calls `draft_story`; `checking` calls `critique_story` at round 0 and, after one failed
check, exactly one revision at round 1. Unsupported research ends before any writing. Any activity
failure after retries, or a second failed check, is terminal `failed`. Review waits for final
`approve`/`reject` signals; the first decision wins. `ready` only means the package was approved:
nothing publishes or renders.

Candidate and story review deadlines never extend past `StoryCreatedAt + 48 hours`. The seven
shared visibility attributes (`StoryStatus`, `StoryTitle`, `StorySource`, `StoryCandidateId`,
`StoryCreatedAt`, `StoryUpdatedAt`, `StoryStatusDetail`) let the list pages read bounded metadata
without workflow queries; the story inherits `StoryCreatedAt` from the candidate's original
detection time. `story_status.primary_story_status` maps every internal state to the five UI
statuses (`pending`, `writing`, `review`, `ready`, `closed`). `StoryStatusService` reads both
workflow types in one visibility query for `GET /stories`, de-duplicates the candidate-to-story
handoff by `StoryCandidateId`, and hides anything at or past 48 hours. `/candidates` keeps its own
pending query but applies the same cutoff. `GET /stories/{id}` shows one workflow's detail through
`StoryDetailService`: queries while open, typed results when terminal, one final Approve/Reject
signal at `review`, and the same 48-hour cutoff.

Pre-053 candidate histories still replay: the new terminal upsert and child start sit behind
`workflow.patched("story-generation-handoff-v1")`, and the new input, query and result fields are
optional with defaults.

Budgets are layered in existing code rather than a new global counter: the per-source UTC-day
`SOURCE_CANDIDATE_BUDGET` (1-500, default 20) bounds candidate starts, deterministic `story-v1-`
ids allow exactly one story per candidate ever, and each story makes one research call, one facts
call, at most two drafts and at most two critiques with three attempts each and fixed timeouts.
Article HTTP, contexts/evidence, facts and draft text also have fixed bounds; the review wait is
capped at `story_created_at + 48 h`. No budget workflow, table or setting exists.

`StoryGenerationWorkflow` is the only writer of the fixed `story_generation.*` events
(`started`, `status`, `research_completed`, `facts_completed`, `draft_completed`,
`check_completed`, `revision`, `activity_failed`, `review_waiting`, `decision`, `ignored_signal`,
`finished`). The pure `story_logging.py` guard rejects forbidden names, non-primitives and
over-long strings, so titles, URLs, prompts, article text, drafts, critiques, notes, emails and
secrets never reach a record. Filter with `grep story_generation`.

Operate the feature with the [stories runbook](../runbooks/stories.md),
[search-attributes](../runbooks/search-attributes.md) and [ingestion](../runbooks/ingestion.md)
runbooks. The deterministic end-to-end proof is
`uv run pytest tests/test_story_generation_e2e.py -q`: recorded HN, article, OWID and Anthropic
fixtures with the real workflows, activities, gateways, services and FastAPI routes on a
`start_local` server, covering success, unsupported, failed-after-retry, critique revision,
candidate rejection, story rejection, review expiry, one row/workflow per candidate, the 48-hour
cutoff and replay-safe histories.

## Module map (`src/ai_trend_pipeline/`)

| Module | Purpose |
| --- | --- |
| `config.py` | `Settings` and `load_settings()`; the only place env vars are read |
| `hn.py` | Activity `fetch_hn_top_stories` (Hacker News API) |
| `sources/` | Frozen bounded source contracts, static registry (`hn`, `fake`), shared normalization, HN and fake connectors (`poll_hn_source`, `poll_fake_source`); fake is test-only |
| `ingestion_logging.py` | Pure fixed `candidate_ingestion.*` event set, forbidden-field guard and truncation |
| `operational_logging.py` | Shared pure primitives: `MAX_LOG_VALUE_LENGTH`, `FORBIDDEN_LOG_FIELDS`, the `StructuredLogger` protocol and `check_log_field` |
| `story_logging.py` | Pure fixed `story_generation.*` event set, story forbidden-field set and level mapping |
| `story_selection.py` | Pure `prefilter_events` and activity `select_story_candidates` (batch LLM editorial selection) |
| `source_ingestion.py` | `SourceIngestionWorkflow`: one long-running poll/select/start loop per enabled source, bounded cursor, processed ids and per-source UTC-day candidate budget |
| `ingestion_starter.py` | Operator entry point (`just ingest`) that ensures source ingestion workflows exist |
| `story_candidate.py` | `StoryCandidateWorkflow` (one review per candidate), candidate models, `candidate_id_for`, `story_input_for`, candidate search-attribute keys and the version-gated story handoff |
| `story_generation.py` | `StoryGenerationWorkflow` (research → analyze → write → check once → final review), story models, `story_id_for`, queue/id/retry constants |
| `story_status.py` | Pure five-status mapping (`primary_story_status`, `PrimaryStoryStatus`) and the 48-hour retention constants |
| `story_visibility.py` | The seven shared visibility attribute keys, `story_start_attributes`, `story_status_updates` and the bounded detail cleaner |
| `research/` | Bounded research contracts, a safe bounded HTTP fetcher, and the `fetch_context`, `match_owid_evidence`, `research_story` activities (the story worker registers `research_story`) |
| `analysis/` | Frozen fact contracts and a pure deterministic `build_fact_set`; the sync `calculate_facts` activity (registered on the story worker with an activity executor) |
| `story_drafting/` | Frozen `StoryPackage`/critique contracts, pure deterministic validation, and the `draft_story`/`critique_story` structured-tool activities (registered on the story worker) |
| `topic.py` | Activity `pick_topic` (LLM picks an OWID catalog title); `make_anthropic_client` |
| `owid.py` | `CATALOG`, `match_dataset`, activity `fetch_owid_dataset`, `OwidTable` |
| `post_options.py` | `PostOptions`, row filtering, chart-type rewrite, options prompt, `merge_style` (pure) |
| `style.py` | `ChartStyle`, palettes, fonts, tones, caption suffix, `apply_style_to_spec` (pure) |
| `chart_spec.py` | Activity `write_chart_spec` (LLM writes spec and caption) |
| `critique.py` | Activity `critique_chart` (LLM reviews the chart) |
| `chart_loop.py` | `draft_chart_with_critique` (workflow helper), `DraftChartWorkflow` (not registered) |
| `render.py` | Activity `render_chart_png` (vl-convert) |
| `bluesky.py` | Activity `post_chart_to_bluesky`, credentials, TID record key |
| `daily_chart.py` | `DailyChartWorkflow`, `DailyChartInput`, `ReviewState`, `ApprovalDecision` |
| `worker.py` | Runs the hello, daily-chart, story-candidate, ingestion and story-generation workers; wires activity classes |
| `temporal_client.py` | `connect(settings)` with the Pydantic data converter; `ensure_story_search_attributes` (and the candidate wrapper) |
| `cli.py` | argparse front end over `ChartService` |
| `services/` | `charts.py`, `candidates.py`, `stories.py`, `styles.py`, `postgres_style_store.py`, `temporal_gateway.py` |
| `web/` | `app.py`, `dependencies.py`, `routes/`, `view_models.py`, `templates/`, `static/` |
| `hello.py`, `start_hello.py` | Smoke-test workflow (`just hello`) |

## Where does state live

| State | Where | Notes |
| --- | --- | --- |
| Run status, review, requester, approver, options | Temporal workflow history | Read by query `review_state`; no app database for runs |
| Candidate review status, decision | Temporal workflow history (`StoryCandidateWorkflow`) | Read by query `review_state`; pending list from visibility (`CandidateStatus`, `CandidateSource`); the approved result carries `story_workflow_id` |
| Story generation state, draft, critique, final decision | Temporal workflow history (`StoryGenerationWorkflow`) | Read by query `story_state`; closed runs by typed result; no story table |
| Story list metadata (status, title, source, candidate id, detection/update time, sanitized detail) | Temporal visibility attributes | Shared by candidates and stories; `StoryStatus` stores the internal state, never the UI badge; `/stories` and `/candidates` read only the strict last 48 hours (`StoryCreatedAt`) |
| Source cursor, processed ids, poll counters | Temporal workflow history (`SourceIngestionWorkflow`) | Bounded (1000 processed ids) and carried through continue-as-new; no Postgres table |
| Source candidate budget (UTC day, starts) | Temporal workflow history (`SourceIngestionWorkflow`) | Per source, resets on a new UTC day with `workflow.now()`; carried through continue-as-new; no Postgres table |
| Per-person style profile | Postgres `topic_studio.style_profiles` (default `postgres` db) | Created lazily on first use |
| Chart PNGs | Directory `CHART_OUTPUT_DIR` (prod volume `charts`, mounted in worker and web) | File name `chart-<spec hash>.png` |
| Daily cap count | Derived from Temporal visibility (memo `requested_by`) | Not stored separately |

Nothing else is persisted.

## Layering rules

```
 web/ , cli.py  ──▶  services/  ──▶  Temporal client  ──▶  workflows  ──▶  activities
```

Imports point one way. Workflows and activities never import `services/` or `web/`. Env vars are
read only in `config.py`. Pure helper modules (`style.py`, `post_options.py`) do no I/O so workflow
code may import them. Full rules: `CLAUDE.md`.

## Glossary

- **OWID**: Our World in Data; the source of chart data (CSV from its grapher URLs).
- **Catalog**: the six OWID datasets in `owid.CATALOG` that topics can map to.
- **Run**: one `DailyChartWorkflow` execution; its workflow id is the run id.
- **Candidate**: one selected source story awaiting review, one `StoryCandidateWorkflow` per
  source/external id; its id is `candidate-v1-<16 hex chars>`.
- **Review**: the drafted chart, caption and critique awaiting a decision (`ReviewRequest`).
- **Style profile**: per-person palette, font, tone, hashtags, signature (`ChartStyle`).
- **Post options**: per-run chart type, time range, countries, tone, instructions (`PostOptions`).
- **Gateway**: the protocol a service talks to instead of Temporal; `TemporalChartRunGateway` and
  `TemporalCandidateGateway` are the real adapters.
- **Task / relay**: the planner-builder-verifier-reviewer-scribe workflow (see `docs/mvp-plan.md`).

## Which doc to read for what

- [business.md](business.md): what the product does: data sources, chart production, style and
  options, approval and posting rules, known limitations.
- [tech.md](tech.md): how it is built: Temporal workflow and activities, web app, infra and
  deployment, every setting, development how-to and pitfalls.
- [../history.md](../history.md): why things are the way they are (decisions and gotchas).
- [../runbooks/deploy.md](../runbooks/deploy.md), [../runbooks/cloudflare.md](../runbooks/cloudflare.md): operations.
- [../runbooks/ingestion.md](../runbooks/ingestion.md): ensure, enable/disable, recover, replay and the two-source demo.
- [../runbooks/stories.md](../runbooks/stories.md): the two human decisions, local walkthrough, budget enforcement, search-attribute checks, failure recovery, 48-hour retention, replay and `story_generation.*` logs.
- [../runbooks/search-attributes.md](../runbooks/search-attributes.md): `CandidateStatus` / `CandidateSource` registration and verification.
- [../../CLAUDE.md](../../CLAUDE.md): rules every agent follows. [../mvp-plan.md](../mvp-plan.md): the agent relay.
