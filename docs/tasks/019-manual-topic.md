# 019: Optional manual topic override for the daily chart

Status: built

Depends on: 018 (merged first, so that catalog titles always match in `match_dataset`).

## Goal

Let a human start a daily chart run with a chosen topic: `just chart --topic "Life expectancy"`. When a topic is given, the workflow skips fetching HN and `pick_topic` and goes straight to `fetch_owid_dataset` with that topic. Without one, it behaves as it does today.

## Acceptance criteria

- [ ] `DailyChartInput` gets `topic: str | None = Field(default=None, min_length=1)`. The default `DailyChartInput()` is unchanged in behaviour.
- [ ] When `data.topic` is set, `DailyChartWorkflow.run` calls neither `fetch_hn_top_stories` nor `pick_topic`. It calls `fetch_owid_dataset` with `FetchOwidInput(topic=data.topic, since_year=data.since_year)`, and the rest of the flow (draft, critique, render, approval, post) is unchanged. `ReviewRequest.topic` is the given topic, and `topic_reason` is the module constant `MANUAL_TOPIC_REASON = "Manually chosen"`. The branch depends only on the input, so it stays deterministic.
- [ ] A manual topic that matches no dataset fails the workflow with the existing non-retryable `NoMatchingDataset` error from `fetch_owid_dataset`. There is no new error path and no pre-check in the CLI.
- [ ] `cli start` accepts `--topic TEXT` (default none) and passes it into `DailyChartInput`, together with the existing `CHART_OUTPUT_DIR` handling and `--id`.
- [ ] The `justfile` recipe becomes `chart *args` and passes the args through to `cli start` with their quoting intact, so `just chart --topic "Life expectancy" --id x` works. Use the recipe attribute `[positional-arguments]` with `"$@"`, not `{{args}}`, because `{{args}}` splits a multi-word topic.
- [ ] The README "Daily chart" section documents `just chart --topic "<catalog title>"` and `--id`, and says the topic should be one of the `CATALOG` titles. `just lint` and `just test` pass with no live calls.

## Files to touch

- `src/ai_trend_pipeline/daily_chart.py`
- `src/ai_trend_pipeline/cli.py`
- `justfile` (`chart` recipe only)
- `tests/test_daily_chart.py`, `tests/test_cli.py` (edit; extend the existing fakes and helpers as needed)
- `README.md` (Daily chart section)

`CLAUDE.md` (`just chart` row only; human-approved addition).

## Tests to write

- `test_manual_topic_skips_hn_and_pick` (test_daily_chart): with `DailyChartInput(topic=TOPIC)`, `fakes.calls` contains neither `fetch_hn_top_stories` nor `pick_topic`, and `fakes.owid_inputs[0] == FetchOwidInput(topic=TOPIC, since_year=...)`.
- `test_manual_topic_review_reason` (test_daily_chart): the queried review has `topic == TOPIC` and `topic_reason == MANUAL_TOPIC_REASON`.
- `test_manual_topic_no_match_fails` (test_daily_chart): with `owid_fails=True` and a manual topic, `handle.result()` raises `WorkflowFailureError`, `pick_topic` is never called, and nothing is rendered.
- `test_cli_start_with_topic` (test_cli): `run_cli(["start", "--id", wf_id, "--topic", "Life expectancy"], env.client)` returns 0, and the run's review topic is "Life expectancy" with no `pick_topic` call.
- The existing `test_activities_chained_in_order` still passes, which shows the run without a topic is unchanged.

## Out of scope

- Validating the topic against `CATALOG` in the CLI or workflow, or listing the catalog from the CLI.
- Growing `CATALOG`, or changing `topic.py`, `owid.py`, the worker, schedules or `CLAUDE.md`.
- Changing other `justfile` recipes.
- Live Temporal-server, Anthropic, OWID or Bluesky calls in tests (use the time-skipping env and fakes).

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->
**planner**: Wrote the spec and set it to `planned`. Approval is `no`, and the Fixes row is added. Branch: `task/019-manual-topic`, created from `main` only after 018 is merged.
**planner**: Gotcha: `{{args}}` in just re-splits "Life expectancy", so use `[positional-arguments]` with `"$@"`. That needs just 1.29 or later, so the builder should check `just --version`. The CLI test covers argparse whatever the just version.
**planner**: CLAUDE.md has no `just chart` row, so it is left out. Next: orchestrator approves, then the builder.
**builder**: Implemented manual topic (workflow, CLI `--topic`, `chart *args` with positional-arguments, README). Tests added first.
**builder**: Touched CLAUDE.md `just chart` row (human-approved) and the spec's Files list. Next: verifier.
