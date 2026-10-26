# 020: Service layer + settings

Status: built

## Goal

Move the start / show / decide use cases out of `cli.py` into `services/charts.py`, behind a small
run-gateway protocol so tests can use a fake. Add `config.py` with one `Settings` model, which
becomes the only place env vars are read. The CLI becomes a thin wrapper. No behaviour change.

## Design (follow exactly)

Dependencies point one way: `cli` -> `services` -> Temporal client -> workflows. `config.py` is a
leaf. It imports only stdlib and pydantic. Workflows and activities never import `services`.

**`config.py`**
- `Settings(BaseModel)`, frozen. Fields and defaults:
  `temporal_address: str = "localhost:7233"`, `chart_output_dir: str | None = None`,
  `anthropic_api_key: SecretStr | None = None`, `bluesky_handle: str | None = None`,
  `bluesky_app_password: SecretStr | None = None`,
  `bluesky_service: str = DEFAULT_BLUESKY_SERVICE` (`"https://bsky.social"`, a constant moved from
  `bluesky.py` into `config.py`; `bluesky.py` may import it under its old name `DEFAULT_SERVICE`).
- `load_settings(environ: Mapping[str, str] | None = None) -> Settings`. It reads `os.environ` when
  `environ` is None, at call time. This is the only `os.environ` / `os.getenv` use under `src/`.
- Keep the current rules for empty values: `TEMPORAL_ADDRESS` falls back to the default only when
  absent. An empty `CHART_OUTPUT_DIR`, `ANTHROPIC_API_KEY`, `BLUESKY_HANDLE`,
  `BLUESKY_APP_PASSWORD` or `BLUESKY_SERVICE` counts as unset (None, or the default service).
- Do not add `pydantic-settings` or any other dependency.

**Settings are passed in, never read from a global**
- `temporal_client.connect(settings: Settings) -> Client`. Remove `temporal_address()`.
- `topic.make_anthropic_client(settings: Settings)`. It keeps raising the same `RuntimeError`
  naming `ANTHROPIC_API_KEY` when the key is None.
- `bluesky.load_bluesky_credentials(settings: Settings) -> BlueskyCredentials`. It raises the same
  `RuntimeError` listing the missing `BLUESKY_HANDLE` / `BLUESKY_APP_PASSWORD`.
- `worker.main` and `start_hello.main` call `load_settings()` once and pass the result down.
  `worker.connect_with_retry(settings)`.

**`services/charts.py`** (use cases, no printing, no argparse, no temporalio exception imports)
- Request models (Pydantic): `StartChartRequest(wf_id: str | None = None, topic: str | None = None)`,
  `ChartRunRef(wf_id: str)`, `DecideChartRequest(wf_id: str, approved: bool, note: str = "")`.
  Result model: `StartChartResult(wf_id: str)`. Show returns the existing `ReviewState`. Decide
  returns the existing `DailyChartResult`.
- Errors: `ChartServiceError(Exception)` base, plus `RunNotFound(wf_id)`, `RunAlreadyExists(wf_id)`
  and `NotAwaitingDecision(wf_id, status)`. Each keeps its fields as attributes.
- Protocols: `ChartRunHandle` has `id: str`, `async review_state() -> ReviewState`,
  `async send_decision(ApprovalDecision) -> None` and `async result() -> DailyChartResult`.
  `ChartRunGateway` has `async start(wf_id: str, data: DailyChartInput) -> None` (raises
  `RunAlreadyExists`) and `handle(wf_id: str) -> ChartRunHandle`. Handle methods raise `RunNotFound`.
- `ChartService(gateway: ChartRunGateway, settings: Settings, now: Callable[[], datetime] = <UTC now>)`
  has these methods:
  - `async start(req: StartChartRequest) -> StartChartResult`. If no id is given, use
    `daily-chart-{now().date().isoformat()}`. Pass `output_dir` only when
    `settings.chart_output_dir` is set, as `cli._start` does today.
  - `async show(ref: ChartRunRef) -> ReviewState`.
  - `async decide(req: DecideChartRequest) -> DailyChartResult`. It queries the state. If the status
    is not `awaiting_decision` it raises `NotAwaitingDecision`. Otherwise it signals and awaits the
    result.
- Later tasks add `refine` and `list_runs` as new `ChartService` methods and gateway methods. Do not
  add them now.

**`services/temporal_gateway.py`**
- `TemporalChartRunGateway(client: Client)` implements `ChartRunGateway`. It holds the only Temporal
  calls: `start_workflow(..., task_queue=DAILY_CHART_TASK_QUEUE, id_conflict_policy=FAIL)`,
  `get_workflow_handle_for`, the query, the signal and `result()`. It translates
  `WorkflowAlreadyStartedError` to `RunAlreadyExists` and `RPCError` with `NOT_FOUND` to
  `RunNotFound`. It re-raises every other `RPCError`.

**`cli.py`** (thin): `run_cli(argv: list[str], client: Client, settings: Settings | None = None) -> int`.
It parses args, uses `settings or load_settings()`, builds
`ChartService(TemporalChartRunGateway(client), settings)`, calls one method, prints, and maps
service errors to exit code 1 with the exact current messages:
`A run with id {id} already exists.`, `Cannot decide: status is {status}.` and
`Error: workflow {id} not found.` Output for start, show and decide stays byte-for-byte the same.
`_main` calls `load_settings()` and `connect(settings)`.

