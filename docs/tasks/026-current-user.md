# 026: Know who's who (current user, requester and approver on each run)

Status: pr-open

## Goal

Add a `current_user` FastAPI dependency (Cloudflare Access email header in production, a fixed dev
user locally). Record who requested each run and who approved or rejected it, and show both on the
run page. The runs list (024) is not built yet, so for now the names appear only on the run page.
024 picks up the same fields later.

## Storage decision

No database. The requester goes into the workflow **input** (`DailyChartInput.requested_by`). The
approver goes into the **signal payload** (`ApprovalDecision.decided_by`). The workflow copies both
into `ReviewState`, which the run page already reads through the `review_state` query, and the
decider also goes into `DailyChartResult`. Temporal stores inputs and signals in the workflow history,
so both survive worker, web and server restarts and replay with no extra code. This is the simplest
option: no new activities, no client-side memo or search-attribute registration, and the existing
gateway and query path stay as they are. Memo or search attributes are only worth adding when 024
needs to filter or list runs without querying each one. That is 024's call, not this task's.

## Acceptance criteria

- [ ] **Settings.** `Settings` gains `auth_mode: Literal["local", "cloudflare"] = "local"`, read from
      `AUTH_MODE` in `load_settings`. Unset or empty means `local`. Any other value raises (the
      Pydantic validation error is fine). `infra/compose.prod.yaml` sets `AUTH_MODE: cloudflare` on
      the `web` service as a literal value, not an interpolation. `compose.dev.yaml` and `.env` files
      are untouched. No real emails or domains appear anywhere in the repo. The dev user is the
      constant `DEV_USER_EMAIL = "dev@localhost"`.
- [ ] **Dependency.** `web/dependencies.py` gains `CurrentUser(BaseModel)` with `email: str` and
      `current_user(request, settings = Depends(get_settings)) -> CurrentUser`:
      - `local`: always returns `CurrentUser(email=DEV_USER_EMAIL)` and ignores any header.
      - `cloudflare`: reads `Cf-Access-Authenticated-User-Email`, strips it and lowercases it, and
        returns it. If the header is missing or blank, it raises `HTTPException(401)`. It never falls
        back to the dev user.
      Every route that changes something uses it: `POST /runs` and `POST /runs/{wf_id}/decision`.
      GET routes do not use it. When it returns 401, no service method is called.
- [ ] **Workflow models and state** (`daily_chart.py`, no new activities, signals, timers or imports):
      - `DailyChartInput.requested_by: str | None = None`
      - `ApprovalDecision.decided_by: str | None = None`
      - `ReviewState` gains `requested_by: str | None = None` and `decided_by: str | None = None`
      - `DailyChartResult.decided_by: str | None = None`

      `run` sets `requested_by` on the state as its first statement and keeps it on every later
      `ReviewState` assignment. From `posting` onward, `decided_by` is the decision's `decided_by`. Expired runs
      have `decided_by=None`. The defaults keep old histories and the CLI working unchanged.
- [ ] **Services** (`services/charts.py`). The user is passed in as plain data, and the service never
      imports `web`. `StartChartRequest` gains `requested_by: str | None = None`, and `start` puts it
      into `DailyChartInput`. `DecideChartRequest` gains `decided_by: str | None = None`, and both
      `decide` and `submit_decision` put it into `ApprovalDecision`. The CLI passes nothing, so
      `cli.py` does not change.
- [ ] **Routes and view.** The routes pass `user.email` into the request models and stay thin.
      `RunView` gains `requested_by: str | None` and `decided_by: str | None`, filled by
      `build_run_view` from the state. `partials/run_status.html` shows "Requested by: X" when it is
      set. It shows "Approved by: Y" or "Rejected by: Y" when `decided_by` is set and the status is
      `posting`, `approved` or `rejected`. To pick "Approved" or "Rejected", it uses a view-model
      field (for example `decided_label`) rather than template logic.
- [ ] `README.md` and `docs/runbooks/cloudflare.md` get one or two lines on `AUTH_MODE`: what it
      does, that prod sets it in compose, and that the header is trusted only because the web port is
      bound to `127.0.0.1` and reached only through the tunnel. `just lint` and `just test` pass.

## Files to touch

- `src/ai_trend_pipeline/config.py`
- `src/ai_trend_pipeline/daily_chart.py` (model fields and state assignments only)
- `src/ai_trend_pipeline/services/charts.py`
- `src/ai_trend_pipeline/web/dependencies.py`
- `src/ai_trend_pipeline/web/routes/runs.py`
- `src/ai_trend_pipeline/web/view_models.py`
- `src/ai_trend_pipeline/web/templates/partials/run_status.html`
- `infra/compose.prod.yaml` (one env line on `web`)
- `tests/test_config.py`, `tests/test_web.py`, `tests/test_chart_service.py`,
  `tests/test_daily_chart.py`, `tests/test_compose_prod.py`
