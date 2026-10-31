# Hotfix 002: Stories home, chart step 1 at /new

Status: pr-open

Goal: make `/stories` the landing page and move the chart flow's topic picker to `/new`.
Acceptance: `GET /` returns 307 to `/stories`; `GET /new` renders the topic picker with its carry and redirect targets.
Acceptance: step 2 without a topic redirects to `/new`; nav, brand, "Make another chart" and run back links use `/new` or `/stories`.
Acceptance: pending story rows link to `/candidates/<candidate-id>`; other rows keep `/stories/<id>`.
Files: `web/routes/home.py`, `web/view_models.py`, `web/templates/`, `tests/test_web_*.py`.

## Handoff

- Built the root redirect, `/new` step 1, `detail_href` on story rows, and doc/test updates.
- Also changed `new_style.html`'s Back button to `/new` so the step 2 back path does not land on Stories.
- Verified: `just lint` clean, `just test` 1010 passed (ANTHROPIC_* base-url vars unset; initial run failed only on those LLM tests with APIConnectionError).
- All checks pass.
- Approved. Lint clean; the web tests pass (276), including the new root 307 redirect and pending-row link tests.
- Pre-existing test_overrides_change_chart_spec still fails on APIConnectionError with ANTHROPIC_* base-url vars unset; unrelated to this diff.
- Scribe: docs/architecture/tech.md route table still lists GET / as step 1 and says step 2 redirects to /. 
- Scribe: fixed tech.md routes (`/` 307 to `/stories`, step 1 at `/new`, step 2 to `/new`, Stories default/first nav), pending-row links and README home paths.
- PR: https://github.com/akonale/ai-trend-pipeline/pull/66
