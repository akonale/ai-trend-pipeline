# 008: Activity: LLM writes a Vega-Lite spec + caption; render to PNG

Status: reviewed

## Goal

Add two Temporal activities. `write_chart_spec` asks Claude, through a forced tool call, to pick up to 8 entities from the task 007 `OwidTable` and write a Vega-Lite spec and a caption. The activity then puts the table rows into the spec itself. `render_chart_png` turns that spec into a PNG file on disk with `vl-convert-python`. Keeping them as two activities means rendering needs no LLM, and task 009 can re-run only the LLM step.

## Acceptance criteria

- [x] **Dependency.** Add `vl-convert-python` with `uv add vl-convert-python`, which updates `pyproject.toml` and `uv.lock`. No other new dependencies. Why this package: it is a self-contained Rust and V8 wheel (manylinux x86_64 and aarch64, abi3, so it works on Python 3.12). It needs no browser, Node, Cairo or system packages, so it works unchanged on a headless server and on `ubuntu-latest` in CI with `uv sync --locked`. Altair is not needed. Other options were rejected: Node `vl2png` needs Node plus native canvas libraries, and Selenium or headless Chrome is heavy and flaky. If `vl_convert` has no type stubs and Pyright strict complains, add a minimal `typings/vl_convert.pyi` (Pyright's default `stubPath`) that declares only `vegalite_to_png`. Do not use `# type: ignore`.
- [x] **`src/ai_trend_pipeline/chart_spec.py`** (new) defines:
  - Constants: `MAX_TOKENS = 4096`, `TOOL_NAME = "write_chart"`, `INVALID_DRAFT_ERROR_TYPE = "InvalidChartDraft"`, `SAMPLE_ROWS = 20`, `MAX_ENTITIES = 8`, `MAX_CAPTION_CHARS = 280`, and a short `SYSTEM_PROMPT`. The prompt tells the model to pick 1 to 8 entities and to write a Vega-Lite v5 spec that uses only the fields `entity` (nominal), `year` and `value`. The spec must have no `data` (the activity adds it). The model also writes a factual caption of at most 280 characters.
  - The Pydantic models are `WriteChartSpecInput(topic: str = Field(min_length=1), table: OwidTable)` and `ChartDraft(entities: list[str] = Field(min_length=1, max_length=MAX_ENTITIES), vega_lite_spec: dict[str, JsonValue], caption: str = Field(min_length=1, max_length=MAX_CAPTION_CHARS))`. `ChartDraft` is the tool input schema, built with `ChartDraft.model_json_schema()`. The output model is `ChartSpec(vega_lite_spec: dict[str, JsonValue], caption: str, entities: list[str])`. `JsonValue` is `pydantic.JsonValue`, so no `Any` is needed.
  - `class ChartSpecActivities: __init__(self, client: anthropic.AsyncAnthropic, model: str = DEFAULT_MODEL)`. It reuses `DEFAULT_MODEL` from `topic.py` by importing it, with no edit to `topic.py`. The class has `@activity.defn async def write_chart_spec(self, data: WriteChartSpecInput) -> ChartSpec`, which works like this:
    1. If `data.table.rows` is empty, it raises `ApplicationError(..., type=INVALID_DRAFT_ERROR_TYPE, non_retryable=True)` before sending any request.
    2. It makes exactly one `messages.create` call with `tools=[tool]` and `tool_choice={"type": "tool", "name": TOOL_NAME}`. The single user message contains: the topic, `table.title`, `table.value_column`, the distinct entities in first-seen order, the min and max year, the row count, and the first `SAMPLE_ROWS` rows as `entity | year | value` lines. It does not send the full table.
    3. It finds the `ToolUseBlock` named `TOOL_NAME`. If there is none, it raises a non-retryable `ApplicationError`. It validates the block input with `ChartDraft.model_validate`. A `ValidationError`, or any entity that is not in the table, becomes `ApplicationError(..., type=INVALID_DRAFT_ERROR_TYPE, non_retryable=True)`. Task 009 owns regenerating.
    4. It builds the final spec. It copies the draft spec, removes any top-level `data` and `datasets` keys, and sets `"data": {"values": [{"entity": r.entity, "year": r.year, "value": r.value}, ...]}` using only rows whose entity is in `draft.entities`, in table order. It returns `ChartSpec(vega_lite_spec=..., caption=draft.caption, entities=draft.entities)`.
- [x] **`src/ai_trend_pipeline/render.py`** (new) defines:
  - Constants: `PNG_SCALE = 2.0` and `INVALID_SPEC_ERROR_TYPE = "InvalidVegaLiteSpec"`.
  - Models: `RenderChartInput(vega_lite_spec: dict[str, JsonValue], output_dir: str = Field(min_length=1))` and `RenderChartOutput(path: str, spec_sha256: str, size_bytes: int)`.
  - `spec_sha256(spec) -> str` is the sha256 hex of `json.dumps(spec, sort_keys=True, separators=(",", ":"))`.
  - `@activity.defn async def render_chart_png(data: RenderChartInput) -> RenderChartOutput` is a plain function, because there is nothing to inject. It works like this:
    1. It creates `output_dir` with `parents=True, exist_ok=True`.
    2. It calls `vl_convert.vegalite_to_png(spec, scale=PNG_SCALE)` inside `await asyncio.to_thread(...)`, so the CPU-bound call does not block the worker's event loop.
    3. Any exception that `vl_convert` raises for a bad spec (check which type it is, most likely `ValueError`) becomes `ApplicationError(..., type=INVALID_SPEC_ERROR_TYPE, non_retryable=True)`.
    4. It writes the PNG to `<output_dir>/chart-<sha256[:16]>.png` atomically: first a temp file in the same directory, then `os.replace`. It returns the absolute path, the hash and the byte count.
  - The same spec always gives the same path and overwrites the same file, so the activity is idempotent. The spec has inline data only, so rendering makes no network calls.
- [x] **Done-when check.** A test runs `write_chart_spec` on the recorded LLM fixture, passes the result to `render_chart_png`, and gets a real PNG file under `tmp_path`.
- [ ] `just lint` and `just test` pass locally and in CI. No test makes a live LLM or HTTP call. The Anthropic client uses `httpx2.MockTransport` exactly as in `tests/test_topic.py`, and the handler fails the test on any path other than `POST /v1/messages`. PNG checks are deterministic. They test structure, not pixels.
- [x] `README.md` gets two or three lines about the two activities (modules `ai_trend_pipeline.chart_spec` and `ai_trend_pipeline.render`) and the `vl-convert-python` dependency.

## Files to touch

- `pyproject.toml`, `uv.lock` (via `uv add vl-convert-python`)
- `src/ai_trend_pipeline/chart_spec.py` (new)
- `src/ai_trend_pipeline/render.py` (new)
- `tests/test_chart_spec.py` (new)
- `tests/test_render.py` (new)
- `tests/fixtures/llm_chart_spec_response.json` (new). This is a hand-written Messages API response in the same shape as `llm_pick_topic_response.json`: a text block, then a `tool_use` block named `write_chart`. Its input has `entities: ["France", "Japan", "Africa"]`, a short caption, and a line-chart spec (`mark: "line"`, x `year` quantitative with axis format `"d"`, y `value` quantitative, color `entity` nominal, a title). It also has a decoy `"data": {"url": "https://example.com/x.csv"}` to prove that the activity replaces it.
- `tests/fixtures/vega_lite_line_chart.json` (new). This is a complete small Vega-Lite spec with inline `data.values` (2 entities and 3 years each), used for render tests that do not involve the LLM.
- `typings/vl_convert.pyi` (new, only if needed for Pyright strict, see the dependency criterion)
- `README.md`

## Tests to write

LLM tests reuse the `_client(...)` mock-transport pattern from `tests/test_topic.py`. The test table is an `OwidTable` built in code (slug `life-expectancy`, `value_column` `life_expectancy_0`) with rows for Africa, Afghanistan, France and Japan, for years 2000 and later. Tests run through `temporalio.testing.ActivityEnvironment`.

`tests/test_chart_spec.py`:
- `test_write_chart_spec_returns_spec_and_caption`: the result is a `ChartSpec`, and its caption and entities equal the fixture's.
- `test_data_is_injected_from_table`: `spec["data"] == {"values": [...]}`. The values hold only the France, Japan and Africa rows in table order, with keys `entity`, `year` and `value`. Afghanistan is absent, the decoy `url` is gone, and `mark` and `encoding` are kept.
- `test_request_shape`: there is exactly one request. It checks the model, `MAX_TOKENS`, the tool names `== ["write_chart"]` and the forced `tool_choice`. The user message contains the topic, the table title, `life_expectancy_0`, and every entity name.
- `test_custom_model_is_sent`: `ChartSpecActivities(client, model="other-model")` sends `"other-model"`.
- `test_unknown_entity_is_non_retryable`: the handler returns the fixture with `entities` changed to `["Atlantis"]`. The activity raises `ApplicationError` with `type == INVALID_DRAFT_ERROR_TYPE` and `non_retryable is True`.
- `test_invalid_draft_is_non_retryable`: the fixture's caption is replaced with 281 characters. The result is the same error type, non-retryable.
- `test_no_tool_use_raises_non_retryable`: with the existing `llm_pick_topic_no_tool.json`, the activity raises a non-retryable `ApplicationError`.
- `test_empty_table_is_non_retryable_without_request`: with `rows=[]`, the activity raises a non-retryable `ApplicationError` and the handler gets zero requests.
- `test_api_error_propagates`: a 500 response raises `anthropic.APIStatusError`.

`tests/test_render.py`:
- `test_render_writes_png`: renders `vega_lite_line_chart.json` into `tmp_path / "out"`, which does not exist yet. The file exists, starts with `b"\x89PNG\r\n\x1a\n"`, is larger than 1000 bytes, and `size_bytes` equals the file size. The IHDR width and height, read with `struct.unpack(">II", data[16:24])`, are both greater than 0.
- `test_render_is_idempotent`: rendering the same spec twice returns the same path and `spec_sha256`, and the output dir holds exactly one file.
- `test_path_derives_from_spec_hash`: `spec_sha256` equals the helper's value, the filename is `chart-<hash[:16]>.png`, and a changed spec gives a different path.
- `test_invalid_spec_is_non_retryable`: `{"data": {"values": []}}`, which has no mark, raises `ApplicationError` with `type == INVALID_SPEC_ERROR_TYPE` and `non_retryable is True`.
- `test_fixture_to_png_end_to_end`: runs `write_chart_spec` (with the mocked LLM fixture), then `render_chart_png`, and gets a valid PNG (magic bytes, more than 1000 bytes). This is the task's done-when check.

## Out of scope

- LLM critique, retry or regenerate logic (task 009). Workflow wiring, `worker.py` registration, approval, or choosing the production output directory (task 010).
- Posting, alt text, or anything specific to Bluesky (task 011). Storing PNGs in Postgres or object storage.
- Pixel or golden-image comparison, SVG output, themes, custom fonts, or Altair.
- Validating the spec against the Vega-Lite JSON schema. A spec the renderer rejects is caught by `render_chart_png`.
- Any live LLM call or fixture-recording script.
- Changes to `topic.py`, `owid.py`, `hn.py`, `CLAUDE.md`, `docs/mvp-plan.md`, `justfile`, `infra/` or CI.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->
**planner**: Wrote the 008 spec and set Status to `planned`. It is two activities in one PR (2 small modules, 2 test files, 2 JSON fixtures, 1 dependency). If the builder finds it too large, split it into 008a (render plus the `vl-convert-python` dependency) and 008b (LLM spec writer plus the end-to-end test). Next: the orchestrator sets `approved` and the builder works on branch `task/008-vega-spec-render`.
**planner**: Design: the LLM never sends data back. It picks entities and writes encoding only, and the activity adds `data.values`. This saves tokens, stops made-up numbers and keeps payloads small. Rendering uses `vl-convert-python` (no browser or Node), and the PNG path is named by the spec's hash, which makes it idempotent.
**planner**: Things to check while building: the exception type `vl_convert` raises on a bad spec, and whether it ships type stubs (if not, add `typings/vl_convert.pyi`). Running render on a separate machine from the next step assumes a shared filesystem, which is fine for the single-worker MVP and is task 010's concern.
**builder**: Built the whole task (no split needed): chart_spec.py, render.py, 2 test files, 2 fixtures, README, `vl-convert-python` 1.9.0.post1. `just lint` and `just test` pass (56 tests). Open questions: vl_convert raises `ValueError` on a bad spec; it ships its own stubs and `py.typed`, so no `typings/vl_convert.pyi`.
**builder**: Clean-venv check: `uv sync --locked` into a fresh venv, then `uv run --locked pytest tests/test_render.py` gave 5 passed. Render passes `json.dumps(spec)` (a str) to `vegalite_to_png`, which avoids dict invariance issues with `JsonValue`. Test helpers `make_client` and `make_table` in test_chart_spec.py are imported by test_render.py.
**builder**: Git had no user identity configured, so I committed with GIT_AUTHOR/COMMITTER env vars set to the identity of earlier commits (akonale, noreply address); no git config changed. Next: verifier.
**verifier**: Ran `just lint` (ruff check, ruff format, pyright: all pass) and `just test` (56 passed). All checks pass.
**reviewer**: Approved. All criteria and listed tests are present; scope is clean; `vl-convert-python` is a direct dependency in pyproject.toml and uv.lock (wheels for manylinux x86_64/aarch64); no secrets, mock transport rejects any path other than POST /v1/messages.
**reviewer**: Notes: (1) `tests/test_render.py` imports `make_client`/`make_table` from `tests.test_chart_spec`; it works because `tests/__init__.py` exists, but moving them to a `tests/helpers.py` or conftest fixture would be less fragile. (2) `render_chart_png` writes the temp file synchronously on the event loop; fine for PNG sizes, could go in `to_thread` later. (3) Task 010 must register both activities and fix the shared output dir.