- `README.md`, `docs/runbooks/cloudflare.md`

## Tests to write

Route tests use `TestClient(create_app(...))` with the existing fake gateway (`RecGateway`) and a
`Settings` override for `auth_mode`. No Temporal.

- Config: `AUTH_MODE` unset or empty gives `local`. `cloudflare` gives `cloudflare`. `bogus` raises.
- Dependency (local): `POST /runs` with no header starts a run whose recorded `DailyChartInput.requested_by == "dev@localhost"`.
- Dependency (local): a `Cf-Access-Authenticated-User-Email` header is ignored, and the dev user is still used.
- Dependency (cloudflare): `POST /runs` with header ` Friend@Example.com ` records `requested_by == "friend@example.com"`.
- Dependency (cloudflare): `POST /runs` without the header, or with a blank one, returns 401, and the fake gateway records no start.
- Dependency (cloudflare): `POST /runs/{id}/decision` without the header returns 401, and no signal is sent.
- Web: `POST /runs/{id}/decision` with the header records an `ApprovalDecision` whose `decided_by` is the header email.
- Web: `GET /runs/{id}` in cloudflare mode without the header still returns 200 (read routes are not gated).
- Web: the run page shows "Requested by: a@example.com" and "Approved by: b@example.com" for an approved state, and "Rejected by: ..." for a rejected one.
- Web: the run page shows no "Requested by" line when `requested_by` is None.
- View model: `build_run_view` copies `requested_by` and `decided_by` and sets the approved or rejected label.
- Service: `start` puts `requested_by` into `DailyChartInput`. `submit_decision` and `decide` put `decided_by` into the signal.
- Service: requests with no user (as the CLI sends them) give `None` in both fields.
- Workflow (time-skipping): a query while awaiting the decision returns the input's `requested_by`. After approve with `decided_by`, the final state and `DailyChartResult` carry both values.
- Workflow (time-skipping): a run that expires has `decided_by=None` and keeps `requested_by`.
- Compose: the `web` service environment has `AUTH_MODE` equal to the literal `cloudflare`.

## Out of scope

- The runs list and any memo or search attributes (024). The daily cap (027). Style profiles (028).
- Validating Cloudflare Access JWTs (`Cf-Access-Jwt-Assertion`). We trust the header because the web
  port is only reachable through the tunnel.
- Login or logout pages, sessions, CSRF, and per-user permissions (anyone allowed in can approve any run).
- Any change to `cli.py`, `temporal_gateway.py`, activities, or `compose.dev.yaml`.
- Showing the user in the page header or on the home page.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: Spec for `current_user` (`AUTH_MODE` local or cloudflare; a missing header in cloudflare mode returns 401) on the POST routes only. Requester and approver appear on the run page only, because 024 is not built.
Storage: workflow input plus signal payload, mirrored into `ReviewState` and the query. Both are durable in history, need no DB or memo, and add no new workflow commands. The new fields are optional, so the CLI and old runs keep working.
Prod sets `AUTH_MODE: cloudflare` as a literal in `compose.prod.yaml`. The dev user is `dev@localhost`, and no real emails or domains are used.
Size fits one PR. Next: orchestrator sets `approved` (task is `no`), then builder on `task/026-current-user`.

**builder**: Implemented all spec items with tests first (config AUTH_MODE, current_user dep, workflow/service/view fields, template, compose, docs).
One pyright ignore in config.py for the Literal from env (Pydantic validates it). just lint and just test pass (202 tests).

**verifier**: Ran just lint (ruff check, ruff format --check, pyright strict: 0 errors) and just test (202 passed) on task/026-current-user. No failures. Next: reviewer checks the diff against the checklist.

**reviewer**: Approved. All criteria and listed tests present; workflow changes are model fields (all optional, default None) plus state assignments, no new imports or commands; cloudflare mode 401s on missing/blank header with no dev fallback; only the two POST routes use current_user; no real emails (example.com, dev@localhost only).
Note: the `# pyright: ignore` in config.py is explained but could be dropped by validating via `Settings.model_validate`/a `cast`; optional.

**scribe**: Docs already had the AUTH_MODE note in README.md and docs/runbooks/cloudflare.md, so no doc edits. Pushed task/026-current-user and opened PR #32 against main (body notes the header trust, prod AUTH_MODE=cloudflare, and that `just deploy` is needed after merge).
PR: https://github.com/akonale/ai-trend-pipeline/pull/32 . Status set to pr-open. Next: human reviews and merges (not merged by agents).
