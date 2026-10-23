# 010: Workflow: wire 005–009 together + approval signal + CLI to approve/reject

Status: reviewed

## Goal

Add `DailyChartWorkflow`. It runs the task 005–009 activities in order (HN stories, topic, OWID table, draft + critique, render PNG), then pauses until a human approves or rejects the chart through a Temporal signal. Register it and all of its activities in the worker, and add a small CLI to start a run, show the pending chart, and approve or reject it.

## Design (read first)

- **One workflow, inline helper.** `DailyChartWorkflow.run` calls `draft_chart_with_critique` (task 009) inline, not as a child workflow. All I/O stays in activities. The workflow code only makes `workflow.execute_activity*` calls, branches, and waits with `workflow.wait_condition`. No clock, randomness or env reads.
- **Pause and decide.** After the render, the workflow stores a `ReviewRequest` and waits on `workflow.wait_condition(lambda: self._decision is not None, timeout=APPROVAL_TIMEOUT)`. A `decide` signal sets the decision. A `review_state` query returns the current state so the CLI can show what is being approved. If nobody decides before the timeout, the run ends with status `expired` rather than failing. It does not raise.
- **Signal rules.** The first decision received while the state is `awaiting_decision` wins. A decision that arrives before the review is ready (`drafting`), or after one has already been recorded, is ignored and logged with `workflow.logger.warning`. The CLI also refuses to send a decision unless the query says `awaiting_decision`. Because the handler only accepts one decision, signalling twice is safe.
- **No posting.** An approved run just returns `status="approved"`. Posting to Bluesky is task 011, which will add it after the approval branch.
- **Output dir.** `DailyChartInput.output_dir` (default `"charts"`) goes straight to `RenderChartInput`. The CLI fills it from the `CHART_OUTPUT_DIR` env var when it is set. The workflow itself never reads env vars. `charts/` is added to `.gitignore`.
- **Failures.** A non-retryable activity error (for example `NoMatchingDataset` from OWID, or a second `InvalidChartDraft`) fails the workflow. This task adds no fallback topic logic.
- **CLI testability.** `cli.py` exposes `async def run_cli(argv: list[str], client: Client) -> int`. `main()` only connects with `temporal_client.connect()` and calls it. Tests pass the time-skipping env client.

## Acceptance criteria

- [x] **`src/ai_trend_pipeline/daily_chart.py`** (new). Imports of pydantic and the activity modules go inside `workflow.unsafe.imports_passed_through()`, as in `chart_loop.py`. It defines:
  - Constants: `DAILY_CHART_TASK_QUEUE = "daily-chart-task-queue"`, `APPROVAL_TIMEOUT = timedelta(hours=20)`, `IO_ACTIVITY_TIMEOUT = timedelta(seconds=60)`, `IO_RETRY_POLICY = RetryPolicy(maximum_attempts=3)`. HN, OWID and render use the IO pair. `pick_topic` uses `LLM_ACTIVITY_TIMEOUT` / `LLM_RETRY_POLICY` from `chart_loop.py`.
  - Models: `DailyChartInput(story_limit: int = Field(default=30, ge=1, le=100), since_year: int = Field(default=2000, ge=1800, le=2100), output_dir: str = Field(default="charts", min_length=1))`. `ReviewRequest(topic: str, topic_reason: str, caption: str, png_path: str, spec_sha256: str, critique: ChartCritique, attempts: int)`. `ApprovalDecision(approved: bool, note: str = Field(default="", max_length=500))`. `ReviewState(status: Literal["drafting", "awaiting_decision", "approved", "rejected", "expired"], review: ReviewRequest | None = None, note: str = "")`. `DailyChartResult(status: Literal["approved", "rejected", "expired"], review: ReviewRequest, note: str = "")`.
  - `@workflow.defn class DailyChartWorkflow` with:
    - `@workflow.run async def run(self, data: DailyChartInput) -> DailyChartResult`, in this order: `HNActivities.fetch_hn_top_stories(HNTopStoriesInput(limit=story_limit))`, then `TopicActivities.pick_topic(PickTopicInput(stories=...))`, then `OwidActivities.fetch_owid_dataset(FetchOwidInput(topic=..., since_year=...))`, then `draft_chart_with_critique(WriteChartSpecInput(topic=..., table=...))`, then `render_chart_png(RenderChartInput(vega_lite_spec=chart.vega_lite_spec, output_dir=data.output_dir))`. It then sets the state to `awaiting_decision` with the `ReviewRequest` and waits. On a decision it returns `approved` or `rejected` with the note. On `asyncio.TimeoutError` from `wait_condition` it returns `expired`.
    - `@workflow.signal def decide(self, decision: ApprovalDecision) -> None`, following the signal rules above.
    - `@workflow.query def review_state(self) -> ReviewState`.
