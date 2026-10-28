# 027: Per-person daily cap

Status: built

## Goal

Limit each signed-in person to `DAILY_CHART_CAP` (default 10) chart runs started from the web per UTC
day. Generate number 11 leaves the person on the home page with a friendly "limit reached" message
and starts no run.

## Decisions

- **How "today" is counted.** Use Temporal itself, so there is no database and no new counter.
  When `requested_by` is set, the gateway's `start` also sets a **memo** `{"requested_by": <email>}`
  on the workflow. A new gateway method lists runs with the visibility API
  (`client.list_workflows`) using the query
  `WorkflowType = 'DailyChartWorkflow' AND StartTime >= '<UTC midnight, RFC3339 Z>'`. It then counts
  the runs whose memo `requested_by` matches. A memo needs no search-attribute registration on the dev
  server or in prod. The workflow code does not change, so determinism is unaffected. The service
  stays testable with a fake gateway. Prod visibility lives in Postgres, so counts survive web, worker
  and server restarts. The dev Temporal server is in-memory, so restarting `just dev` resets dev
  counts, which is fine. Runs started before this task have no memo and don't count.
- **Timezone and reset.** UTC. "Today" starts at 00:00 UTC on the service clock's date (the existing
  injectable `now`, converted to UTC). The message says it resets at 00:00 UTC.
- **What counts.** Every run the person started today, in any state: running, awaiting a decision,
  approved, rejected, expired, failed, terminated or cancelled. Each one costs LLM calls.
- **Who is capped.** Only requests with `requested_by` set, which means web Generate. The CLI sends
  no user, so `just chart` and any operator runs are not capped and never call the count.
- **Races.** The check and the start are not atomic, and visibility can lag by a moment. Two Generate
  clicks at the same moment may let one run through over the cap (11 instead of 10). We accept this:
  the cap is a cost guard for two trusted users, not a security boundary.
- **Dev.** With `AUTH_MODE=local` everyone is `dev@localhost`, so everyone on the dev server shares
  one cap.

## Acceptance criteria

- [ ] **Settings.** `Settings.daily_chart_cap: int = Field(default=10, ge=1)`. `load_settings` reads
      `DAILY_CHART_CAP`. Unset or empty gives 10. The value is parsed with `int()` and then checked by
      Pydantic. `0`, `-1`, `abc` and `2.5` raise `ValueError` (a Pydantic `ValidationError` is fine),
      so the web app and worker fail at startup. Add no `# pyright: ignore`.
- [ ] **Service** (`services/charts.py`).
      - `ChartRunGateway` gains `async def count_started_since(self, requested_by: str, since: datetime) -> int`.
      - New `DailyCapReached(ChartServiceError)` with attributes `cap: int`. Its message is exactly
        `You've reached today's limit of {cap} charts. It resets at 00:00 UTC.`
      - `ChartService.start`: if `req.requested_by` is set, it computes `since` as UTC midnight of
        `self._now()` and calls `count_started_since(req.requested_by, since)`. If the count is
        `>= settings.daily_chart_cap`, it raises `DailyCapReached` and does not call `gateway.start`.
        With no `requested_by`, nothing changes and the count is not called.
- [ ] **Gateway** (`services/temporal_gateway.py`). `start` passes `memo={"requested_by": ...}` only
      when `data.requested_by` is not None (otherwise no memo, as today). `count_started_since` runs
      the visibility query above, built by a small pure function (for example
      `started_since_query(since) -> str`). It iterates every page and counts entries whose memo
      `requested_by` equals the argument. Read the memo with `memo_value(..., type_hint=str)` or
      similar so Pyright strict is happy. Errors from Temporal propagate; they are not swallowed.
- [ ] **Route** (`web/routes/runs.py`). `POST /runs` catches `DailyCapReached` and re-renders
      `home.html` with `HomeView(error=str(exc), topic=topic)` and status **429**. The existing `.error`
      paragraph (`role="alert"`, task 035 styles) shows it. The typed topic is kept and no run is
      started. The route still calls exactly one service method. Templates, CSS and JS are unchanged.
- [ ] **Prod compose.** The `web` service gets `DAILY_CHART_CAP: ${DAILY_CHART_CAP:-10}`.
- [ ] **Docs.** One or two lines in `README.md` (next to the `AUTH_MODE` note) and in the
      `.env.prod` keys list in `docs/runbooks/deploy.md`: what `DAILY_CHART_CAP` does, the default of
      10, the UTC midnight reset, that every started run counts, and that the CLI is not capped.
      `just lint` and `just test` pass.

