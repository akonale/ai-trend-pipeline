# 005: Activity: fetch Hacker News top stories

Status: pr-open

## Goal

Add a Temporal activity that fetches the current Hacker News top stories from the official Firebase API (`https://hacker-news.firebaseio.com/v0/`) and returns them as typed Pydantic models. It is tested only against a recorded fixture, with no live HTTP.

## Acceptance criteria

- [x] `httpx` is added as a runtime dependency with `uv add httpx`, so both `pyproject.toml` and `uv.lock` change. No other dependencies are added. `httpx.MockTransport` ships with httpx, so tests need no extra mock library.
- [x] `src/ai_trend_pipeline/hn.py` defines these Pydantic models:
  - `HNTopStoriesInput(limit: int = 30)`, with `limit` constrained to 1..100 using `Field(ge=1, le=100)`.
  - `HNStory`, with fields `id: int`, `title: str`, `url: str | None` (Ask HN posts have no url), `score: int`, `by: str`, `time: datetime` (timezone-aware UTC, parsed from the unix seconds in the API) and `comments: int` (taken from the API's `descendants` field, default 0).
  - `HNTopStoriesOutput(stories: list[HNStory])`.
  - Raw API JSON is validated through Pydantic (`TypeAdapter(list[int])` and a private raw-item model), so no `Any` leaks and Pyright strict passes with no `# type: ignore`.
- [x] The activity is a method on a class, so the HTTP client can be injected. It looks like this: `class HNActivities: def __init__(self, client: httpx.AsyncClient)` and `@activity.defn async def fetch_hn_top_stories(self, data: HNTopStoriesInput) -> HNTopStoriesOutput`. The method does the following:
  1. GETs `topstories.json` and takes the first `limit` ids.
  2. GETs `item/{id}.json` for those ids only, concurrently (`asyncio.gather`).
  3. Keeps the items in topstories order.
  4. Drops any item that is `null`, is not `type == "story"`, is `dead` or `deleted`, or has no `title`.
- [x] Any non-2xx response raises an error (`response.raise_for_status()`), so Temporal's retry policy handles it. The activity is read-only, so it is idempotent. The base URL is a module constant. `hn.py` also provides a small factory, `make_hn_client() -> httpx.AsyncClient`, that uses that base URL and a 10s timeout. Nothing in `src/` reads env vars or secrets for this task.
- [x] The fixture `tests/fixtures/hn_top_stories.json` has the shape `{"topstories": [ids...], "items": {"<id>": item-or-null}}`. It holds at least 6 ids and covers each of these cases at least once:
  - a normal story with a url
  - an Ask HN story with no url and no `descendants`
  - a `job` item
  - a `null` item
  - a `dead` story
  - The real API shape is used, trimmed by hand. No recording script.
- [x] `just lint` and `just test` pass, and no test makes a network call. The mock transport handler fails the test on any unexpected URL. `README.md` gets one or two lines noting that the HN fetch activity exists (module `ai_trend_pipeline.hn`).

## Files to touch

- `pyproject.toml` (via `uv add httpx`)
- `uv.lock` (via `uv add httpx`)
- `src/ai_trend_pipeline/hn.py` (new)
- `tests/test_hn.py` (new)
- `tests/fixtures/hn_top_stories.json` (new; create `tests/fixtures/`)
- `README.md`

## Tests to write

All tests use a `httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=...)` that serves the fixture. Run the activity through `temporalio.testing.ActivityEnvironment`.

- `test_fetch_returns_typed_stories`: the result is an `HNTopStoriesOutput`. Every entry is an `HNStory`, and the ids and order match the expected filtered list from the fixture.
- `test_fetch_filters_non_stories`: the job, null and dead items are not in the result.
- `test_story_without_url_and_descendants`: the Ask HN story has `url is None` and `comments == 0`.
- `test_time_is_utc_datetime`: `time` is tz-aware UTC and equals the fixture's unix timestamp.
- `test_limit_caps_item_requests`: with `limit=2`, the handler sees exactly 1 topstories request plus 2 item requests, and no more.
- `test_http_error_raises`: if the handler returns 500 for `topstories.json`, the activity raises `httpx.HTTPStatusError`.
- `test_input_limit_bounds`: `HNTopStoriesInput(limit=0)` and `HNTopStoriesInput(limit=101)` raise `pydantic.ValidationError`.
- `test_make_hn_client_base_url`: `make_hn_client()` has the HN base URL. This only builds the client and sends no request.

## Out of scope

- Registering the activity in `worker.py`, or any workflow that calls it. Task 010 does that wiring.
- Topic selection, LLM calls, OWID, rendering or Bluesky (tasks 006 to 011).
- Fetching comments, the Algolia API, other HN lists (new/best/ask), caching, persistence or rate limiting.
- Any test or script that makes a live HTTP call, including a fixture-recording script.
- Changes to `CLAUDE.md`, `docs/mvp-plan.md`, `justfile`, `infra/`, CI, or the hello workflow.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->
**planner**: Wrote the 005 spec and set Status to `planned`. It fits in one PR (1 module, 1 test file, 1 fixture, 1 new dependency), so no split is needed. Next: the orchestrator sets `approved` (approval = no), then the builder works on branch `task/005-hn-top-stories`.
**planner**: Design choice: the activity is a class method with an injected `httpx.AsyncClient`, so tests can pass a `MockTransport` and never touch the network. Task 010 will build `HNActivities(make_hn_client())` in the worker.
**planner**: `httpx` is not in `uv.lock` yet, so it must be added with `uv add httpx`, not pip. API JSON must go through Pydantic validation to satisfy Pyright strict with no `Any`.
**builder**: Added hn.py, tests/test_hn.py, fixture (7 ids), httpx dep, README note. `just lint` and `just test` pass. Next: verifier.
**builder**: Note: the mock serves `null` items via `content=json.dumps(...)` since httpx `json=None` sends an empty body. Fixture also has a `deleted` item (1007).
**verifier**: Ran `just lint` (ruff check, ruff format, pyright: all clean) and `just test` (19 passed, including 8 in tests/test_hn.py). All checks pass.
**reviewer**: Approved. All acceptance criteria and all 8 listed tests are present, and changes stay within "Files to touch". Notes: (1) `_RawItem` defaults missing `score`/`by`/`time` to 0/""/epoch, which is lenient but fine for now. (2) `make_hn_client` creates an unclosed client, so task 010 should close it on worker shutdown.
**scribe**: Opened PR https://github.com/akonale/ai-trend-pipeline/pull/10 (005: Activity: fetch Hacker News top stories). Ticked all acceptance boxes after re-running lint and test. Next: poll the `check` CI job, then the human merges.
