# 006: Activity: LLM picks a chartable topic

Status: pr-open

## Goal

Add a Temporal activity that sends the Hacker News stories from task 005 to Claude using the Anthropic Python SDK. Claude picks one topic that can be charted with public data, and the activity returns the topic and the reason as a typed Pydantic model. It is tested only against a recorded Messages API response, with no live LLM calls.

## Acceptance criteria

- [ ] `anthropic` is added as a runtime dependency with `uv add anthropic`, so both `pyproject.toml` and `uv.lock` change. No other dependencies are added. Tests reuse `httpx.MockTransport`; no extra mock library.
- [x] `src/ai_trend_pipeline/topic.py` defines these Pydantic models:
  - `PickTopicInput(stories: list[HNStory])`, where `stories` has `Field(min_length=1)` and `HNStory` is imported from `ai_trend_pipeline.hn`.
  - `PickTopicOutput(topic: str, reason: str)`, with both fields `Field(min_length=1)`. Its `model_json_schema()` is also used as the tool `input_schema`.
  - It also defines module constants `DEFAULT_MODEL = "claude-sonnet-5-5"`, `MAX_TOKENS = 1024`, `TOOL_NAME = "pick_topic"` and a short `SYSTEM_PROMPT`. The prompt tells Claude to pick one topic from the stories that could be charted with public country or time-series data (for example from Our World in Data), and to explain why in one or two sentences.
- [x] The activity is a method on a class, so the client can be injected. It looks like this: `class TopicActivities: def __init__(self, client: anthropic.AsyncAnthropic, model: str = DEFAULT_MODEL)` and `@activity.defn async def pick_topic(self, data: PickTopicInput) -> PickTopicOutput`. The method does the following:
  1. Makes exactly one call to `client.messages.create`, with `model`, `max_tokens=MAX_TOKENS`, `system=SYSTEM_PROMPT`, a single tool named `TOOL_NAME`, and `tool_choice={"type": "tool", "name": TOOL_NAME}` so the output is structured.
  2. The user message lists each story's id, title, url (or "none"), score and comment count, one per line.
  3. Finds the `tool_use` block named `TOOL_NAME` in `response.content` and validates its `input` with `PickTopicOutput.model_validate`. There is no hand-parsing of text and no `Any`. Pyright strict passes with no `# type: ignore`.
  4. If there is no such block, it raises `temporalio.exceptions.ApplicationError(..., non_retryable=True)`. A bad tool input lets `pydantic.ValidationError` propagate.
- [x] Secrets: the API key comes only from the environment. `topic.py` provides `make_anthropic_client() -> anthropic.AsyncAnthropic`, which reads `ANTHROPIC_API_KEY` from `os.environ`, raises `RuntimeError` naming the variable if it is unset or empty, and sets a 60s timeout. No key, real or fake-looking (`sk-ant-...`), is committed. Tests use the literal `"test-key"`. API errors (for example a 500) propagate so Temporal's retry policy handles them. The activity has no side effects, so it is safe to re-run (idempotent); Temporal records its result, so workflow replay does not repeat the call.
- [x] The fixture `tests/fixtures/llm_pick_topic_response.json` is a full Messages API response body in the real shape: `id`, `type: "message"`, `role`, `model`, `content` with one short `text` block followed by one `tool_use` block named `pick_topic` with `{"topic": ..., "reason": ...}`, `stop_reason: "tool_use"` and `usage`. It is written by hand, not recorded by a script. A second fixture, `tests/fixtures/llm_pick_topic_no_tool.json`, has only a `text` block and `stop_reason: "end_turn"`.
- [x] `just lint` and `just test` pass, and no test makes a network call. The mock transport handler fails the test on any URL other than `POST /v1/messages`. `README.md` gets one or two lines noting the topic activity (module `ai_trend_pipeline.topic`) and that it needs `ANTHROPIC_API_KEY` in the environment.

## Files to touch

- `pyproject.toml` (via `uv add anthropic`)
- `uv.lock` (via `uv add anthropic`)
- `src/ai_trend_pipeline/topic.py` (new)
- `tests/test_topic.py` (new)
- `tests/fixtures/llm_pick_topic_response.json` (new)
- `tests/fixtures/llm_pick_topic_no_tool.json` (new)
- `README.md`

