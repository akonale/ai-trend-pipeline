# 009: Activity: LLM critique, one regenerate on failure

Status: reviewed

## Goal

Add a `critique_chart` activity in which Claude, through a forced tool call, checks a `ChartSpec` (task 008) against a compact summary of its data and returns pass or fail with reasons. Add a deterministic workflow helper, `draft_chart_with_critique`, that calls `write_chart_spec` and then `critique_chart`, and regenerates the chart exactly once (passing the critique's reasons back) if the first draft fails. A minimal `DraftChartWorkflow` wraps the helper so the loop can be tested now in Temporal's time-skipping environment. Task 010 then calls the same helper from the main workflow.

## Design (read first)

- **Where the loop lives.** "Regenerate once" is orchestration, so it goes in workflow code, not in an activity. An activity that called the LLM twice would not be idempotent, its retries would be hidden from Temporal history, and a worker crash would repeat both calls. The helper is a plain `async def` that runs only inside a workflow. It contains only `workflow.execute_activity` calls and pure branching: no I/O, clock or randomness.
- **Feedback.** The regenerate is useful only if the second draft knows why the first one failed. `WriteChartSpecInput` gets an optional `feedback: list[str]`, which `write_chart_spec` appends to its user message. This is the only change to `chart_spec.py`.
- **What counts as a failure on attempt 1.** Either (a) the critique returns `passed=False`, or (b) `write_chart_spec` raises the non-retryable `InvalidChartDraft` from task 008. Both lead to exactly one regenerate. There are never more than 2 `write_chart_spec` calls and 2 `critique_chart` calls.
- **After attempt 2.** If the second critique also fails, the helper does **not** raise. It returns the second draft with `critique.passed=False` and `attempts=2`. A human approves every chart (task 010), so the reasons are shown to them rather than the day's run being thrown away. If the second `write_chart_spec` raises `InvalidChartDraft`, the error propagates and the workflow fails.
- **Temporal retries and regenerates are separate.** Temporal retries cover transient errors (API 5xx, timeouts): `RetryPolicy(maximum_attempts=3)` on both LLM activities. Content failures are non-retryable `ApplicationError`s or `passed=False` results, so Temporal never retries them; only the helper's single regenerate does.
- **What the critic sees.** Not the inline `data.values`, which can be large. It sees the spec with top-level `data`/`datasets` removed, the caption, the entities, the topic, table title and value column, and one summary line per chosen entity: first year and value, last year and value, min, max and row count. That is enough to catch a caption that contradicts the data, a wrong encoding or a misleading title.

## Acceptance criteria

- [x] **`src/ai_trend_pipeline/chart_spec.py`** (edit, minimal): `WriteChartSpecInput` gains `feedback: list[str] = Field(default_factory=list)`. When it is non-empty, the user message ends with `"\nA previous draft was rejected for these reasons:\n"` followed by one `- <reason>` line per item. When it is empty, the request is byte-for-byte what it is today (all existing task 008 tests pass unchanged). Nothing else in the file changes.
- [x] **`src/ai_trend_pipeline/critique.py`** (new) defines:
  - Constants: `MAX_TOKENS = 1024`, `TOOL_NAME = "critique_chart"`, `INVALID_CRITIQUE_ERROR_TYPE = "InvalidChartCritique"`, `MAX_REASONS = 5`, and a short `SYSTEM_PROMPT`. The prompt tells the model to check that the caption is factually supported by the summary, that the encoding (entity nominal colour, year x, value y) and the title fit the topic, and that the chart is not misleading. It fails the chart only for concrete problems, giving one short reason per problem.
  - Models: `CritiqueChartInput(topic: str = Field(min_length=1), table: OwidTable, chart: ChartSpec)` and `ChartCritique(passed: bool, reasons: list[str] = Field(default_factory=list, max_length=MAX_REASONS))`, with a `model_validator(mode="after")` that rejects `passed=False` with empty `reasons`. `ChartCritique.model_json_schema()` is the tool input schema.
  - `class CritiqueActivities: __init__(self, client: anthropic.AsyncAnthropic, model: str = DEFAULT_MODEL)` (reusing `DEFAULT_MODEL` from `topic.py`), with `@activity.defn async def critique_chart(self, data: CritiqueChartInput) -> ChartCritique`. It makes exactly one `messages.create` call with `tools=[tool]` and `tool_choice={"type": "tool", "name": TOOL_NAME}`. The user message contains: the topic, `table.title`, `table.value_column`, `chart.caption`, `chart.entities`, the spec as JSON with top-level `data`/`datasets` removed, and the per-entity summary lines described above. These lines are built only from `table.rows` whose entity is in `chart.entities`, in `chart.entities` order. The activity finds the `ToolUseBlock` named `TOOL_NAME`; if there is none it raises a non-retryable `ApplicationError`. A `ValidationError` becomes `ApplicationError(..., type=INVALID_CRITIQUE_ERROR_TYPE, non_retryable=True)`.
- [x] **`src/ai_trend_pipeline/chart_loop.py`** (new). Its imports of `chart_spec`, `critique` and pydantic go inside `workflow.unsafe.imports_passed_through()`, as in `hello.py`. It defines:
  - `LLM_ACTIVITY_TIMEOUT = timedelta(seconds=120)`, `LLM_RETRY_POLICY = RetryPolicy(maximum_attempts=3)`, `MAX_DRAFTS = 2`.
  - Model `CritiquedChart(chart: ChartSpec, critique: ChartCritique, attempts: int = Field(ge=1, le=MAX_DRAFTS))`.
  - `async def draft_chart_with_critique(data: WriteChartSpecInput) -> CritiquedChart`. Only workflow code calls it. It calls `ChartSpecActivities.write_chart_spec` and `CritiqueActivities.critique_chart` through `workflow.execute_activity_method` (activity names `write_chart_spec` and `critique_chart`), with the timeout and retry policy above. The flow:
    1. Draft 1. If it raises `ActivityError` whose `cause` is an `ApplicationError` with `type == INVALID_DRAFT_ERROR_TYPE`, it regenerates with `feedback=[cause.message]`. Any other error propagates.
    2. Otherwise it critiques draft 1. If `passed`, it returns with `attempts=1`.
    3. Otherwise it regenerates once with `feedback=critique.reasons`, critiques draft 2, and returns that draft and critique with `attempts=2`, whether or not draft 2 passed.
    The regenerate input is `data.model_copy(update={"feedback": ...})`.
  - `@workflow.defn class DraftChartWorkflow` with `@workflow.run async def run(self, data: WriteChartSpecInput) -> CritiquedChart`, which only returns `await draft_chart_with_critique(data)`.
- [x] **Done-when check.** In the time-skipping environment, a test runs `DraftChartWorkflow` with the **real** `ChartSpecActivities` and `CritiqueActivities`, each backed by an `httpx2.MockTransport`. The critique transport serves `llm_critique_fail.json` first and `llm_critique_pass.json` second. The run makes exactly 2 chart requests and 2 critique requests, the second chart request's user message contains the fail fixture's reason text, and the result has `attempts == 2` and `critique.passed is True`.
- [x] `just lint` and `just test` pass. No live LLM or HTTP calls. Every mock handler fails the test on any request other than `POST /v1/messages`. Workflow tests use `WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)` as in `tests/test_hello.py`.
- [x] `README.md` gets two or three lines on the `critique_chart` activity (`ai_trend_pipeline.critique`) and the one-regenerate helper and workflow (`ai_trend_pipeline.chart_loop`).

## Files to touch

- `src/ai_trend_pipeline/chart_spec.py` (edit: `feedback` field and message suffix only)
- `src/ai_trend_pipeline/critique.py` (new)
- `src/ai_trend_pipeline/chart_loop.py` (new)
- `tests/test_critique.py` (new)
- `tests/test_chart_loop.py` (new)
- `tests/test_chart_spec.py` (edit: add one feedback test only)
- `tests/fixtures/llm_critique_pass.json` (new). A Messages API response in the same shape as `llm_chart_spec_response.json`: a text block, then a `tool_use` block named `critique_chart` with input `{"passed": true, "reasons": []}`.
- `tests/fixtures/llm_critique_fail.json` (new). The same shape, with input `{"passed": false, "reasons": ["Caption says Japan's life expectancy fell, but the data shows it rose."]}`.
- `README.md`

## Tests to write

`tests/test_critique.py`. These tests use `ActivityEnvironment`, a `ChartSpec` built from the task 008 chart fixture plus `make_table()`, and a mock client in the same style as `make_client` (importing from `tests.test_chart_spec` is fine, as `test_render.py` does).
- `test_critique_pass_fixture`: returns `ChartCritique(passed=True, reasons=[])`.
- `test_critique_fail_fixture`: returns `passed is False` and the fixture's single reason.
- `test_request_shape`: one request, with the model, `MAX_TOKENS`, tool names `== ["critique_chart"]` and the forced `tool_choice`. The user message contains the topic, the caption, `life_expectancy_0` and each chosen entity. It contains no `"values"` key from the inline data, and does not mention the unchosen entity `Afghanistan`.
- `test_summary_lines`: the message includes, for `France`, its first and last year, the values for those years, and its min and max (from `make_table()` data).
- `test_custom_model_is_sent`: `CritiqueActivities(client, model="other-model")` sends `"other-model"`.
- `test_fail_without_reasons_is_non_retryable`: tool input `{"passed": false, "reasons": []}` raises `ApplicationError` with `type == INVALID_CRITIQUE_ERROR_TYPE` and `non_retryable is True`.
- `test_no_tool_use_raises_non_retryable`: `llm_pick_topic_no_tool.json` raises a non-retryable `ApplicationError`.
- `test_api_error_propagates`: a 500 response raises `anthropic.APIStatusError`.

`tests/test_chart_spec.py` (one addition):
- `test_feedback_is_appended_to_request`: with `feedback=["too many lines"]`, the user message ends with the rejection header and `- too many lines`. The existing `test_request_shape` still proves that an empty feedback list changes nothing.

`tests/test_chart_loop.py`. Fake-activity tests register `@activity.defn(name="write_chart_spec")` and `@activity.defn(name="critique_chart")` fakes on a small stateful class that records every input and returns scripted outputs. Each test runs `DraftChartWorkflow` in the time-skipping environment.
- `test_pass_first_time_no_regenerate`: 1 write and 1 critique, `attempts == 1`, and the write input has empty `feedback`.
- `test_bad_draft_triggers_exactly_one_regenerate`: critique fails then passes. 2 writes and 2 critiques, the second write's `feedback` equals the first critique's `reasons`, `attempts == 2`, and the result holds the second draft.
- `test_two_failures_return_failed_critique_without_third_draft`: critique fails both times. Exactly 2 writes and 2 critiques, `attempts == 2`, `critique.passed is False`.
- `test_invalid_draft_triggers_one_regenerate`: the first write raises `ApplicationError(type=INVALID_DRAFT_ERROR_TYPE, non_retryable=True)`. The second write's `feedback` contains that error message, there is 1 critique, and `attempts == 2`.
- `test_invalid_draft_twice_fails_workflow`: both writes raise `InvalidChartDraft`. `execute_workflow` raises `WorkflowFailureError`, there are exactly 2 writes and 0 critiques.
- `test_fixture_bad_critique_triggers_one_retry_end_to_end`: the done-when check above, using real activity classes and mock transports.

## Out of scope

- Registering activities or workflows in `worker.py`, building the real Anthropic client in the worker, task queues, the main pipeline workflow, rendering after critique, the approval signal, and the CLI. All of this is task 010.
- Showing critique reasons to the approver, which is task 010's job (it gets them from `CritiquedChart.critique`).
- More than one regenerate, configurable retry counts, scoring, a vision-based critique of the rendered PNG, and prompt versioning (task 013).
- Changes to `topic.py`, `owid.py`, `hn.py`, `render.py`, `hello.py`, `worker.py`, `CLAUDE.md`, `docs/mvp-plan.md`, `justfile`, `infra/` or CI. No new dependencies.
- Moving the test helpers out of `tests/test_chart_spec.py` (reviewer note from 008); leave that for a later cleanup.
- Any live LLM call or fixture-recording script.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->
**planner**: Wrote the 009 spec and set Status to `planned`. The loop runs in workflow code (`draft_chart_with_critique` in `chart_loop.py`), not in an activity, so each LLM call is its own idempotent activity in history. `DraftChartWorkflow` is a thin wrapper so this can be tested now in time-skipping. The only edit to 008 code is the optional `feedback` field. If the builder finds it too large, split it into 009a (`critique.py` + fixtures + `test_critique.py`) and 009b (feedback field + `chart_loop.py` + `test_chart_loop.py`).
**planner**: Left for 010: register `write_chart_spec`, `critique_chart` (and `DraftChartWorkflow` if used as a child) in `worker.py` with a real client; call `draft_chart_with_critique` inline from the main workflow (recommended over a child workflow), then `render_chart_png`; show `critique.reasons` and `attempts` in the approval CLI when `passed` is False.
**planner**: Things to check while building: fake activities must use the exact names `write_chart_spec`/`critique_chart`; `ActivityError.cause` is the `ApplicationError` carrying `.type`; and if the sandbox rejects importing `anthropic` via `chart_loop.py`, keep those imports inside `imports_passed_through()`. Next: the orchestrator sets `approved`; the builder uses branch `task/009-chart-critique`.
**builder**: Implemented all of 009 (no split needed): feedback field, `critique.py`, `chart_loop.py`, two fixtures, tests, README. `just lint` and `just test` pass (71 tests).
**builder**: Unsure: tests import the private `_fixture` from `tests.test_chart_spec` with a pyright ignore comment; the loop uses try/except/else so draft 2 is the only extra write. Next: verifier.
**verifier**: Ran `just lint` (ruff check, ruff format --check, pyright: clean) and `just test` (71 passed). All checks pass.
**reviewer**: Needs-fix, one item. Temporal check passed: `chart_loop.py` has only activity calls and pure branching, its imports are in `imports_passed_through()`, and the loop makes at most 2 writes and 2 critiques. Temporal's RetryPolicy covers transient errors only. The regenerate is triggered by the `InvalidChartDraft` ActivityError or by `passed=False`. The critic gets no inline data or unchosen entities. No secrets, and the tests use mock transports that reject any request other than `POST /v1/messages`.
1. `tests/test_critique.py:20` and `tests/test_chart_loop.py:26`: `# pyright: ignore[reportPrivateUsage]` on the `_fixture` import has no explanation, and CLAUDE.md allows suppressions only with a comment that explains them (checklist item 7). The spec puts moving the helpers out of scope, so do not move them. Add a one-line comment above each import saying why (for example "private test helper shared across test modules; move to tests/helpers.py later"). Split the import over lines if needed to satisfy the line-length limit. Re-run `just lint`.
Notes: the verifier's uncommitted edit to this file needs committing with the fix.
**builder**: Added explanatory comments above the `_fixture` pyright-ignore imports in both test files (imports split over lines). `just lint` clean, `just test` 71 passed. Next: verifier.
**verifier**: Re-verified after fix commit 46d27ee. `just lint` clean (ruff check, ruff format --check, pyright 0 errors). `just test` 71 passed. All checks pass.
**reviewer**: Approved. Commit 46d27ee only adds explanatory comments above the `_fixture` pyright-ignore imports in tests/test_critique.py and tests/test_chart_loop.py (imports split across lines), and logs the handoff in this file. Nothing else changed. Note: commit the verifier and reviewer edits to this task file. Note: later move `_fixture` to tests/helpers.py.
