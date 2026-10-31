# Hotfix 002: Stories home, chart step 1 at /new

Status: built

Goal: make `/stories` the landing page and move the chart flow's topic picker to `/new`.
Acceptance: `GET /` returns 307 to `/stories`; `GET /new` renders the topic picker with its carry and redirect targets.
Acceptance: step 2 without a topic redirects to `/new`; nav, brand, "Make another chart" and run back links use `/new` or `/stories`.
Acceptance: pending story rows link to `/candidates/<candidate-id>`; other rows keep `/stories/<id>`.
Files: `web/routes/home.py`, `web/view_models.py`, `web/templates/`, `tests/test_web_*.py`.

## Handoff

- Built the root redirect, `/new` step 1, `detail_href` on story rows, and doc/test updates.
- Also changed `new_style.html`'s Back button to `/new` so the step 2 back path does not land on Stories.
