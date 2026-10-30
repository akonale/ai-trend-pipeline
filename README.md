# ai-trend-pipeline

Architecture: start at [docs/architecture/README.md](docs/architecture/README.md) (what it does: `business.md`; how it is built: `tech.md`).

Python 3.12 project managed with [uv](https://docs.astral.sh/uv/). Tasks run through a [just](https://just.systems/) justfile.

## Setup

```sh
uv sync
```

Creates `.venv` and installs runtime and dev dependencies from `uv.lock`.

## Commands

```sh
just          # list recipes
just lint     # ruff check, ruff format --check, pyright (strict)
just fmt      # ruff format and ruff check --fix
just test     # pytest
just ingest   # ensure one ingestion workflow per enabled source (idempotent)
```

If `just` is not installed, run the same commands with `uv run`, for example `uv run pytest`.

All tool config lives in `pyproject.toml`.

## Local stack

Needs Docker. Postgres and a Temporal dev server run through `infra/compose.dev.yaml`.

```sh
just dev     # terminal 1: start the stack, then run the worker
just hello   # terminal 2: run HelloWorkflow
just down    # stop the stack
```

Open http://localhost:8233 to see `hello-workflow` as Completed.

On a remote dev server, tunnel
the UI port first: `ssh -L 8233:localhost:8233 <host>`.

## Web app

```sh
just web     # serves http://127.0.0.1:8000 (health page at /health)
```

On a remote dev server, tunnel the port first: `ssh -L 8000:localhost:8000 <host>`. The page needs
no Temporal connection. htmx 2.0.4 is vendored as-is in `src/ai_trend_pipeline/web/static/`.

Open `/`, type a topic (or click an example chip, which fills the box) and click Next. Step 2
(`/new/style`) sets the per-post options and shows your saved style; Generate starts the run.
`/?topic=<text>` prefills the topic. This needs `just dev` running. The run
page at `/runs/<id>` updates live (htmx polling) and, once the chart is ready, shows the preview,
caption and critique with Approve and Reject buttons; approving posts to Bluesky, and the
approved run then links to the post on bsky.app. It also links to
the run in the Temporal UI, at `TEMPORAL_UI_URL` (default `http://localhost:8233`).

The header lists Stories first, then Candidates, New chart and Your style, with the brand still
opening `/`. Stories is marked current on `/stories` and its fragments; Candidates is marked current
on the inbox and every candidate page. On a successful first inbox page Candidates also shows a
count badge (`N`, or `N+` when there is another page); older pages and non-inbox pages show no
badge.

`/stories` is the status ledger for everything detected in the last 48 hours. Each row shows the
title, source, one of five status stamps (Pending, Writing, Review, Ready, Closed), an optional
one-line status detail, the updated time and a link to `/stories/<id>` (the detail page arrives in
task 055). The six filter pills reset pagination; `?page=` is an opaque forward cursor that keeps
the filter. It is plain server HTML: no JavaScript is needed to filter, page or open a row. While a
visible row is Pending, Writing or Review the results region refreshes with htmx every 10 seconds;
a Ready/Closed-only, empty or unavailable view stops polling. Invalid page links get a friendly
notice and unavailable pages keep the shared shell.

`/candidates` is the pending-action inbox: a plain server-rendered list of pending candidates
(no JavaScript needed), paged with `?page=` and showing a full-shell notice if the candidate
service is unreachable. It applies the same rolling 48-hour cutoff to its rows, count text, badge
and pages. Each row opens `/candidates/<id>`, a proof sheet with a single Back to
candidates link, the title, source link (only `http`/`https`), summary, proposed angle, selection
reason and source metrics, plus Approve and Reject buttons that 303 back to the inbox with a notice.
Approving only records the story; nothing is published. Missing candidates get a 404 not-available
page and already-decided or expired ones get a 410 page with status-specific copy; both use the same
back link and page title.

`AUTH_MODE` (`local` by default, or `cloudflare`) decides who the web app records as the requester
and approver of a run. `local` uses the fixed user `dev@localhost`. `cloudflare` reads the
`Cf-Access-Authenticated-User-Email` header and answers 401 to POST requests without it. Prod sets
it in `infra/compose.prod.yaml`; the header is trusted only because the web port is bound to
`127.0.0.1` and reached only through the tunnel.

`DAILY_CHART_CAP` (default 10, at least 1) limits how many charts each signed-in person can start from
the web per UTC day; the count resets at 00:00 UTC and includes every started run. The CLI is not capped.

### Your style

`/settings` ("Your style" in the header) lets each signed-in person save a palette, font, caption tone,
up to 3 hashtags and a short signature. Charts they start from the web use it: the tone goes into the
chart prompt, and the palette, font, hashtags and signature are applied in code, so two people get
visibly different charts. People who never saved a style get the same charts as before. Styles are
stored in Postgres: `DATABASE_URL` defaults to the dev Postgres from `just dev`, and the `topic_studio`
schema and its table are created at first use. The CLI needs no database.

### Per-post options

Step 2 of the flow has a "This chart" section with four fields: chart type (line, bar for the
latest value, or area), time range (a preset or From/To years), countries (up to 8, comma separated;
names or 3-letter codes) and free-text instructions. A "Style for this chart" panel below it sets
palette, font, caption tone, hashtags and signature for that chart only. Blank fields fall back to
your saved style and then to the defaults; "Save as my default" also saves the chart's effective
style as yours. Time range presets are relative to the dataset's latest year, not
today's date. Countries that do not match are listed as notes on the run page and never fail the run.
Instructions only steer the caption wording; chart data, colours and years are set in code, and a person
still approves every post.

## Production

`just deploy` runs the full stack with docker compose on the server. Production reads its settings
from `.env.prod` (separate from the dev `.env`) and publishes only `127.0.0.1:18000` (web) and
`127.0.0.1:18233` (Temporal UI). Public access goes through a Cloudflare Tunnel. See
`docs/runbooks/deploy.md` and `docs/runbooks/cloudflare.md`.

## CI

Every pull request and every push to `main` runs `just lint` and `just test` through
`.github/workflows/ci.yml`. A PR is ready to merge only when the `check` job is green.

## Configuration

Environment variables are read only in `ai_trend_pipeline.config` (`load_settings()` returns a
`Settings` model that is passed to the code that needs it). Source polling adds five of them:
`SOURCE_ENABLED` (comma-separated registered source names, default `hn`; `none` disables all),
`SOURCE_POLL_LIMIT` (1-100, default 30), `SOURCE_POLL_INTERVAL_S` (30-3600 seconds, default 300),
`SOURCE_POLL_TIMEOUT_S` (over 0 and at most 60, default 15.0) and `SOURCE_CANDIDATE_BUDGET`
(1-500, default 20: new candidate workflows per source per UTC day; the selector is skipped and no
LLM credit is spent when a source's budget is exhausted). `CANDIDATE_REVIEW_TIMEOUT_S`
(1-604800 seconds, default 172800) sets how long a story candidate waits for an Approve/Reject
decision before it expires.

Story research adds five more: `RESEARCH_FETCH_TIMEOUT_S` (over 0 and at most 60, default 10.0),
`RESEARCH_MAX_RESPONSE_BYTES` (1024-4194304, default 524288), `RESEARCH_MAX_REDIRECTS` (0-5,
default 3), `RESEARCH_USER_AGENT` (stripped 1-200, default `ai-trend-pipeline-research/0.1`) and
`RESEARCH_EXTRA_CONTEXT_HOSTS` (comma-separated exact host names for approved extra context; empty
or `none` disables it). The `research_story` activity runs on the story-generation worker as part of
`StoryGenerationWorkflow`.

## Hacker News

`ai_trend_pipeline.hn` provides the `HNActivities.fetch_hn_top_stories` activity, which fetches
current HN top stories as typed models.

`ai_trend_pipeline.sources` turns source items into normalized, bounded source events through the
`poll_hn_source` activity (`HNSourceActivities`), reusing the existing HN fetch. The registry now
lists two sources: `hn` (`Hacker News`) and `fake` (`Test source`, `poll_fake_source`). The fake
source returns two fixed stories, is wired into the ingestion worker for the pluggability proof,
and is **test-only**: it is off by default, never appears in `.env.prod`, and must never be enabled
in production. Google Trends is the planned next connector once its API/provider access is chosen.

## Story candidates

`ai_trend_pipeline.story_candidate` provides `StoryCandidateWorkflow`, one durable review per
selected story candidate (task queue `story-candidate-task-queue`), with a `review_state` query and
Approve/Reject signals. `services/candidates.py` and the Temporal candidate gateway start
candidates idempotently, list pending ones with pagination and send decisions. The web app lists
pending candidates at `/candidates` and shows and decides one at `/candidates/<id>`;
`/stories` is the unified 48-hour status ledger over the same shared metadata.

Approving a candidate starts one deterministic child `StoryGenerationWorkflow`
(`story-generation-task-queue`) without waiting for it and records its id as `story_workflow_id` in
the candidate query and result. The story runs `researching → analyzing → writing → checking` with at
most one revision, then waits for a final Approve/Reject in `review` and ends `ready`, `rejected`,
`unsupported`, `failed` or `expired`. Both candidate and story review windows are capped at 48 hours
from the candidate's original detection time, and seven shared `Story*` search attributes expose
bounded metadata for the list pages. `ready` only means the package was approved; nothing publishes
or renders.

`ai_trend_pipeline.source_ingestion` provides `SourceIngestionWorkflow`: one long-running workflow
per enabled source on `source-ingestion-task-queue` that polls through the source activity on a
Temporal timer, prefilters new events, asks `select_story_candidates` for a bounded batch, and
starts one candidate workflow per selection. Cursor, processed-story state and the per-source
UTC-day candidate budget live in the workflow (continue-as-new carries them), never in Postgres.
Before calling the selector it checks the budget; at zero it skips the LLM call entirely, leaves the
batch unprocessed, and logs `candidate_ingestion.budget_exhausted` once for that UTC day.

Ingestion is not started by worker startup. Once ensured, the workflow polls continuously on
Temporal timers. Run the idempotent operator command once after the worker is up (and after each
production deploy):

```sh
just ingest   # dev: loads settings, connects, ensures one workflow per enabled source
```

In production the worker container runs the same entry point:

```sh
env -i PATH="$PATH" HOME="$HOME" docker compose -f infra/compose.prod.yaml --env-file .env.prod \
  exec worker python -m ai_trend_pipeline.ingestion_starter
```

Re-running it is safe (`USE_EXISTING`); `SOURCE_ENABLED=none` ensures nothing and exits 0.

### Structured ingestion logs

Operational events are a fixed `candidate_ingestion.*` set: `started`, `poll`, `poll_failed`,
`events_normalized`, `selection`, `selection_failed`, `selection_outside_batch`,
`candidate_started`, `candidate_duplicate`, `budget_exhausted`, `continue_as_new` and `stopped`.
Each record carries `event` and `source` plus primitive counters or ids. A guard rejects titles,
URLs, summaries, angles, reasons, prompts, payloads, credentials and non-primitive values, and
string values are truncated to 256 characters, so source text and secrets never reach the log.
The selector activity emits no success log; the workflow logs the batch and budget context.

Filter worker output with `grep candidate_ingestion`. To see two sources feed one inbox locally, set
`SOURCE_ENABLED=hn,fake` and `SOURCE_CANDIDATE_BUDGET=5` in `.env`, then run `just dev`,
`just ingest` and `just web`, and open `/candidates`. Fake stories are labelled `Test source`; the
fake source is test-only and never belongs in `.env.prod`.

Runbooks: [ingestion](docs/runbooks/ingestion.md) (ensure, enable/disable, recover, replay and the
two-source demo) and [search attributes](docs/runbooks/search-attributes.md) (register and verify the two
`Candidate*` and seven shared `Story*` attributes).

## Topic picking

`ai_trend_pipeline.topic` provides the `TopicActivities.pick_topic` activity, which asks Claude to
pick a chartable topic from HN stories. It chooses one of the titles in `owid.CATALOG`, and an
off-catalog pick is retried. It needs `ANTHROPIC_API_KEY` in the environment.

## Our World in Data

`ai_trend_pipeline.owid` provides the `OwidActivities.fetch_owid_dataset` activity, which maps a
topic to an OWID grapher chart and returns its CSV as a typed table. It only covers the topics in
`CATALOG`; any other topic fails with a non-retryable `NoMatchingDataset` error.

## Charts

`ai_trend_pipeline.chart_spec` provides `ChartSpecActivities.write_chart_spec`, which asks Claude to
pick entities and write a Vega-Lite spec and caption, then fills in the data from the OWID table.
`ai_trend_pipeline.render` provides the `render_chart_png` activity, which renders a spec to a PNG
file with `vl-convert-python` (no browser or Node needed).
`ai_trend_pipeline.critique` provides `CritiqueActivities.critique_chart`, where Claude checks a
chart spec against a compact summary of its data and returns pass or fail with reasons.
`ai_trend_pipeline.chart_loop` provides `draft_chart_with_critique` (workflow code) and
`DraftChartWorkflow`: they draft, critique, and regenerate once with the reasons as feedback.

## Daily chart

`ai_trend_pipeline.daily_chart` provides `DailyChartWorkflow`, which chains the activities above
(HN stories, topic, OWID data, draft and critique, PNG), waits for a human decision, and on
approval posts the chart to Bluesky. The worker needs `ANTHROPIC_API_KEY` (even for `just hello`),
`BLUESKY_HANDLE` and `BLUESKY_APP_PASSWORD` (use an app password from the **test** account), and
optionally `BLUESKY_SERVICE` (default `https://bsky.social`).

Step-by-step run guide:

1. Put the variables above in `.env` (never commit it). The `justfile` has `dotenv-load`, so every
   `just` recipe reads `.env` automatically.
2. `just dev` starts Temporal and the worker. Leave it running.
3. In another terminal, `just chart` starts a run and prints the workflow id
   (`daily-chart-YYYY-MM-DD`). To choose the topic yourself, run
   `just chart --topic "<catalog title>"` (the topic should be one of the `CATALOG` titles in
   `owid.CATALOG`); this skips HN and the topic picker. `--id <id>` sets the workflow id. A topic
   that matches no dataset fails the run.
4. Watch the run in the Temporal UI at http://localhost:8233.
5. When the run waits for a decision, the PNG is under `charts/` (or `CHART_OUTPUT_DIR`). Open it.
6. `just show <id>` prints the caption, PNG path and critique result.
7. Decide with the CLI (the Temporal UI has no approve button):
   `just approve <id>` or `just reject <id>`.
8. Approving posts the chart and caption to Bluesky once. The CLI prints `posted: <uri>`. Running
   `approve` again returns 1 because the run is no longer awaiting a decision, and a retried
   post is detected and not duplicated. Rejecting posts nothing.

If nobody decides within 20 hours the run ends as `expired` and nothing is posted.

## Idea board
1. Price radar as a plugin ( compare supermarket prices )
2. Event finder plugin
