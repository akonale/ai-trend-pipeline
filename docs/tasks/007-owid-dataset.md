# 007: Activity: fetch a matching Our World in Data dataset

Status: pr-open

## Goal

Add a Temporal activity that takes the topic string from task 006 and maps it to one Our World in Data (OWID) grapher chart, using a small keyword catalog kept in code. It downloads that chart's CSV with `httpx` and returns it as a typed Pydantic table. If no catalog entry matches the topic, it fails right away with a non-retryable error and makes no HTTP call.

## Acceptance criteria

- [x] `src/ai_trend_pipeline/owid.py` defines these Pydantic models:
  - `OwidDataset(slug: str, title: str, keywords: tuple[str, ...])` is one catalog entry.
  - `FetchOwidInput(topic: str, since_year: int = 2000)`. `topic` uses `Field(min_length=1)` and `since_year` uses `Field(ge=1800, le=2100)`.
  - `OwidRow(entity: str, code: str | None, year: int, value: float)`.
  - `OwidTable(slug: str, title: str, value_column: str, rows: list[OwidRow])`. This is the activity output.
  - Module constants: `OWID_BASE_URL = "https://ourworldindata.org/grapher/"`, `NO_MATCH_ERROR_TYPE = "NoMatchingDataset"`, and `CATALOG: tuple[OwidDataset, ...]` with these 6 entries, in this order. Keywords are lowercase:
    1. `co-emissions-per-capita`, "CO2 emissions per capita", keywords `co2`, `carbon`, `emissions`, `climate`
    2. `share-of-individuals-using-the-internet`, "Share of the population using the Internet", keywords `internet`, `online`, `broadband`
    3. `solar-energy-consumption`, "Solar energy consumption", keywords `solar`, `renewable`, `renewables`
    4. `electric-car-sales-share`, "Share of new cars sold that are electric", keywords `electric car`, `electric vehicle`, `ev`, `evs`, `tesla`
    5. `life-expectancy`, "Life expectancy", keywords `life expectancy`, `health`, `longevity`
    6. `gdp-per-capita-worldbank`, "GDP per capita", keywords `gdp`, `economy`, `economic`, `income`
- [x] `match_dataset(topic: str) -> OwidDataset | None` is a pure function. It lowercases the topic and returns the first `CATALOG` entry, in catalog order, where any keyword appears as a whole word or phrase. It uses a regex with `\b` on both sides and `re.escape(keyword)`, so `ev` matches "EV sales" but not "every" or "developer". If nothing matches, it returns `None`.
- [ ] The activity is a method on a class, so the client can be injected, the same way as in `hn.py`: `class OwidActivities: def __init__(self, client: httpx.AsyncClient)` with `@activity.defn async def fetch_owid_dataset(self, data: FetchOwidInput) -> OwidTable`. The method does the following:
  1. Calls `match_dataset(data.topic)`. If the result is `None`, it raises `ApplicationError(f"No OWID dataset matches topic {data.topic!r}", type=NO_MATCH_ERROR_TYPE, non_retryable=True)` and sends no request. (Task 010 decides what the workflow does on this error. This task only raises it.)
  2. Makes exactly one `GET {slug}.csv` with params `csvType=full` and `useColumnShortNames=true`.
  3. A 404 is raised as `ApplicationError(..., non_retryable=True)` because a wrong slug will not fix itself on retry. Any other error status raises `httpx.HTTPStatusError` through `raise_for_status()`, so Temporal retries it.
  4. Parses the body with the stdlib `csv` module. The header must start with `Entity,Code,Year` and have at least 4 columns. If it does not, it raises `ApplicationError(..., non_retryable=True)`. `value_column` is the 4th header name, and any extra columns are ignored.
  5. Builds one `OwidRow` per data row. An empty `Code` becomes `None`. Rows with an empty value, or with `year < data.since_year`, are skipped. The year filter keeps the output well under Temporal's 2 MB payload limit. Rows stay in file order.
  6. Returns `OwidTable(slug=..., title=<catalog title>, value_column=..., rows=...)`.
  It uses no `Any` and no `# type: ignore`, and Pyright strict passes.
- [x] `make_owid_client() -> httpx.AsyncClient` returns a client with `base_url=OWID_BASE_URL`, `timeout=30.0` and `follow_redirects=True`. The activity only sends GET requests and has no side effects, so it is idempotent.
- [x] No new dependencies, so `pyproject.toml` and `uv.lock` stay unchanged. Tests use plain `httpx.MockTransport`, like `tests/test_hn.py`. `httpx2` is not used here. The fixture `tests/fixtures/owid_life_expectancy.csv` is written by hand in the real OWID grapher CSV shape. Header: `Entity,Code,Year,life_expectancy_0`. It has about 12 rows that cover: a country with a code, a region with an empty `Code` (for example `Africa`), a row with an empty value, and years both before and after 2000.
- [x] `just lint` and `just test` pass, and no test makes a network call. The mock handler fails the test on any path other than `/grapher/life-expectancy.csv`. `README.md` gets one or two lines noting the OWID activity (module `ai_trend_pipeline.owid`) and that it only covers the topics in `CATALOG`.

## Files to touch

- `src/ai_trend_pipeline/owid.py` (new)
- `tests/test_owid.py` (new)
- `tests/fixtures/owid_life_expectancy.csv` (new)
- `README.md`

## Tests to write

Activity tests use `httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=OWID_BASE_URL)`, where the handler serves the fixture, and run through `temporalio.testing.ActivityEnvironment`. The topic is `"Rising life expectancy worldwide"` unless a test says otherwise.

