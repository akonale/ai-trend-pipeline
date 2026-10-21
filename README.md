# ai-trend-pipeline

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

Open http://localhost:8233 to see `hello-workflow` as Completed. On a remote dev server, tunnel
the UI port first: `ssh -L 8233:localhost:8233 <host>`.

## CI

Every pull request and every push to `main` runs `just lint` and `just test` through
`.github/workflows/ci.yml`. A PR is ready to merge only when the `check` job is green.

## Hacker News

`ai_trend_pipeline.hn` provides the `HNActivities.fetch_hn_top_stories` activity, which fetches
current HN top stories as typed models. It is not registered in the worker yet.

## Topic picking

`ai_trend_pipeline.topic` provides the `TopicActivities.pick_topic` activity, which asks Claude to
pick a chartable topic from HN stories. It needs `ANTHROPIC_API_KEY` in the environment.

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
