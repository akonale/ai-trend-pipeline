# Architecture: start here

Topic Studio turns a topic into one chart post on Bluesky, with a human approving it first.
A person types a topic on the web page or passes `--topic` to the CLI; without a topic (CLI only,
`just chart`) the LLM picks one from Hacker News. There is no scheduler: nothing starts a run on its own. An LLM writes a Vega-Lite chart and caption from Our World in Data (OWID) data,
a second LLM call critiques it, the chart is rendered to a PNG, a human approves or rejects it, and
approval posts it to Bluesky. Everything is orchestrated by one Temporal workflow.

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

## Module map (`src/ai_trend_pipeline/`)

| Module | Purpose |
| --- | --- |
| `config.py` | `Settings` and `load_settings()`; the only place env vars are read |
| `hn.py` | Activity `fetch_hn_top_stories` (Hacker News API) |
| `sources/` | Frozen bounded source contracts, static registry, shared normalization, HN and fake connectors (`poll_hn_source`); no worker wiring yet |
| `story_selection.py` | Pure `prefilter_events` and activity `select_story_candidates` (batch LLM editorial selection); no worker wiring yet |
| `story_candidate.py` | `StoryCandidateWorkflow` (one review per candidate), candidate models, `candidate_id_for`, candidate search-attribute keys |
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
| `worker.py` | Runs the hello, daily-chart and story-candidate workers; wires activity classes |
| `temporal_client.py` | `connect(settings)` with the Pydantic data converter; `ensure_candidate_search_attributes` |
| `cli.py` | argparse front end over `ChartService` |
| `services/` | `charts.py`, `candidates.py`, `styles.py`, `postgres_style_store.py`, `temporal_gateway.py` |
| `web/` | `app.py`, `dependencies.py`, `routes/`, `view_models.py`, `templates/`, `static/` |
| `hello.py`, `start_hello.py` | Smoke-test workflow (`just hello`) |

## Where does state live

| State | Where | Notes |
| --- | --- | --- |
| Run status, review, requester, approver, options | Temporal workflow history | Read by query `review_state`; no app database for runs |
| Candidate review status, decision | Temporal workflow history (`StoryCandidateWorkflow`) | Read by query `review_state`; pending list from visibility (`CandidateStatus`, `CandidateSource`) |
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
- [../../CLAUDE.md](../../CLAUDE.md): rules every agent follows. [../mvp-plan.md](../mvp-plan.md): the agent relay.