- [x] **`src/ai_trend_pipeline/worker.py`** (edit). It adds `def daily_chart_activities(anthropic_client, hn_client, owid_client) -> list[Callable[..., Any]]`, with a comment explaining that `Any` matches Temporal's `Worker(activities=...)` type. The function returns the six bound methods/functions `fetch_hn_top_stories`, `pick_topic`, `fetch_owid_dataset`, `write_chart_spec`, `critique_chart` and `render_chart_png`, built from `HNActivities`, `TopicActivities`, `OwidActivities`, `ChartSpecActivities`, `CritiqueActivities` and `render_chart_png`. `main()` builds the clients with `make_anthropic_client()`, `make_hn_client()` and `make_owid_client()`, closing the httpx clients on exit with `async with`. It then runs the existing hello `Worker` and a second `Worker` on `DAILY_CHART_TASK_QUEUE` (with `workflows=[DailyChartWorkflow]`) concurrently with `asyncio.gather`. `connect_with_retry` is unchanged.
- [x] **`src/ai_trend_pipeline/cli.py`** (new), run as `python -m ai_trend_pipeline.cli`. It uses argparse subcommands, and every command returns an exit code:
  - `start [--id ID]` starts `DailyChartWorkflow` on `DAILY_CHART_TASK_QUEUE` without waiting, and prints the workflow id. The default id is `daily-chart-YYYY-MM-DD` (UTC date; the CLI is not workflow code, so it may read the clock). `output_dir` comes from `CHART_OUTPUT_DIR` if that is set. If a run with that id already exists (`WorkflowAlreadyStartedError`), it prints a message and returns 1.
  - `show ID` queries `review_state` and prints the status. If a review exists, it also prints the topic, topic reason, caption, PNG path, `attempts` and `critique.passed`. When the critique failed, it prints each critique reason on its own line. Returns 0.
  - `approve ID [--note TEXT]` / `reject ID [--note TEXT]`: if the state is not `awaiting_decision`, it prints the current status and returns 1. Otherwise it sends `decide`, awaits the workflow result, prints the final status, and returns 0.
  - If the workflow id is unknown (`RPCError` with status `NOT_FOUND`), every command prints an error and returns 1.
  - `main()` runs `sys.exit(asyncio.run(...))` using `connect()`.
- [x] **Done-when check.** In the time-skipping env with fake activities, a test starts the workflow, polls `review_state` until it is `awaiting_decision`, and asserts `handle.describe()` reports `RUNNING`. It then calls `run_cli(["approve", wf_id], env.client)`, which returns 0, and the workflow result is `status == "approved"`.
- [x] `justfile` gets the recipes `chart` (runs `cli start`), `show id`, `approve id` and `reject id`, all of which run `uv run python -m ai_trend_pipeline.cli ...`. `.gitignore` gets `charts/`. `README.md` gets a short "Daily chart" section: the worker now needs `ANTHROPIC_API_KEY`, then `just chart`, `just show <id>` and `just approve <id>` / `just reject <id>`, plus `CHART_OUTPUT_DIR` and the 20h expiry. The "not registered in the worker yet" line under Hacker News is removed.
- [x] `just lint` and `just test` pass. No live LLM or HTTP calls. Workflow tests use `WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)`.

## Files to touch

- `src/ai_trend_pipeline/daily_chart.py` (new)
- `src/ai_trend_pipeline/cli.py` (new)
- `src/ai_trend_pipeline/worker.py` (edit)
- `tests/test_daily_chart.py` (new)
- `tests/test_cli.py` (new)
- `tests/test_worker.py` (new)
- `justfile`
- `.gitignore`
- `README.md`
- `CLAUDE.md` (human-approved in fix loop 1: add the `just chart` row to the commands table)

## Tests to write

Fake activities live on a small stateful class with `@activity.defn(name=...)` methods using the exact names `fetch_hn_top_stories`, `pick_topic`, `fetch_owid_dataset`, `write_chart_spec`, `critique_chart` and `render_chart_png`. They record their inputs and return scripted outputs. The render fake returns a fixed path and writes no file. Put the fakes and a `wait_for_state(handle, status)` polling helper (real `asyncio.sleep(0.05)` steps, at most 10 s) in `tests/test_daily_chart.py`, and import them in `tests/test_cli.py`. If an import needs a pyright suppression, explain it in a comment.