## Files to touch

- `src/ai_trend_pipeline/config.py`
- `src/ai_trend_pipeline/services/charts.py`
- `src/ai_trend_pipeline/services/temporal_gateway.py`
- `src/ai_trend_pipeline/web/routes/runs.py`
- `infra/compose.prod.yaml` (one env line on `web`)
- `tests/test_config.py`, `tests/test_chart_service.py`, `tests/test_web.py`, `tests/test_compose_prod.py`
- `tests/test_temporal_gateway.py` (new, pure helper tests only)
- `README.md`, `docs/runbooks/deploy.md`

The existing fakes (`FakeGateway` in `test_chart_service.py`, plus `FakeGateway` and `RecGateway` in
`test_web.py`) must gain `count_started_since` so they still satisfy the protocol. They default to a
count of 0, so the existing tests pass unchanged.

## Tests to write

No live Temporal. The service and route tests use fake gateways.

- Config: unset or empty `DAILY_CHART_CAP` gives 10, and `"3"` gives 3. `"0"`, `"-1"`, `"abc"` and `"2.5"` raise `ValueError`. `Settings()` defaults to 10.
- Service, under cap: count 9 with cap 10 starts the run. The fake receives `requested_by` and `since == 2026-05-01T00:00Z` for `now = 2026-05-01T12:00Z`.
- Service, at cap: count 10 with cap 10 raises `DailyCapReached` (`cap == 10`, exact message), and `gateway.start` is not called.
- Service, other person unaffected: the fake counts per email. `a@` at the cap is refused while `b@` starts.
- Service, day rollover: a fake that counts its own starts by start time. With cap 2, a third start at 23:59 UTC is refused. With the clock moved to 00:00:01 UTC the next day, a start succeeds and `since` is the new midnight.
- Service: a non-UTC aware `now` (for example 01:30 at +02:00) gives `since` equal to the previous day's 00:00 UTC.
- Service: a request with no `requested_by` (CLI) never calls `count_started_since` and is never capped.
- Gateway helper: `started_since_query(datetime(2026, 5, 1, tzinfo=UTC))` returns `WorkflowType = 'DailyChartWorkflow' AND StartTime >= '2026-05-01T00:00:00Z'`.
- Route: with a fake gateway count at the cap, `POST /runs` returns 429. The body contains "reached today's limit" and "00:00 UTC", it is inside `role="alert"`, the topic is kept in the input, and no start is recorded.
- Route: blank topic is still 400 and does not call the count. In cloudflare mode, a missing header is still 401 and does not call the count.
- Compose: the `web` environment has `DAILY_CHART_CAP` equal to `${DAILY_CHART_CAP:-10}`.

## Out of scope

- Any change to `daily_chart.py`, activities, the worker, `cli.py` or `compose.dev.yaml`.
- Search attributes, a database or Redis, or any atomic or locked counter.
- Showing "N of 10 left" on the home page, per-user caps or admin overrides.
- Capping refine (030) or decisions. Only `POST /runs` is capped.
- The runs list (024), which may reuse the memo later.
- Template, CSS or JavaScript changes.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: Spec for the per-person daily cap (`DAILY_CHART_CAP`, default 10, >=1, invalid values raise at startup). The check lives in `ChartService.start` and raises `DailyCapReached`. The route renders it as a 429 on home with the existing `.error` alert.
Count: the gateway sets a memo `requested_by` on start and counts today's runs (UTC midnight) through the visibility list API. No DB, no search attributes, no workflow change. Every started run counts. The CLI (no user) is uncapped. The race that lets one extra run through is accepted. Dev shares one cap.
Gateway list code has no live test, only the pure query-helper test. Builder: check it once by hand against `just dev`.
Next: orchestrator sets `approved` (task is `no`), then builder on `task/027-daily-cap`.

**builder**: Implemented cap (config, DailyCapReached in ChartService.start, gateway memo + visibility count, 429 route, compose, docs) tests-first; fakes updated.
Live check against a throwaway dev server (port 17233, now killed): PASSED. 3 runs for a@ counted 3, 2 for b@ counted 2, unknown c@ 0, runs without memo ignored, a since boundary in the future gave 0. No bugs found.
