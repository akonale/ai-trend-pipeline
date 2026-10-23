# 018: Fix: topic picker chooses from the OWID catalog

Status: reviewed

## Goal

`pick_topic` picks any topic, but `owid.CATALOG` has six datasets, so a live run failed with a non-retryable `NoMatchingDataset` on "Rail travel / passenger rail usage by country". The picker must instead get the catalog titles in its prompt and return the one that best fits the day's news, so `fetch_owid_dataset` always finds a dataset.

## Acceptance criteria

- [x] `topic.py` builds `SYSTEM_PROMPT` from `owid.CATALOG`. The prompt lists every catalog `title` and tells the model to choose exactly one that best fits the stories, to copy it verbatim into `topic`, and to say in `reason` how it links to the news. The prompt still ends with the 017 sentence `f"Respond only by calling the {TOOL_NAME} tool."`, because `tool_choice` stays `{"type": "auto"}`.
- [x] The tool `input_schema` sent to the API restricts `topic` to the catalog titles (`"enum": [titles in CATALOG order]`). The `PickTopicOutput` model itself stays `topic: str`, `reason: str`.
- [x] After the tool call is parsed, `pick_topic` accepts the topic only if it equals a catalog title after `strip()` and case-folding. It then returns the canonical catalog title. Any other topic raises `ApplicationError` with `type="TopicNotInCatalog"` and `non_retryable=False`, so `LLM_RETRY_POLICY` (3 attempts) gives the model another try. The error message includes the rejected topic. Define the type as a constant `NOT_IN_CATALOG_ERROR_TYPE` in `topic.py`.
- [x] `owid.match_dataset` first checks for an exact case-insensitive title match, then falls back to the existing keyword match. This is needed because "Share of new cars sold that are electric" matches none of its own keywords today. `match_dataset(d.title)` returns `d` for every dataset in `CATALOG`. The catalog entries themselves (slugs, titles, keywords) do not change.
- [x] The existing no-tool path is unchanged: it raises the non-retryable `Model response contained no pick_topic tool call`. `just lint` and `just test` pass with no live calls.

## Files to touch

- `src/ai_trend_pipeline/topic.py` (prompt, tool schema, catalog check)
- `src/ai_trend_pipeline/owid.py` (`match_dataset` title check only)
- `tests/test_topic.py`, `tests/test_owid.py` (edit)
- `tests/fixtures/llm_pick_topic_response.json` (edit: set `topic` to a catalog title, for example "Solar energy consumption")
- `tests/fixtures/llm_pick_topic_off_catalog.json` (new: same shape, `topic` = "Rail travel / passenger rail usage by country")
- `README.md` (Topic picking section: one sentence saying the picker chooses from `CATALOG`)

## Tests to write

- `test_system_prompt_lists_catalog` (test_topic): every `CATALOG` title appears in `SYSTEM_PROMPT` and in the captured request's `system`. The `TOOL_NAME` and "calling" assertion stays.
- `test_tool_schema_enum_is_catalog` (test_topic): the captured `tools[0]["input_schema"]["properties"]["topic"]["enum"]` equals the catalog titles in order.
- `test_pick_outside_catalog_raises_retryable` (test_topic): the off-catalog fixture raises `ApplicationError` with type `TopicNotInCatalog`, `non_retryable is False`, and the rejected topic in its message.
- `test_pick_topic_canonicalises_title` (test_topic): if the tool input topic differs from a catalog title only in case or surrounding spaces, the activity returns the exact catalog title. Build this response in the test from the main fixture, with no new file.
- `test_match_dataset_every_title` (test_owid): parametrised over `CATALOG`, `match_dataset(d.title) == d`.
- The existing `test_match_dataset` cases and `test_pick_topic_returns_topic_and_reason` (now using the edited fixture) still pass.

## Out of scope

- Growing `CATALOG` or editing its keywords. A possible follow-up task is "grow catalog", but it is not specced here.
- Changing `fetch_owid_dataset` errors, `daily_chart.py`, `chart_loop.py`, the CLI, the worker, `DEFAULT_MODEL`, or `tool_choice`.
- Manual topic override (task 019).
- Live Anthropic or OWID calls in tests.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->
**planner**: Wrote the spec and set it to `planned`. Approval is `no`, and the row is added under Fixes in `docs/mvp-plan.md`. Branch: `task/018-topic-from-catalog`.
**planner**: Gotcha: the title "Share of new cars sold that are electric" matches none of its keywords, so `match_dataset` gets an exact-title check first. Without it the fix would still fail for that dataset.
**planner**: An off-catalog pick is retryable (`TopicNotInCatalog`) so the model gets another try under `LLM_RETRY_POLICY`. The current main fixture's topic is off-catalog, so it must be edited. Next: orchestrator approves, then the builder.
**builder**: Implemented per spec: catalog-based prompt, enum in tool schema, TopicNotInCatalog retryable check with canonical title, title-first match_dataset, tests/fixtures/README updated. `just lint` and `just test` pass (113). Next: verifier.
**verifier**: `just lint` (ruff, format, pyright 0 errors) and `just test` (113 passed) pass. Spec tests and symbols confirmed present. Set verified. Next: reviewer.
**reviewer**: Approved. All criteria and listed tests present; retryable TopicNotInCatalog, enum schema, and title-first match_dataset match spec. No scope creep, no secrets, no live calls. Next: scribe.
