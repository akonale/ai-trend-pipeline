# 023: Run page with live status, preview, Approve / Reject and Temporal UI link

Status: reviewed

## Goal

Grow the minimal `/runs/{wf_id}` page from 022 into the full review page: live status through htmx
polling, the PNG preview, caption, critique, Approve / Reject buttons, and a "View in Temporal" link.
A chart can then go from Generate to posted without the CLI.

## Acceptance criteria

- [ ] **Settings.** `Settings` gains `temporal_ui_url: str = "http://localhost:8233"`, read from
      `TEMPORAL_UI_URL` in `load_settings` (empty counts as unset; a trailing `/` is stripped).
- [ ] **Posting state.** `ReviewState.status` gains `"posting"`. In `DailyChartWorkflow.run`, once an
      approve decision arrives and before the Bluesky activity runs, the state becomes
      `ReviewState(status="posting", review=review, note=decision.note)`. After the post it becomes
      `approved` as today. Rejected and expired runs never go through `posting`. Nothing else in the
      workflow changes (no new activities, signals or timers).
- [ ] **Services.** `ChartService` gains two methods, and `decide` stays unchanged for the CLI:
      - `submit_decision(req: DecideChartRequest) -> None`: same status check as `decide`
        (`NotAwaitingDecision` if the status is not `awaiting_decision`), sends the signal once, and
        does **not** wait for the workflow result.
      - `chart_image(ref: ChartRunRef) -> Path`: returns the run's `review.png_path` resolved to an
        absolute path. It raises a new `ChartImageNotFound(ChartServiceError)` when there is no review
        yet, when the file does not exist, or when the resolved path is not inside the resolved output
        directory (`settings.chart_output_dir`, or `DailyChartInput`'s default `"charts"` when unset).
- [ ] **Routes** (`web/routes/runs.py`, all through `Depends`, no `temporalio` imports):
      - `GET /runs/{wf_id}` renders `run.html`, which includes `partials/run_status.html`.
      - `GET /runs/{wf_id}/status` returns only `partials/run_status.html` (the htmx fragment).
      - `GET /runs/{wf_id}/chart.png` returns the file from `chart_image` as `image/png`
        (`FileResponse`); `ChartImageNotFound` or `RunNotFound` returns 404.
      - `POST /runs/{wf_id}/decision` reads form field `approved` (`"true"` or `"false"`, anything else
        is 400 with no service call), calls `submit_decision` once, and returns a 303 redirect to
        `/runs/{wf_id}?sent=1`. `NotAwaitingDecision` returns 409 and re-renders the run page with an
        error. `RunNotFound` returns 404 on every run route.
- [ ] **View model and template.** A `RunView` in `web/view_models.py`, built by a pure function
      `build_run_view(wf_id, state, temporal_ui_url, sent=False, error="")`, holds everything the
      templates need: id, status, a plain-language status label, topic, topic reason, caption,
      critique passed + reasons, attempts, note, `image_url` (`/runs/{id}/chart.png`, only when there
      is a review), `can_decide` (status `awaiting_decision` and not `sent`), `polling` (status
      `drafting` or `posting`, or `awaiting_decision` with `sent`), and `temporal_url` =
      `{temporal_ui_url}/namespaces/default/workflows/{url-quoted id}`. The partial holds
      `hx-get="/runs/{id}/status{?sent=1 when sent}" hx-trigger="every 2s" hx-swap="outerHTML"` only
      when `polling` is true, so polling stops at `awaiting_decision`, `approved`, `rejected` and
      `expired`. Approve and Reject are plain HTML forms posting to `/runs/{id}/decision`. The
      "View in Temporal" link opens in a new tab (`target="_blank" rel="noopener"`). Templates hold
      only ifs and loops.
- [ ] `README.md` "Web app" section gets one or two lines on reviewing and approving from the run
      page and on `TEMPORAL_UI_URL`. `just lint` and `just test` pass.

## Files to touch

- `src/ai_trend_pipeline/config.py` (`temporal_ui_url`)
- `src/ai_trend_pipeline/daily_chart.py` (`"posting"` status and the one state assignment only)
- `src/ai_trend_pipeline/services/charts.py` (`submit_decision`, `chart_image`, `ChartImageNotFound`)
- `src/ai_trend_pipeline/web/routes/runs.py`
- `src/ai_trend_pipeline/web/view_models.py` (`RunView`, `build_run_view`; drop `RunSummaryView` if unused)
- `src/ai_trend_pipeline/web/templates/run.html`
- `src/ai_trend_pipeline/web/templates/partials/run_status.html` (new)
- `src/ai_trend_pipeline/web/static/app.css` (optional, minimal: preview image width, button spacing)
- `tests/test_web.py`, `tests/test_chart_service.py`, `tests/test_config.py`, `tests/test_daily_chart.py`
- `README.md`

## Tests to write

Web tests use `TestClient(create_app(...))` with `get_chart_service` overridden by a `ChartService` on
the fake gateway from 022. No Temporal. PNG tests write a tiny file under `tmp_path` and set
`chart_output_dir` to it.

- Config: `TEMPORAL_UI_URL` unset gives `http://localhost:8233`; `"http://x:1/"` gives `"http://x:1"`.
- Workflow (time-skipping): after approve, a query while the (blocked) post activity runs returns
  `posting`; the run then completes `approved`.
- Workflow (time-skipping): reject goes straight to `rejected`, never `posting`.
- Service: `submit_decision` while awaiting sends one signal and never calls `result()`.
- Service: `submit_decision` when not awaiting raises `NotAwaitingDecision` and sends nothing.
- Service: `chart_image` returns the path for a PNG inside the output dir.
- Service: `chart_image` raises `ChartImageNotFound` for no review, a missing file, and a path outside the output dir.
- View model: `build_run_view` sets `polling`/`can_decide` correctly for each status and for `sent=True`.
- View model: `temporal_url` is `{base}/namespaces/default/workflows/{id}` with the id URL-quoted.
- Web: `GET /runs/{id}` while drafting has the htmx polling attributes and no Approve button.
- Web: `GET /runs/{id}` while awaiting shows the image tag, caption, critique reasons, Approve and Reject forms, the Temporal link, and no polling attribute.
- Web: `GET /runs/{id}` for `approved`, `rejected`, `expired` shows the status label and no polling or buttons.
- Web: `GET /runs/{id}/status` returns the fragment only (no `<html>`).
- Web: `GET /runs/{id}/chart.png` returns 200 `image/png` with the file bytes; missing file returns 404.
- Web: `POST /runs/{id}/decision` with `approved=true` returns 303 to `/runs/{id}?sent=1` and the fake recorded one approve decision; `approved=false` records a reject.
- Web: `POST /runs/{id}/decision` with `approved=maybe` returns 400 and no signal is sent.
- Web: `POST /runs/{id}/decision` when not awaiting returns 409 with an error message.
- Web: `GET /runs/{id}?sent=1` while still awaiting hides the buttons and keeps polling.
- Web: every run route returns 404 when the handle raises `RunNotFound`.

## Out of scope

- Runs list on the home page (024). Auth, `current_user`, CSRF and the daily cap (025 to 027).
- A free-text decision note in the form, refine, version history, run timeline (030, 031, 033).
- Linking to the Bluesky post or showing its URI (would need the result in the query state).
- Any change to `cli.py`, `temporal_gateway.py`, activities, or the CLI `decide` behaviour.
- Server-sent events or websockets; any JavaScript beyond the existing htmx file.
- Friendly handling of Temporal or the worker being down (a 500 is fine).

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: Wrote spec for the full run page: htmx status fragment, PNG route, decision POST, Temporal UI link via `TEMPORAL_UI_URL` (default `http://localhost:8233`, the compose port).
Key decisions: new `submit_decision` (no wait, since posting can take minutes) and a `posting` status in the workflow so the page knows a post is in flight; `?sent=1` covers the gap before the signal is processed.
`chart_image` confines served files to the output dir. `tests/test_config.py` defaults/env-map tests need the new field.
Size is one PR but at the upper end; if it grows, split the workflow `posting` change into its own task first.
Next: orchestrator sets `approved` (task is `no`), then builder on `task/023-run-page`.

**builder**: Implemented all spec items on `task/023-run-page`; tests written first, lint, pyright and full pytest pass.
Old 022 run-page tests now assert the status label ("Drafting the chart") instead of the raw status string.
Decision form is two plain forms (hidden `approved` field), one per button. Missing/blank `approved` is 400.

**verifier**: Ran `just lint` (ruff check, ruff format --check, pyright: clean) and `just test` (169 passed). All checks pass. Next: reviewer.

**reviewer**: Approved. Diff matches spec and Files to touch; workflow change is one state assignment, no I/O added; services/routes/view model layering holds; README updated.
Note: `RunView.critique_reasons: list[str] = []` is fine in Pydantic (copied per instance).
