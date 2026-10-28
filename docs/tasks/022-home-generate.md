# 022: Home page with topic box and Generate button

Status: pr-open

## Goal

Serve a home page at `GET /` with a topic text box and a Generate button. Submitting it starts a
chart run through `ChartService.start` and redirects to a minimal run page at `/runs/{wf_id}`. Task
023 fills in the full run page.

## Acceptance criteria

- [ ] `GET /` (`web/routes/home.py`) returns 200 HTML from `home.html`, which extends `base.html`.
      The page has a plain HTML `<form method="post" action="/runs">` with a text input
      `name="topic"` (required, `maxlength="200"`) and a "Generate" submit button. No JavaScript is
      needed for this form.
- [ ] `POST /runs` (`web/routes/runs.py`) reads the `topic` form field, strips whitespace, and checks
      that it is 1 to 200 characters. If it is valid, the route calls
      `ChartService.start(StartChartRequest(topic=topic, unique_id=True))` once and returns a
      `303 See Other` redirect to `/runs/{wf_id}`. If it is blank or too long, the route returns 400
      and re-renders `home.html` with an error message and the typed value, and does not call the
      service. If the service raises `RunAlreadyExists`, the route returns 409 and re-renders
      `home.html` with an error message.
- [ ] Each Generate click gets its own workflow id, so a second run on the same day does not
      collide. `StartChartRequest` gains `unique_id: bool = False`. When `wf_id` is None and
      `unique_id` is True, `ChartService.start` uses `chart-{YYYY-MM-DD}-{suffix}`, where the suffix
      comes from a new injectable `ChartService(..., new_suffix: Callable[[], str])` that defaults to
      `uuid4().hex[:8]`. The current CLI behaviour stays the same: the default id is still
      `daily-chart-{date}`, and an explicit `wf_id` still wins.
- [ ] `GET /runs/{wf_id}` (`web/routes/runs.py`) calls `ChartService.show(ChartRunRef(wf_id=...))`
      and renders `run.html` (extends `base.html`). The page shows the workflow id, the status, and
      the topic when `review` is present, all taken from a `RunSummaryView` model in
      `web/view_models.py`. It also has a link back to `/`. `RunNotFound` returns 404 with a short
      "run not found" page.
- [ ] Both routers are included in `create_app`. Routes get the service only through
      `Depends(get_chart_service)` and never import from `temporalio`. `python-multipart` is added
      with `uv add python-multipart` (FastAPI needs it for `Form`). `just lint` and `just test` pass.

## Files to touch

- `src/ai_trend_pipeline/services/charts.py` (`unique_id` field, `new_suffix` injection)
- `src/ai_trend_pipeline/web/app.py` (include `home` and `runs` routers)
- `src/ai_trend_pipeline/web/routes/home.py` (new)
- `src/ai_trend_pipeline/web/routes/runs.py` (new)
- `src/ai_trend_pipeline/web/view_models.py` (add `HomeView` (error, topic) and `RunSummaryView`)
- `src/ai_trend_pipeline/web/templates/home.html` (new)
- `src/ai_trend_pipeline/web/templates/run.html` (new)
- `src/ai_trend_pipeline/web/templates/run_not_found.html` (new)
- `src/ai_trend_pipeline/web/static/app.css` (form styling only, optional and minimal)
- `tests/test_web.py` (replace `test_root_is_not_routed`, add the tests below)
- `tests/test_chart_service.py` (add the unique-id tests)
- `README.md` (one or two lines in "Web app": open `/`, type a topic, click Generate)
- `pyproject.toml`, `uv.lock` (via `uv add python-multipart` only)

## Tests to write

Web tests use `TestClient(create_app(...))` with `get_chart_service` overridden by a `ChartService`
built on a fake gateway that records its calls. No Temporal.

- `GET /` returns 200 HTML containing a form posting to `/runs`, an input named `topic`, and "Generate".
- `POST /runs` with `topic="  CO2 emissions "` (`follow_redirects=False`) returns 303, `Location` is
  `/runs/<id>`, and the fake gateway was started once with `DailyChartInput.topic == "CO2 emissions"`.
- `POST /runs` with a blank topic returns 400, shows an error, and the gateway is never called.
- `POST /runs` with a 201-character topic returns 400 and the gateway is never called.
- `POST /runs` when the gateway raises `RunAlreadyExists` returns 409 with an error message.
- `GET /runs/{id}` with a fake handle in `awaiting_decision` state shows the id, the status, and the topic.
- `GET /runs/{id}` with a fake handle in `drafting` state (no review) shows the id and "drafting".
- `GET /runs/{id}` when the handle raises `RunNotFound` returns 404.
- Service: `start(StartChartRequest(topic="x", unique_id=True))` with a fixed clock and
  `new_suffix=lambda: "abc12345"` returns `chart-2026-..-..-abc12345`.
- Service: two unique starts with a counter-based `new_suffix` produce different ids.
- Service: `unique_id=True` with an explicit `wf_id` uses that `wf_id` unchanged.

## Out of scope

- Live status polling, PNG preview, caption, critique, Approve/Reject, and the Temporal UI link (023).
- The runs list on the home page (024). `current_user`, auth, and the daily cap (026, 027).
- Starting a run with a blank topic (HN pick) from the web, and per-post options (029).
- Topic matching or suggestions (032). Friendly handling of Temporal or worker being unreachable
  (a 500 is acceptable for now).
- Any change to `cli.py`, `config.py`, workflows, activities, `temporal_gateway.py`, or the CLI's
  default workflow id. No new `Settings` fields.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: Wrote spec for the home page (topic form, `POST /runs` with 303 redirect) plus a minimal `GET /runs/{wf_id}` stub that 023 will grow.
Key decision: web runs get unique ids (`chart-{date}-{suffix}`) through `StartChartRequest.unique_id` so the CLI's `daily-chart-{date}` default does not change.
Builder: `test_root_is_not_routed` in `tests/test_web.py` must be replaced. `python-multipart` is required for `Form`.
Manual "Done when" check (the run shows up in the Temporal UI) is for the human with `just dev` and `just web` running.
Next: human approval (this task is `yes`), then builder on `task/022-home-generate`.

**builder**: Implemented home form, `POST /runs` (303/400/409), minimal `GET /runs/{id}`, `unique_id` + `new_suffix` in `ChartService`; added `python-multipart`.
Replaced `test_root_is_not_routed`; added web and service tests. Ruff, Pyright and the two test files pass; full suite left to the verifier.
Note: tests avoid annotating `httpx.Response` because TestClient returns an `httpx2` type, which Pyright rejects.
Next: verifier.

**verifier**: `just lint` passed (ruff check, ruff format --check, pyright 0 errors); `just test` passed (144 tests).
All checks pass.

**reviewer**: Approved. All criteria and listed tests present; scope matches Files to touch; layers and Pydantic rules hold.
Note: wf_id is not URL-quoted in the redirect (safe today, uuid hex ids).

**scribe**: Moved the Generate guide line to "Web app" in README per reviewer note; pushed the branch.
Opened PR: https://github.com/akonale/ai-trend-pipeline/pull/24
Next: human merges the PR.