## Acceptance criteria

- [ ] `tests/test_cli.py` passes with no edits (`git diff main...HEAD -- tests/test_cli.py` is empty).
- [ ] `grep -rnE "os\.environ|getenv" src/` matches only `src/ai_trend_pipeline/config.py`.
- [ ] `cli.py` no longer imports `temporalio.exceptions`, `temporalio.service` or `datetime`, and has
      no `os` import. All workflow interaction goes through `ChartService`.
- [ ] `services/charts.py` imports nothing from `temporalio`, `argparse` or `cli`, and has no `print`.
      `daily_chart.py` and the activity modules import nothing from `services` or `config`, except
      `bluesky.py` and `topic.py`, which import `Settings` / the default-service constant from `config`.
- [ ] New service tests run with a fake `ChartRunGateway` and no Temporal environment.
- [ ] `just lint` and `just test` pass. Pyright strict, no new `Any` or `# type: ignore`.

## Files to touch

- `src/ai_trend_pipeline/config.py` (new)
- `src/ai_trend_pipeline/services/__init__.py` (new, empty or a docstring)
- `src/ai_trend_pipeline/services/charts.py` (new)
- `src/ai_trend_pipeline/services/temporal_gateway.py` (new)
- `src/ai_trend_pipeline/cli.py`
- `src/ai_trend_pipeline/temporal_client.py`
- `src/ai_trend_pipeline/topic.py`
- `src/ai_trend_pipeline/bluesky.py`
- `src/ai_trend_pipeline/worker.py`
- `src/ai_trend_pipeline/start_hello.py`
- `tests/test_config.py` (new)
- `tests/test_chart_service.py` (new)
- `tests/test_hello.py` (drop `test_temporal_address_default_and_env`; it moves to `test_config.py`)
- `tests/test_topic.py` (`test_make_anthropic_client_reads_env` now passes a `Settings`)
- `tests/test_bluesky.py` (the `load_bluesky_credentials` test now passes a `Settings`)
- `README.md` (one or two lines: env vars are read in `config.py`. The list of env vars stays the same.)

## Tests to write

`tests/test_config.py`:
- `load_settings({})` returns all defaults (`localhost:7233`, `https://bsky.social`, the rest None).
- `load_settings` maps every env var to its field. The secrets are `SecretStr` and do not appear in `repr(settings)`.
- An empty `CHART_OUTPUT_DIR`, `ANTHROPIC_API_KEY` or `BLUESKY_SERVICE` counts as unset.
- `load_settings()` with no argument reads `os.environ` at call time (monkeypatch `TEMPORAL_ADDRESS`).

`tests/test_chart_service.py` (fake gateway and fake handle, fixed `now`):
- `start` with no id uses `daily-chart-YYYY-MM-DD` from the injected clock and returns it.
- `start` passes the topic, and `output_dir` from settings only when it is set (the default otherwise).
- `start` lets `RunAlreadyExists` from the gateway propagate.
- `show` returns the handle's `ReviewState`. An unknown id raises `RunNotFound`.
- `decide` while awaiting sends `ApprovalDecision(approved, note)` and returns the result.
- `decide` while not awaiting raises `NotAwaitingDecision` with the status and sends no signal.

Updated tests:
- `test_topic.py`: `make_anthropic_client(Settings(anthropic_api_key=...))` sets the key, and a None key raises `RuntimeError` naming `ANTHROPIC_API_KEY`.
- `test_bluesky.py`: `load_bluesky_credentials(Settings(...))` raises when the password is missing, and returns the credentials with the default service otherwise.

## Out of scope

- Any web code (FastAPI, Jinja, `web/`, `create_app`, `just web`): that is task 021.
- New use cases or settings: `refine`, `list_runs`, `TEMPORAL_UI_URL`, `AUTH_MODE`, `DAILY_CHART_CAP`.
- Changing CLI arguments, output text, exit codes, workflow or activity behaviour, or the justfile.
- Adding dependencies (including `pydantic-settings`).
- Editing `tests/test_cli.py`, `daily_chart.py`, the other activity modules, or `CLAUDE.md`.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->
**planner**: Wrote spec. Env reads go to `config.Settings` and `load_settings()`. The use cases go to `services.ChartService` behind a `ChartRunGateway` protocol (Temporal adapter in `services/temporal_gateway.py`).
`run_cli` gets an optional `settings` arg that defaults to `load_settings()` at call time, so `test_cli.py` (which monkeypatches `CHART_OUTPUT_DIR`) passes unchanged.
Odd: CLAUDE.md has no "Architecture rules" section. I followed mvp-plan "Web architecture". A human may want to add an "env vars only in config.py" rule to CLAUDE.md.
`test_hello`, `test_topic` and `test_bluesky` change, because their env-reading helpers now take `Settings`. Next: approval (orchestrator, task is `no`).
**builder**: Implemented config.py, services/charts.py, services/temporal_gateway.py; thin cli; Settings passed into connect/topic/bluesky/worker/start_hello. `test_cli.py` untouched. Lint, pyright and full suite pass (126).
Followed CLAUDE.md "Architecture rules". `bluesky.py` no longer exposes `DEFAULT_SERVICE` (nothing used it). Next: verifier.