## Tests to write

All tests build `anthropic.AsyncAnthropic(api_key="test-key", max_retries=0, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))`, where the handler serves a fixture, and run the activity through `temporalio.testing.ActivityEnvironment`. Input stories are 2–3 `HNStory` objects built inline.

- `test_pick_topic_returns_topic_and_reason`: the result is a `PickTopicOutput` whose `topic` and `reason` equal the fixture's tool input.
- `test_request_shape`: the handler captures the one request body. It checks `model == DEFAULT_MODEL`, `max_tokens == MAX_TOKENS`, that the tool is named `pick_topic`, that `tool_choice` forces that tool, and that every input story title appears in the user message. It also checks that exactly one request was made.
- `test_custom_model_is_sent`: `TopicActivities(client, model="other-model")` sends `model == "other-model"`.
- `test_no_tool_use_raises_non_retryable`: with the no-tool fixture, the activity raises `ApplicationError` with `non_retryable is True`.
- `test_api_error_propagates`: if the handler returns 500 with an Anthropic-style error body, the activity raises `anthropic.APIStatusError`.
- `test_input_requires_stories`: `PickTopicInput(stories=[])` raises `pydantic.ValidationError`.
- `test_make_anthropic_client_reads_env`: with `monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")`, the client's `api_key == "test-key"` and no request is sent. With the variable deleted, the factory raises `RuntimeError`.

## Out of scope

- Registering the activity in `worker.py`, or any workflow that calls it (task 010).
- Fetching OWID data, matching topics to datasets, Vega-Lite, critique/retry loops or Bluesky (tasks 007 to 011).
- Prompt versioning, storing prompts or responses, caching, or tracking token usage and cost (task 013 or later).
- Any test or script that calls the live Anthropic API, including a fixture-recording script.
- Reading the model id from an env var or config file. A constructor argument plus a module default is enough for now.
- Changes to `hn.py`, `CLAUDE.md`, `docs/mvp-plan.md`, `justfile`, `infra/`, CI, or the hello workflow.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->
**planner**: Wrote the 006 spec and set Status to `planned`. It fits in one PR (1 module, 1 test file, 2 fixtures, 1 new dependency), so no split is needed. Next: the orchestrator sets `approved` (approval = no), then the builder works on branch `task/006-llm-pick-topic`.
**planner**: Design: a forced tool call (`tool_choice`) gives structured output, which is validated with Pydantic. The `AsyncAnthropic` client is injected with an `httpx.MockTransport` `http_client`, so the real SDK parses the recorded fixture and no network call is made.
**planner**: Model id `claude-sonnet-5-5` came from the orchestrator's brief. The builder should keep it in one constant. The key is read only from `ANTHROPIC_API_KEY`, and tests use the literal `"test-key"`.
**builder**: Implemented topic.py, tests, 2 fixtures, README, `uv add anthropic`; lint and tests/test_topic.py pass. Next: verifier.
**builder**: Deviation: the installed anthropic SDK rejects `httpx.AsyncClient` (it uses `httpx2`, pulled in transitively, nothing extra added), so tests use `httpx2.AsyncClient`/`httpx2.MockTransport` instead of `httpx`.
**verifier**: just lint and just test (26 tests) pass. All checks pass.
**reviewer**: Approved. All criteria and listed tests present; no secrets (only "test-key"), no live network (mock handler rejects anything but POST /v1/messages); activity has no workflow code and is side-effect free.
**reviewer**: httpx2 deviation justified: uv.lock shows anthropic 2.0.0 depends on `httpx2`; pyproject.toml adds only `anthropic>=2.0.0` (httpx2 not a direct dep).
**reviewer**: Notes: (1) tests import transitive `httpx2` directly, so an anthropic upgrade could break them; consider declaring it as a dev dep later. (2) Spec text still says `httpx.MockTransport`; update for future specs. (3) Task 006 file edits (spec + log) are uncommitted/arrive via this branch; scribe should commit them.
**scribe**: Ticked criteria 2 to 6 (verifier and reviewer confirmed them). Criterion 1 stays open: tests use `httpx2.MockTransport` instead of `httpx.MockTransport` (see builder deviation). Opening PR. Next: CI on the PR's latest commit.
