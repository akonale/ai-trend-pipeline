# 017: Fix: use tool_choice auto for the LLM activities

Status: built

## Goal

A live run failed with Anthropic 400 `tool_choice: type "tool" and "any" are not supported for this model` on `claude-sonnet-5-5`. Switch the three LLM activities (topic, chart spec, critique) to `tool_choice={"type": "auto"}`. Each system prompt must then tell the model to answer by calling its tool. Keep the existing non-retryable "no tool call" error.

## Acceptance criteria

- [ ] `src/ai_trend_pipeline/topic.py`, `chart_spec.py` and `critique.py` each send `tool_choice={"type": "auto"}` to `messages.create`. No `"type": "tool"` or `"type": "any"` remains in `src/`.
- [ ] Each module's `SYSTEM_PROMPT` ends with a sentence that tells the model to respond only by calling the `<TOOL_NAME>` tool. Build it from the module's `TOOL_NAME` constant (for example `f"Respond only by calling the {TOOL_NAME} tool."`) so the name cannot drift. `TOOL_NAME` must be defined before `SYSTEM_PROMPT`, which is already the case.
- [ ] When the response has no matching `ToolUseBlock`, each activity still raises `ApplicationError(..., non_retryable=True)` with the message `Model response contained no <TOOL_NAME> tool call`. This behaviour is unchanged.
- [ ] Nothing else changes: tool names, schemas, models, max_tokens, output parsing and validation errors (`InvalidChartDraft`, `InvalidChartCritique`).
- [ ] `just lint` and `just test` pass with no live API calls.

## Files to touch

- `src/ai_trend_pipeline/topic.py` (edit line 64 and `SYSTEM_PROMPT`)
- `src/ai_trend_pipeline/chart_spec.py` (edit line 91 and `SYSTEM_PROMPT`)
- `src/ai_trend_pipeline/critique.py` (edit line 90 and `SYSTEM_PROMPT`)
- `tests/test_topic.py`, `tests/test_chart_spec.py`, `tests/test_critique.py` (edit)
- `docs/mvp-plan.md` (row for 017 already added by the planner; tick nothing)

Existing fixtures under `tests/fixtures/` (`llm_pick_topic_response.json`, `llm_pick_topic_no_tool.json`, `llm_chart_spec_response.json`, `llm_critique_pass.json`, `llm_critique_fail.json`) already cover both the tool-call path and the no-tool path, so no fixture changes are expected. Only add a fixture if a test truly needs one.

## Tests to write

Make these changes first (tests go first) in each of the three test files:
- `test_request_shape` (edit): assert `body["tool_choice"] == {"type": "auto"}` in place of the forced-tool assertion.
- `test_system_prompt_mentions_tool` (new): the captured request's `body["system"]` contains `TOOL_NAME` and the word "calling", and so does the module's `SYSTEM_PROMPT`.
- `test_no_tool_use_raises_non_retryable` (existing, unchanged): it must still pass with the `llm_pick_topic_no_tool.json` fixture.

## Out of scope

- Changing `DEFAULT_MODEL`, adding a model fallback, or retrying when the model makes no tool call.
- Making the no-tool error retryable, or changing how `chart_loop.py` or `daily_chart.py` handle it.
- Rewording the prompts beyond adding the single tool instruction sentence.
- Any change to `bluesky.py`, `render.py`, `owid.py`, `hn.py`, the worker, the CLI, `README.md`, `CLAUDE.md`, CI or `infra/`.
- Live Anthropic calls in tests.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->
**planner**: Wrote the 017 bug-fix spec and set it to `planned`. The fix: send `tool_choice` auto in topic/chart_spec/critique, add a "Respond only by calling the {TOOL_NAME} tool." sentence to each system prompt, and keep the non-retryable no-tool error.
**planner**: I numbered it 017 because 012–016 are already reserved in the plan, and added a "Fixes" row (approval `no`) to `docs/mvp-plan.md`. Existing fixtures should be enough. Risk: with auto, the model may still answer in text, and that now surfaces as the non-retryable error, which is the intended behaviour. Next: the orchestrator approves, then the builder works on branch `task/017-tool-choice-auto`.
**builder**: Switched topic/chart_spec/critique to tool_choice auto and appended the "Respond only by calling the {TOOL_NAME} tool." sentence to each SYSTEM_PROMPT. Edited test_request_shape and added test_system_prompt_mentions_tool in the three test files. just lint and just test pass (103). Next: verifier.