`tests/test_daily_chart.py`:
- `test_activities_chained_in_order`: each fake receives the previous step's output (stories go to pick_topic, the topic goes to OWID and write, `output_dir` and the chart spec go to render). The call order matches the design.
- `test_pauses_until_decision`: after the state reaches `awaiting_decision`, `describe()` is `RUNNING` and the review holds the topic, caption, PNG path, `spec_sha256`, critique and attempts from the fakes.
- `test_approve_signal_completes_approved`: the `decide(approved=True, note="ok")` result is `approved` with the note `ok`.
- `test_reject_signal_completes_rejected`: the result is `rejected`.
- `test_second_decision_is_ignored`: approve, then reject, sent back to back. The result is `approved`.
- `test_no_decision_expires`: awaiting the result without a signal (time skips past 20h) returns `expired`.
- `test_failed_critique_is_shown_in_review`: the critique fake fails twice, so the review has `attempts == 2`, `critique.passed is False` and the reasons. The workflow still waits for a decision.
- `test_non_retryable_activity_error_fails_workflow`: the OWID fake raises a non-retryable `ApplicationError(type="NoMatchingDataset")`, so `result()` raises `WorkflowFailureError` and render is never called.

`tests/test_cli.py` (time-skipping env client and the same fakes; use `capsys` for output):
- `test_cli_approve_done_when`: the done-when check above.
- `test_cli_reject`: `reject ID --note bad` returns 0, and the result is `rejected` with the note `bad`.
- `test_cli_start_uses_id_and_output_dir`: with `CHART_OUTPUT_DIR` set through monkeypatch, `start --id X` returns 0 and prints `X`. The render fake receives that output dir.
- `test_cli_start_duplicate_id_returns_1`.
- `test_cli_show_prints_review`: the output has the status, caption, PNG path and (for a failed critique) each reason.
- `test_cli_approve_when_not_awaiting_returns_1`: a run that was already approved is approved again, which returns 1 and sends no second decision.
- `test_cli_unknown_id_returns_1`: `show` and `approve` on a missing id both return 1.

`tests/test_worker.py`:
- `test_daily_chart_activities_names`: with dummy clients (`anthropic.AsyncAnthropic(api_key="test")` and plain `httpx.AsyncClient()`, never used for requests), the `__name__`s of the returned callables are exactly the six activity names.

## Out of scope

- Posting to Bluesky or any posting idempotency (011), Temporal Schedules or cron (012), and feedback, metrics, Postgres writes or prompt versioning (013).
- Fallback topics when OWID has no match, re-picking a topic, or passing the OWID catalog to the topic picker.
- Changes to `hn.py`, `topic.py`, `owid.py`, `chart_spec.py`, `critique.py`, `chart_loop.py`, `render.py`, `hello.py`, `start_hello.py`, `temporal_client.py`, `docs/mvp-plan.md`, `infra/` or CI. No new dependencies (argparse is stdlib).
- Showing or opening the PNG image in the CLI, a web UI, or editing the caption during approval.
- An end-to-end test with the real activity classes and mock transports (the 009 test covers the draft loop). Moving `_fixture` to `tests/helpers.py`.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->
**planner**: Wrote the 010 spec and set Status to `planned`. Design: one `DailyChartWorkflow` calls the 009 helper inline, then waits on `decide` with a 20h timeout (ending `expired`), and a `review_state` query backs the CLI. `run_cli(argv, client)` keeps the CLI testable against the time-skipping env. Posting is deliberately left for 011.
**planner**: Risk: the worker now needs `ANTHROPIC_API_KEY` even for `just hello`. That is documented in the README and accepted. Real-world risk: `pick_topic` may choose topics outside the OWID `CATALOG`, which fails the run. Fixing that is a follow-up, not part of 010.
**planner**: If this is too big, split it into 010a (`daily_chart.py` + worker + `test_daily_chart.py` + `test_worker.py`) and 010b (`cli.py` + justfile + README + `test_cli.py`). This is a `yes` task, so the human approves the spec. Next: the builder uses branch `task/010-wiring`.
**builder**: Built on `task/010-wiring`; new tests pass and ruff and pyright are clean. Next: verifier.
**builder**: SPEC CONFLICT: the new `chart` recipe makes `tests/test_claude_md.py::test_claude_md_commands_match_justfile` fail, because it needs `just chart` in CLAUDE.md, which the spec puts out of scope. I did not edit CLAUDE.md. Human decision needed (add the row, or rename the recipe).
**builder**: Fix loop 1: added the `just chart` row to the CLAUDE.md commands table (human-approved scope change; CLAUDE.md added to "Files to touch" and removed from the out-of-scope list). Next: verifier.
**verifier**: `just lint` passed (ruff check, ruff format --check, pyright 0 errors). `just test` passed (87 tests, 0 failures).
**verifier**: Confirmed all named tests, the chart/show/approve/reject recipes, the `charts/` gitignore entry and the README Daily chart section exist. Ticked the done-when and lint/test boxes only; code-review boxes left for the reviewer.
**reviewer**: Approved. All acceptance boxes hold; diff is in scope (CLAUDE.md row approved in fix loop 1), workflow code is deterministic, models are Pydantic.
Note: no `ANTHROPIC_API_KEY` is checked until worker start; accepted per spec. Next: scribe.
