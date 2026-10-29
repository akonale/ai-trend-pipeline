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
caption and critique with Approve and Reject buttons; approving posts to Bluesky. It also links to
the run in the Temporal UI, at `TEMPORAL_UI_URL` (default `http://localhost:8233`).

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

Step 2 of the flow has a "This chart" section with five fields: chart type (line, bar for the
latest value, or area), time range (a preset or From/To years), countries (up to 8, comma separated;
names or 3-letter codes), caption tone and free-text instructions. Blank options fall back to your
saved style and then to the defaults. Time range presets are relative to the dataset's latest year, not
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
`Settings` model that is passed to the code that needs it).

## Hacker News

`ai_trend_pipeline.hn` provides the `HNActivities.fetch_hn_top_stories` activity, which fetches
current HN top stories as typed models.

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