- `test_fetch_returns_typed_table`: the result is an `OwidTable` with `slug == "life-expectancy"`, `value_column == "life_expectancy_0"`, and every row is an `OwidRow`. The first row's values equal the fixture.
- `test_request_shape`: the handler captures requests. It checks there is exactly one GET to `/grapher/life-expectancy.csv` with `csvType=full` and `useColumnShortNames=true`.
- `test_region_code_is_none_and_blank_values_skipped`: the region row has `code is None`, and the empty-value row is missing from the output.
- `test_since_year_filters_rows`: by default every `year >= 2000`. With `since_year=1950`, the earlier fixture rows are included.
- `test_no_match_raises_non_retryable_without_request`: with topic `"Rust compiler release"`, the activity raises `ApplicationError` with `type == NO_MATCH_ERROR_TYPE` and `non_retryable is True`, and the handler gets zero requests.
- `test_not_found_is_non_retryable`: if the handler returns 404, the activity raises `ApplicationError` with `non_retryable is True`.
- `test_server_error_raises_http_status_error`: if the handler returns 500, the activity raises `httpx.HTTPStatusError`.
- `test_bad_header_is_non_retryable`: if the body has the header `Entity,Day,value`, the activity raises `ApplicationError` with `non_retryable is True`.
- `test_match_dataset`: a parametrized test with no HTTP. `"CO2 emissions in Europe"` maps to `co-emissions-per-capita`. `"EV sales boom"` maps to `electric-car-sales-share`. `"every developer"` maps to `None`. `"Internet access in Africa"` maps to `share-of-individuals-using-the-internet`. `"Solar and climate"` maps to `co-emissions-per-capita`, because the first catalog entry wins.
- `test_input_validation`: `FetchOwidInput(topic="")` and `FetchOwidInput(topic="x", since_year=1700)` both raise `pydantic.ValidationError`.
- `test_make_owid_client`: the base URL equals `OWID_BASE_URL` and `follow_redirects` is true.

## Out of scope

- Live OWID search, the OWID search API, an LLM-based dataset choice, or fuzzy or embedding matching. The catalog stays a hand-written constant.
- Fetching `.metadata.json`, units or source citations, and multi-column or `Day`-based datasets.
- Caching, storing datasets in Postgres, or adding catalog entries beyond the 6 listed.
- Changing the task 006 prompt to steer it toward catalog topics. That can happen in task 010 if no-match turns out to be common.
- Registering the activity in `worker.py`, or any workflow that calls it or handles the no-match error (task 010).
- Any test or script that calls the live OWID site, including a fixture-recording script.
- Changes to `hn.py`, `topic.py`, `CLAUDE.md`, `docs/mvp-plan.md`, `justfile`, `infra/`, CI, `pyproject.toml` or `uv.lock`.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->
**planner**: Wrote the 007 spec and set Status to `planned`. It fits in one PR (1 module, 1 test file, 1 CSV fixture, README, no new dependencies), so no split is needed. Next: the orchestrator sets `approved`, then the builder works on branch `task/007-owid-dataset`.
**planner**: Design: a keyword catalog maps the topic to an OWID grapher slug (first match wins, using `\b` word boundaries). The data comes from the `/grapher/{slug}.csv` endpoint and is parsed with the stdlib `csv` module. No match raises a non-retryable `ApplicationError` of type `NoMatchingDataset` before any HTTP call.
**planner**: Uses plain `httpx` like `hn.py` (not `httpx2`). The `since_year` filter (default 2000) keeps the output under Temporal's payload limit. The 6 slugs come from memory, not a live check. If one is wrong, the 404 fails non-retryably and fixing it is a one-line edit later.
**builder**: Built owid.py, tests, fixture, README; `just lint` and `just test` pass (42 tests). Slug check (HEAD, 2026-10-21): all 6 return 200 except `solar-energy-consumption`, which is a 301 to `/grapher/electricity-mix.csv?...source=solar`; with httpx `follow_redirects` and our params it returns 200 (header `entity,code,year,solar_generation__twh`), so no slug changed.
**builder**: Spec deviation: live OWID headers are lowercase (`entity,code,year,...`), so the header check is case-insensitive (spec said `Entity,Code,Year`); added `test_lowercase_real_header_is_accepted`. Unsure: gdp CSV has an extra `owid_region` column (ignored, fine).
**verifier**: Ran `just lint` (ruff, format, pyright: 0 errors) and `just test` (42 passed). All checks pass.
**reviewer**: Approved. All criteria and listed tests present; no live HTTP (MockTransport, handler asserts path); redirects followed via `follow_redirects=True` in `make_owid_client`; 404 and bad header non-retryable, 5xx via `raise_for_status`; `since_year` filter applied. Case-insensitive header deviation is justified and tested.
Notes: (1) `int()`/`float()` on a malformed row raises a retryable `ValueError`; consider non-retryable later. (2) `response.text.splitlines()` would split quoted newlines; harmless for OWID data. (3) Tests don't exercise redirect following (MockTransport), only the client flag.
**closer**: Opened PR https://github.com/akonale/ai-trend-pipeline/pull/12. Activity checkbox left unticked for the case-insensitive header deviation (explained in PR). Next: orchestrator reviews and merges; CI `check` job result is logged below.
**closer**: CI `check` passed on 3a2b695 (run 39636485040, 19s). Next: orchestrator merges PR 12.
