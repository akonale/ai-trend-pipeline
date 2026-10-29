# Tech side: workflow, web, infra, development

How the system is built. For what it does and the business rules see [business.md](business.md).

Contents: [1 Temporal](#1-temporal) | [2 Web app](#2-web-app) | [3 Infra](#3-infra-and-deployment) | [4 Settings](#4-settings-configpy) | [5 Development](#5-development)

## 1. Temporal

### Workflow (`daily_chart.py`)

`DailyChartWorkflow.run(DailyChartInput) -> DailyChartResult`, task queue
`DAILY_CHART_TASK_QUEUE = "daily-chart-task-queue"`.

```
 drafting ─▶ awaiting_decision ─┬─ decide(approved) ─▶ posting ─▶ approved
                                ├─ decide(rejected) ─────────────▶ rejected
                                └─ 20 h timeout ─────────────────▶ expired
```

- Steps: HN stories and `pick_topic` (skipped with a manual topic), `fetch_owid_dataset`,
  `draft_chart_with_critique`, `render_chart_png`, wait, `post_chart_to_bluesky`.
- Signal `decide(ApprovalDecision)`: ignored unless status is `awaiting_decision` and no decision
  exists yet. Approve and reject are one signal (`approved: bool`, `note`, `decided_by`).
- Query `review_state() -> ReviewState` (status, `ReviewRequest`, note, `requested_by`,
  `decided_by`, `options`, `option_notes`). Web and CLI read all run state from this query.
- Timeouts: I/O activities 60 s, 3 attempts (`IO_ACTIVITY_TIMEOUT`, `IO_RETRY_POLICY`); LLM
  activities 120 s, 3 attempts (`LLM_ACTIVITY_TIMEOUT`, `LLM_RETRY_POLICY` in `chart_loop.py`);
  approval wait `APPROVAL_TIMEOUT` = 20 hours. Non-retryable `ApplicationError` types:
  `NoMatchingDataset`, `InvalidChartDraft`, `InvalidVegaLiteSpec`, `InvalidChartCritique`,
  `ImageTooLarge`, `ChartFileMissing`, `BlueskyAuthFailed`. `TopicNotInCatalog` is retryable.
- Requester and approver: `requested_by` is in `DailyChartInput` (also stored as workflow memo
  `requested_by` for the cap); `decided_by` is in the signal. Both are copied into `ReviewState`.

### Activities and models

| Activity | Class / function | Input -> output |
| --- | --- | --- |
| `fetch_hn_top_stories` | `HNActivities` | `HNTopStoriesInput` -> `HNTopStoriesOutput` |
| `pick_topic` | `TopicActivities` | `PickTopicInput` -> `PickTopicOutput` |
| `fetch_owid_dataset` | `OwidActivities` | `FetchOwidInput` -> `OwidTable` |
| `write_chart_spec` | `ChartSpecActivities` | `WriteChartSpecInput` -> `ChartSpec` |
| `critique_chart` | `CritiqueActivities` | `CritiqueChartInput` -> `ChartCritique` |
| `render_chart_png` | module function | `RenderChartInput` -> `RenderChartOutput` |
| `post_chart_to_bluesky` | `BlueskyActivities` | `PostChartInput` -> `PostChartOutput` |

Activity classes take their clients in `__init__` (httpx / Anthropic), so tests pass fakes with
`MockTransport`.

### Determinism, idempotency, replay

- No I/O, clock, randomness or LLM calls in workflow code. The workflow uses `workflow.now()` and
  `workflow.info().workflow_id`. Third-party imports go inside
  `workflow.unsafe.imports_passed_through()`.
- Idempotency: `render_chart_png` names the file by spec hash and replaces atomically. Posting:
  the Bluesky record key is a TID built from `created_at` (`workflow.now()`) and the workflow id
  (`make_tid`); the activity creates a session, calls `getRecord` for that key, and returns
  `already_posted=True` if it exists, otherwise uploads the blob and `createRecord`s with that key.
- Replay compatibility: new fields on workflow inputs and state must be optional with defaults
  (`requested_by`, `style`, `options`, `option_notes`, `decided_by` all are), so open runs from older
  code still replay.
- Pydantic everywhere: `temporal_client.connect(settings)` uses `pydantic_data_converter`.

### Worker (`worker.py`)

`main()` loads settings, connects with retry (30 s), builds the Anthropic, HN, OWID and Bluesky
clients and runs two workers: `hello-task-queue` (`HelloWorkflow`, `say_hello`) and the daily chart
queue (`DailyChartWorkflow` and the seven activities from `daily_chart_activities`). It exits at
startup if `ANTHROPIC_API_KEY`, `BLUESKY_HANDLE` or `BLUESKY_APP_PASSWORD` is missing.
`DraftChartWorkflow` (in `chart_loop.py`) is not registered.

## 2. Web app

### Layering

```
 web routes ─┐
             ├──▶ services ──▶ Temporal client ──▶ workflows ──▶ activities
 CLI ────────┘     (charts)                        (no web or service imports)
```

| Folder | Holds | Never does |
| --- | --- | --- |
| `src/ai_trend_pipeline/services/` | Use cases: start a chart, get its state, decide, style profiles. Plain async classes taking Pydantic models | Read HTTP requests, render HTML, print |
| `src/ai_trend_pipeline/web/app.py` | `create_app(settings)` factory: wires routes, templates, static files | Business logic |
| `src/ai_trend_pipeline/web/routes/` | One module per page (`home.py`, `runs.py`, `settings.py`, `health.py`). Parse input, call one service, render a template | Call Temporal or the LLM directly |
| `src/ai_trend_pipeline/web/templates/` | Jinja templates; `partials/` for htmx fragments | Logic beyond loops and ifs |
| `src/ai_trend_pipeline/web/view_models.py` | Pydantic models shaped for templates (`HomeView`, `RunView`, `SettingsView`, `build_*_view`) | I/O |
| `src/ai_trend_pipeline/config.py` | One `Settings` model read from env vars, passed in, not imported as a global | Read env vars anywhere else |

- Routes get services and settings through FastAPI dependencies, so tests swap in fakes. Route tests
  never need Temporal running.
- The signed-in user is one dependency, `current_user`, used by every route that changes
  something.
- No JavaScript build step: htmx and a small CSS file, served from `web/static/`.

### Dependencies (`web/dependencies.py`)

`get_settings`, `get_templates` (read `app.state`), `get_style_store` (builds one
`PostgresStyleStore` per process, no I/O), `get_style_service`, `get_chart_service` (connects to
Temporal once, caches the client on `app.state`, returns `ChartService(TemporalChartRunGateway,
settings, styles=...)`), `current_user`.

### Routes

| Method | Path | Purpose | Notes |
| --- | --- | --- | --- |
| GET | `/health` | Health page | no Temporal call |
| GET | `/` | Home: topic box, options, chips | `?topic=` prefills |
| POST | `/runs` | Validate form, start a run, 303 to `/runs/{id}` | uses `current_user`; 400 bad options, or empty topic or over `MAX_TOPIC_LENGTH` (200 characters), 429 cap, 409 id exists, 503 style store down |
| GET | `/runs/{id}` | Run page | 404 page if unknown |
| GET | `/runs/{id}/status` | htmx fragment, polled every 2 s while `polling` | |
| GET | `/runs/{id}/chart.png` | The PNG | 404 unless the file is inside `CHART_OUTPUT_DIR` |
| POST | `/runs/{id}/decision` | Send approve/reject, 303 to `?sent=1` | uses `current_user`; 400 bad value, 409 not awaiting, 404 unknown |
| GET, POST | `/settings` | Style profile form | uses `current_user`; 400 invalid, 503 store down |

Which routes touch Postgres: only `POST /runs` (style lookup via `StyleStore.get`) and `/settings`.
Other routes work while Postgres is down.

### Services

- `ChartService` (`services/charts.py`): `start`, `show`, `decide` (waits for the result, used by
  the CLI), `submit_decision` (signal only, used by the web), `chart_image`. Talks to a
  `ChartRunGateway` protocol: `start`, `handle(wf_id)` (query `review_state`, signal, `result`),
  `count_started_since`. Errors: `RunNotFound`, `RunAlreadyExists`, `NotAwaitingDecision`,
  `DailyCapReached`, `ChartImageNotFound`.
- `TemporalChartRunGateway` (`services/temporal_gateway.py`): starts with `id_conflict_policy=FAIL`
  and memo `requested_by`; maps gRPC NOT_FOUND to `RunNotFound`. The daily cap runs
  `list_workflows("WorkflowType = 'DailyChartWorkflow' AND StartTime >= '<00:00 UTC>'")` and counts
  matching memos. Runs without the memo are not counted.
- `StyleService` / `StyleStore` protocol (`services/styles.py`); `PostgresStyleStore`
  (`services/postgres_style_store.py`): psycopg 3, one short connection per call, schema and table
  created on first use and retried if setup fails; `psycopg.Error` or `OSError` become
  `StyleStoreUnavailable`; a stored profile that no longer validates is ignored (defaults used).
  Table `topic_studio.style_profiles(email primary key, profile jsonb, updated_at)`.

### Auth (`current_user`)

| `AUTH_MODE` | Behaviour |
| --- | --- |
| `local` (default) | everyone is `dev@localhost` |
| `cloudflare` | email from header `Cf-Access-Authenticated-User-Email`, lower-cased; missing header gives 401 |

The Access JWT is not verified. That is acceptable only because prod publishes the web port on
`127.0.0.1` and the only way in is the tunnel, which sits behind Access; nothing else can set the
header. Do not expose the web port publicly.

### Templates, view models, design

Templates (`base.html`, `home.html`, `run.html`, `settings.html`, `health.html`,
`run_not_found.html`, `partials/`) hold only loops and ifs; labels, badges, option lines and error
text are computed in `view_models.py` (`STATUS_LABELS`, `badge_for`, `option_lines`,
`options_error_message`, `style_error_message`). Static assets in `web/static/`: `app.css` and
vendored `htmx.min.js` (2.0.4). `app.css` defines design tokens as CSS custom properties on
`:root` (`--color-*`, `--font-*`, `--text-*`, `--space-*`, `--radius-*`, `--shadow-*`), redefined
under `@media (prefers-color-scheme: dark)`. Fonts are system stacks (no web fonts).

## 3. Infra and deployment

### Image (`Dockerfile`)

`python:3.12.16-slim`, `uv` 0.13.6 copied in, `uv sync --frozen --no-dev` into `/opt/venv`,
`fonts-liberation` installed (needed for chart fonts), non-root user `app` (uid 1000), `/data/charts`
owned by it. Default command is the worker; compose overrides it for web.

### Compose

| | `infra/compose.dev.yaml` | `infra/compose.prod.yaml` (project `ai-trend-prod`) |
| --- | --- | --- |
| Services | `postgres`, `temporal` | `postgres`, `temporal`, `temporal-ui`, `worker`, `web`, `cloudflared` |
| Postgres | `postgres:16.15`, 127.0.0.1:5432 | `postgres:16.15`, no published port |
| Temporal | `temporalio/temporal:1.9.1` `start-dev` (in memory), 127.0.0.1:7233 and 8233 | `temporalio/auto-setup:1.29.7` on Postgres (`DB=postgres12`) |
| UI | built into dev server (8233) | `temporalio/ui:2.51.1`, 127.0.0.1:18233 |
| Worker and web | run from the host (`just dev`, `just web` on 8000) | built from `Dockerfile`; web on 127.0.0.1:18000 |
| Tunnel | none | `cloudflare/cloudflared:2026.10.4`, token from env |
| Volumes | `postgres-data` | `postgres-data`, `charts` (shared by worker and web at `/data/charts`) |

Dev Postgres is used only for style profiles; the dev Temporal server keeps nothing across
restarts. Prod Temporal stores its history in the same Postgres server (its own databases, created
by auto-setup) next to the `topic_studio` schema in the default `postgres` database. The worker
gets Anthropic and Bluesky settings, the web gets `AUTH_MODE: cloudflare`, `WEB_HOST: 0.0.0.0`,
`DATABASE_URL` and `TEMPORAL_UI_URL`. Prod `DATABASE_URL` is built in compose in key/value form
from `POSTGRES_PASSWORD`, so the password may not contain spaces, quotes or backslashes. The worker
gets no `DATABASE_URL`.

### Env files and deploy

- Dev: `.env` (gitignored), loaded by `set dotenv-load := true` in the `justfile`.
- Prod: `.env.prod` on the server only (gitignored); keys are listed in `docs/runbooks/deploy.md`.
- `just deploy` = `git pull --ff-only`, then compose under `env -i PATH HOME` with
  `--env-file .env.prod`. `env -i` matters because dotenv-load exports the dev `.env` and shell
  variables beat `--env-file`.
- Never read or commit either file.

### Cloudflare Tunnel and Access

`cloudflared` runs `tunnel run` with a token. Two public hostnames (placeholders `app.example.com`
-> `http://web:8000`, `temporal.example.com` -> `http://temporal-ui:8080`) each have an Access
application with one-time-PIN login: the app hostname allows the owner and invited people, the
Temporal UI hostname the owner only. Set `TEMPORAL_UI_URL` in `.env.prod` to the public Temporal
hostname. See `docs/runbooks/cloudflare.md`; operations in `docs/runbooks/deploy.md`.

### CI and bootstrap

`.github/workflows/ci.yml`: on pull requests and pushes to `main`, `uv sync --locked`,
`just lint`, `just test`. Server setup: `infra/cloud-init.yaml` (new Ubuntu 24.04 server: user, SSH
hardening, Docker, GitHub CLI, uv, Temporal CLI) or `infra/bootstrap.sh` (the same on an existing
server).

## 4. Settings (`config.py`)

`Settings` is a frozen Pydantic model returned by `load_settings()`; it is the only reader of
environment variables.

| Env var | Field | Purpose | Default |
| --- | --- | --- | --- |
| `TEMPORAL_ADDRESS` | `temporal_address` | Temporal frontend | `localhost:7233` |
| `TEMPORAL_UI_URL` | `temporal_ui_url` | base for run links | `http://localhost:8233` |
| `WEB_HOST` | `web_host` | bind address of `python -m ai_trend_pipeline.web` (port fixed at 8000) | `127.0.0.1` |
| `CHART_OUTPUT_DIR` | `chart_output_dir` | where PNGs are written and served from | unset (`charts`) |
| `ANTHROPIC_API_KEY` | `anthropic_api_key` | LLM calls (worker) | unset |
| `BLUESKY_HANDLE` | `bluesky_handle` | posting account (worker) | unset |
| `BLUESKY_APP_PASSWORD` | `bluesky_app_password` | app password (worker) | unset |
| `BLUESKY_SERVICE` | `bluesky_service` | PDS base URL | `https://bsky.social` |
| `AUTH_MODE` | `auth_mode` | `local` or `cloudflare` | `local` |
| `DAILY_CHART_CAP` | `daily_chart_cap` | runs per person per UTC day, at least 1 | `10` |
| `DATABASE_URL` | `database_url` | Postgres for style profiles | local dev Postgres URL |

Secrets use `SecretStr`. Compose-only keys (`POSTGRES_PASSWORD`, `CLOUDFLARE_TUNNEL_TOKEN`,
`APP_URL` documentation only) are not read by the app.

## 5. Development

- Setup: `uv sync`; `just dev` (compose Postgres and Temporal, then the worker); `just web`;
  `just chart [--topic "..."] [--id ...]`, `just show|approve|reject <id>`, `just hello`,
  `just lint`, `just fmt`, `just test`, `just down`.
- Tests (`tests/`): TDD, no live calls. Activities use `httpx.MockTransport` and recorded
  fixtures; web routes use `app.dependency_overrides` with fake services (`tests/style_fakes.py`
  has `InMemoryStyleStore`); workflows use `WorkflowEnvironment.start_time_skipping()`
  (`test_daily_chart.py`, `test_chart_loop.py`, `test_hello.py`). Guard tests check `CLAUDE.md`
  structure, compose files and CI.
- Lint: Ruff (line length 100) and Pyright strict; no `# type: ignore` or `Any` without a comment.
- Agent relay: planner, builder, verifier, reviewer, scribe with human checkpoints; see
  `docs/mvp-plan.md` and `CLAUDE.md` (do not copy them).

Add an activity: write tests with a fixture; Pydantic input and output; make it idempotent; define
it on a class that takes its clients; register it in `worker.daily_chart_activities`; call it from
the workflow with a timeout and retry policy; new workflow fields optional with defaults.

Add a route: parse input only; call one service obtained via a dependency; shape data in
`view_models.py`; template with no logic; test with a fake service.

Add a setting: field on `Settings` and a line in `load_settings` (the only env reader); pass
`Settings` in; add to compose and `docs/runbooks/deploy.md` if prod needs it; update `README.md`.

Pitfalls (details in `docs/history.md`): shell variables beat `--env-file`, so deploy under `env -i`;
LLM activities use `tool_choice` auto; the postgres image bump needed a collation refresh; the
Postgres password format restriction; `vl-convert` needs `fonts-liberation`; the worker will not
start without LLM and Bluesky credentials.
