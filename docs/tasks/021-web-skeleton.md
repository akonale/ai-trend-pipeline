# 021: Web app skeleton (FastAPI + Jinja + htmx, health page)

Status: pr-open

## Goal

Add the web layer described in "Web architecture" (`docs/mvp-plan.md`): a `create_app(settings)`
factory, one route module per page, Jinja templates, vendored htmx plus a small CSS file, and FastAPI
dependencies for settings and `ChartService`. Ship one page, `GET /health`, served by `just web` on
`127.0.0.1:8000`.

## Acceptance criteria

- [ ] `create_app(settings: Settings) -> FastAPI` in `web/app.py` stores `settings` and the Jinja
      templates on `app.state`, mounts `web/static/` at `/static`, and includes the health router.
      It reads no env vars and does not connect to Temporal. `/` stays unrouted (404) until task 022.
- [ ] `web/dependencies.py` provides `get_settings(request) -> Settings`, `get_templates(request) ->
      Jinja2Templates` and `async get_chart_service(request, settings=Depends(get_settings)) ->
      ChartService`. `get_chart_service` connects to Temporal lazily through
      `temporal_client.connect(settings)` on first use, caches the client on `app.state`, and returns
      `ChartService(TemporalChartRunGateway(client), settings)`. Values read from `app.state` are
      narrowed with `isinstance` (no `Any` leaking, Pyright strict clean).
- [ ] `GET /health` (`web/routes/health.py`, an `APIRouter` named `router`) returns 200 HTML rendered
      from `health.html`, which extends `base.html` and includes `partials/health_status.html`. The
      page shows "ok" and the configured Temporal address, taken from a `HealthView` Pydantic model in
      `web/view_models.py`. The route depends only on `get_settings` and `get_templates`, never on
      `get_chart_service`, so it works with Temporal down.
- [ ] `base.html` loads `/static/htmx.min.js` (htmx 2.x, vendored as-is, version noted in README) and
      `/static/app.css`; both are served with 200. No JS build step, no CDN links.
- [ ] `just web` runs `uv run python -m ai_trend_pipeline.web`; `web/__main__.py` calls
      `load_settings()`, then `uvicorn.run(create_app(settings), host="127.0.0.1", port=8000)`.
      `CLAUDE.md` commands table and `README.md` list `just web` (the guard test in
      `tests/test_claude_md.py` requires the CLAUDE.md row).
- [ ] Dependencies added with `uv add fastapi jinja2 uvicorn` (`httpx` is already a main dependency,
      so the TestClient needs nothing extra). `just lint` and `just test` pass.

## Files to touch

- `src/ai_trend_pipeline/web/__init__.py` (new, empty docstring)
- `src/ai_trend_pipeline/web/__main__.py` (new)
- `src/ai_trend_pipeline/web/app.py` (new)
- `src/ai_trend_pipeline/web/dependencies.py` (new)
- `src/ai_trend_pipeline/web/view_models.py` (new, `HealthView` only)
- `src/ai_trend_pipeline/web/routes/__init__.py` (new)
- `src/ai_trend_pipeline/web/routes/health.py` (new)
- `src/ai_trend_pipeline/web/templates/base.html` (new)
- `src/ai_trend_pipeline/web/templates/health.html` (new)
- `src/ai_trend_pipeline/web/templates/partials/health_status.html` (new)
- `src/ai_trend_pipeline/web/static/htmx.min.js` (new, vendored)
- `src/ai_trend_pipeline/web/static/app.css` (new, small)
- `tests/test_web.py` (new)
- `justfile` (add `web` recipe)
- `CLAUDE.md` (add `just web` row to the Commands table only)
- `README.md` (short "Web app" section: `just web`, URL, SSH tunnel hint, htmx version)
- `pyproject.toml`, `uv.lock` (via `uv add` only)

## Tests to write

All in `tests/test_web.py`, using `fastapi.testclient.TestClient(create_app(Settings(...)))`, no Temporal.

- `create_app` returns a `FastAPI` whose `app.state.settings` is the passed `Settings` instance.
- `GET /health` returns 200, `text/html`, contains "ok" and the Settings' `temporal_address`.
- Health HTML references `/static/htmx.min.js` and `/static/app.css` (proves `base.html` is used).
- `GET /static/htmx.min.js` and `GET /static/app.css` return 200.
- `GET /` returns 404 (no home page yet).
- Overriding `get_settings` via `app.dependency_overrides` with a different `temporal_address`
  changes what `/health` shows.
- Overriding `get_chart_service` with a function that raises: `/health` still returns 200 (health
  never touches the chart service).
- `get_chart_service` override works: a test-local route added to the app in the test that depends on
  `get_chart_service` receives the fake `ChartService` (built on a fake gateway from the test).

## Out of scope

- Home, runs, run pages, forms, htmx fragments beyond the health status partial (tasks 022-024).
- `current_user`, auth, `AUTH_MODE`, Cloudflare (025-026). No new `Settings` fields (no web host/port
  settings; host and port are constants in `web/__main__.py`).
- Checking Temporal reachability on the health page; JSON health endpoints.
- Any change to `services/`, `config.py`, `cli.py`, workflows or activities.
- Docker/compose changes or deploying the web app.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: Wrote spec for the web skeleton (create_app, dependencies, /health, vendored htmx, `just web`).
Next: approval, then builder on `task/021-web-skeleton`. Note: plan's approval column reads `noo` (likely `no`).
Builder: vendor htmx 2.x as-is (needs one download); `tests/test_claude_md.py` forces the CLAUDE.md `just web` row.
`get_chart_service` connects lazily so app startup and `/health` never need Temporal.
Manual "Done when" check (page loads through the SSH tunnel) is for the human after merge.

**builder**: Built web skeleton (create_app, dependencies, /health, htmx 2.0.4 vendored, `just web`); tests in tests/test_web.py.
Added ruff `extend-immutable-calls = ["fastapi.Depends"]` in pyproject (B008 on `Depends` defaults); not in spec's file list but pyproject is touched anyway.
Tests use two `pyright: ignore[reportAny]`-style comments for starlette's untyped `app.state`. Next: verifier.

**verifier**: `just lint` (ruff check, ruff format --check, pyright strict) passed; `just test` passed (134 passed).
All checks pass.

**reviewer**: Approved. All criteria and listed tests present; layering, lazy Temporal connect and no-env-var rules hold.
Notes: `pyright: ignore[reportAny]` in `web/dependencies.py` lacks an explanatory comment (starlette untyped `app.state`); the ruff `extend-immutable-calls` change is outside the file list but justified.

**scribe**: Docs already current (README "Web app" section, CLAUDE.md `just web` row); no doc edits needed.
Pushed `task/021-web-skeleton`, opened PR #23: https://github.com/akonale/ai-trend-pipeline/pull/23
Next: human reviews and merges PR #23.
