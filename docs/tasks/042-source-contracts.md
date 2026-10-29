# 042: Source contracts and HN polling adapter

Status: pr-open

## Goal

Introduce the source boundary every later ingestion task uses: frozen, bounded Pydantic contract
models, a static source registry, one shared normalization path, a deterministic fake connector for
tests, and an HN connector/activity that turns HN top stories into normalized `SourceEvent`s. No
workflow, candidate, UI, or database work in this task.

## Contract

All three boundary models are frozen (`ConfigDict(frozen=True, extra="forbid")`) and bounded, so a
malformed or oversized source payload fails at validation instead of entering Temporal history.
Treat the `list`/`dict` fields as read-only even though `frozen=True` only prevents reassignment.

### `SourceName` (`sources/models.py`)

`StrEnum` with `HN = "hn"` and `FAKE = "fake"`. `fake` is the pluggability/test source: it is
defined here so the fake connector and contract tests can produce valid events, but it is not in
the registry and cannot be enabled in this task. 049 registers it with an activity. Task 044 should
reuse this enum as `CandidateSource`; do not add a second source enum.

### `SourceEvent`

| Field | Type | Rules |
| --- | --- | --- |
| `source` | `SourceName` | Required |
| `external_id` | `str` | Trimmed; 1-128 chars; stable per source; unique within one output |
| `title` | `str` | Trimmed; 1-200 chars; otherwise stored verbatim; never HTML-processed |
| `url` | `HttpUrl \| None` | Only `http`/`https`; max 1024 chars; invalid schemes rejected |
| `published_at` | `datetime \| None` | Timezone-aware, stored in UTC; `None` when the source has none |
| `observed_at` | `datetime` | Timezone-aware, stored in UTC; one value per poll |
| `metrics` | `dict[str, int]` | Default `{}`; at most 4 entries; key 1-16 chars; value 0-10,000,000 |

Naive datetimes raise `ValidationError`; aware non-UTC input is converted to UTC. HN uses
`{"score": <int>, "comments": <int>}`.

### `SourcePollInput`

| Field | Type | Rules |
| --- | --- | --- |
| `source` | `SourceName` | Required |
| `limit` | `int` | Default 30; 1-100 |

`limit` is the maximum raw items requested and the maximum events returned.

### `SourcePollOutput`

| Field | Type | Rules |
| --- | --- | --- |
| `source` | `SourceName` | Required |
| `events` | `list[SourceEvent]` | Default `[]`; max 100 |

A model validator rejects an event whose `source` differs from the output's `source`, and duplicate
`external_id` values. An empty output is valid.

### `SourceConnector` (`sources/models.py`)

A `Protocol` with `async poll(data: SourcePollInput) -> SourcePollOutput`. The HN connector and the
fake implement it; contract tests are parameterised over both. Activity classes are thin Temporal
wrappers around connectors.

### Normalization (`sources/normalize.py`)

`RawSourceItem` is an internal, frozen, `extra="forbid"` model with `external_id: str`,
`title: str | None`, `url: str | None`, `published_at: datetime | None`,
`metrics: dict[str, int]`, and `discarded: bool = False`. It is not an activity boundary.

`normalize_events(*, source, items, limit, observed_at) -> SourcePollOutput` is the single path used
by HN and the fake:

- Skip items with `discarded=True`.
- Skip items whose trimmed `external_id` is empty or longer than 128 chars, whose trimmed `title` is
  empty or longer than 200 chars, or whose metrics break the bounds.
- A URL that is missing, malformed, longer than 1024 chars, or not `http`/`https` becomes `None`;
  the event is kept. `SourceEvent` itself still rejects such URLs.
- Collapse duplicate `external_id` values: the first valid occurrence wins, later ones are dropped.
- Preserve input order; stop once `limit` events are collected.
- Use the one supplied `observed_at` for every event.
- Never log titles, URLs, or metrics (source text is untrusted).

### Static registry (`sources/registry.py`)

`SourceDefinition` is frozen with `name: SourceName`, `label: str`, `poll_activity: str`. `REGISTRY`
is a read-only mapping (`MappingProxyType`) containing exactly one entry in this task:

| Source | Label | Poll activity |
| --- | --- | --- |
| `hn` | `Hacker News` | `poll_hn_source` |

`get_source(name)` returns the definition or raises `UnknownSourceError` (a `LookupError`).
`enabled_sources(enabled)` returns definitions in registry order and raises `UnknownSourceError` for
an enabled name that is not registered. The module imports only `models.py`, so 048's workflow code
can import it without I/O or HTTP imports.

### HN adapter (`sources/hn.py`)

- `HNSourceConnector(client: httpx.AsyncClient)` implements `SourceConnector`.
- It rejects `data.source != SourceName.HN` with `ValueError` before any HTTP call.
- It reuses the existing HN implementation by constructing `HNActivities(client)` and calling its
  `fetch_hn_top_stories` method directly with `HNTopStoriesInput(limit=data.limit)`. The decorated
  method is a plain coroutine when called directly; no nested Temporal scheduling happens.
- It maps `HNStory` to `RawSourceItem`: `external_id=str(story.id)`, `title=story.title`,
  `url=story.url`, `published_at=story.time` when positive else `None`, and
  `metrics={"score": story.score, "comments": story.comments}`. `dead`, `deleted`, non-stories,
  null items, and title-less items are already excluded by `fetch_hn_top_stories`; the shared
  normalizer additionally drops whitespace-only and over-long titles.
- It computes `observed_at = datetime.now(UTC)` once per poll and then calls `normalize_events`.
- `HNSourceActivities(client)` has the activity `poll_hn_source` (function name and
  `@activity.defn` name both `poll_hn_source`), which delegates to the connector's `poll`.
- `make_source_hn_client(settings)` returns an `httpx.AsyncClient(base_url=HN_BASE_URL,
  timeout=settings.source_poll_timeout_s)`. `make_hn_client()` and its 10.0 s timeout stay unchanged.
- Polling is read-only, so retries are safe; HTTP errors propagate for Temporal's retry policy.

### Fake connector (`sources/fake.py`)

`FakeSourceConnector(items: Sequence[RawSourceItem], *, observed_at: datetime | None = None)`
implements `SourceConnector`, rejects `data.source != SourceName.FAKE`, and runs the same
`normalize_events`. No I/O. It exists for the contract suite in this task and for 049's pluggability
proof. It is deliberately not registered and has no activity and no worker wiring in 042.

### Settings (`config.py`)

| Env var | Field | Default | Rules |
| --- | --- | --- | --- |
| `SOURCE_ENABLED` | `source_enabled: frozenset[SourceName]` | `{hn}` | Comma-separated registered source names, case-insensitive, whitespace trimmed; `none` means an empty set; an unknown or unregistered name raises `ValueError` at startup |
| `SOURCE_POLL_LIMIT` | `source_poll_limit: int` | `30` | 1-100 |
| `SOURCE_POLL_TIMEOUT_S` | `source_poll_timeout_s: float` | `15.0` | Greater than 0 and at most 60 |

Empty values count as unset, matching the existing settings. `Settings()` must equal
`load_settings({})`. Unknown names are validated against `REGISTRY`, not just `SourceName`, so
`fake` is rejected until 049 registers it. The error message lists the registered names.

## Acceptance criteria

- [ ] `sources` defines `SourceName`, `SourceEvent`, `SourcePollInput`, `SourcePollOutput` and the
      `SourceConnector` protocol exactly as above: frozen, `extra="forbid"`, bounded fields,
      timezone-aware UTC timestamps, `http`/`https`-or-`None` URLs, one source and unique
      `external_id` values per output, and an empty output allowed.
- [ ] The registry is read-only and contains only `hn` mapped to the `poll_hn_source` activity;
      `get_source` and `enabled_sources` raise `UnknownSourceError` for unregistered names; the
      module imports no I/O.
- [ ] `HNSourceConnector` and `HNSourceActivities.poll_hn_source` reuse
      `HNActivities.fetch_hn_top_stories` with no edit to `hn.py`: the recorded fixture yields
      normalized events, dead/deleted/title-less/non-story/null items are excluded, duplicate IDs
      collapse to the first occurrence, invalid URL schemes become `None` with the event kept, a
      missing `time` yields `published_at=None`, and event count never exceeds `limit`. The
      activity returns `SourcePollOutput` through `ActivityEnvironment`.
- [ ] The fake connector implements the same protocol and passes the same contract cases as HN,
      deterministically (fixed `observed_at` supported); it is not reachable through the registry or
      worker.
- [ ] `config.py` adds the three settings with the stated defaults, bounds and `none` semantics;
      `Settings()` equals `load_settings({})`; unknown/unregistered source names fail startup with
      a clear `ValueError`; `make_source_hn_client` uses the configured timeout while
      `make_hn_client` is unchanged.
- [ ] `hn.py`, `worker.py`, `daily_chart.py`, `chart_loop.py`, chart activities/routes and
      `tests/test_hn.py` are unchanged; `just lint` (Ruff plus strict Pyright, no unexplained
      `Any`/`type: ignore`) and `just test` pass; `tech.md`, `architecture/README.md` and
      `README.md` describe the package, activity, registry and settings.

## Files to touch

- `src/ai_trend_pipeline/sources/__init__.py` (new; re-export the contract models only, no
  `hn`/`fake`/`registry` imports, so `config.py` can import it cheaply)
- `src/ai_trend_pipeline/sources/models.py` (new: `SourceName`, `SourceEvent`,
  `SourcePollInput`, `SourcePollOutput`, `SourceConnector`)
- `src/ai_trend_pipeline/sources/normalize.py` (new: `RawSourceItem`, `normalize_events`)
- `src/ai_trend_pipeline/sources/registry.py` (new: `SourceDefinition`, `REGISTRY`,
  `UnknownSourceError`, `get_source`, `enabled_sources`)
- `src/ai_trend_pipeline/sources/hn.py` (new: `HNSourceConnector`, `HNSourceActivities`,
  `make_source_hn_client`)
- `src/ai_trend_pipeline/sources/fake.py` (new: `FakeSourceConnector`)
- `src/ai_trend_pipeline/config.py` (new `Settings` fields and `load_settings` parsing)
- `tests/fixtures/hn_source_poll.json` (new recorded fixture: duplicate top-story ID; https,
  `javascript:`, `ftp:` and no URL items; whitespace-only and over-long titles; missing `time`;
  dead, deleted, job and null items; `Ask HN` with no URL)
- `tests/test_sources_models.py` (new)
- `tests/test_sources_registry.py` (new)
- `tests/test_sources_contract.py` (new; shared suite over HN and fake)
- `tests/test_sources_hn.py` (new)
- `tests/test_config.py` (extend)
- `docs/architecture/tech.md` (module map, activities table, settings table, a short source-contract
  note under Temporal)
- `docs/architecture/README.md` (module map row for `sources/`; no diagram or state changes)
- `README.md` (Configuration: the three env vars; Hacker News section: one sentence on
  `sources/` and the HN poll activity)

## Tests to write

`tests/test_sources_models.py`

- `test_source_event_minimal_and_frozen`: only required fields work; `url`/`published_at`/`metrics`
  default; reassigning a field raises.
- `test_source_event_rejects_non_http_urls`: `javascript:`, `ftp:`, `data:`, `file:` and hostless
  URLs raise; `http://`, `https://` and `None` pass.
- `test_source_event_enforces_bounds`: blank/over-long `external_id`, blank/over-long `title`,
  over-long URL, too many metrics, bad metric key/value all raise.
- `test_source_event_timestamps_are_aware_utc`: naive `published_at`/`observed_at` raise; `+02:00`
  input is stored as UTC.
- `test_source_event_keeps_title_verbatim`: a title containing `<script>` is stored unchanged as a
  plain string.
- `test_source_poll_input_bounds`: default 30; 1 and 100 accepted; 0, 101 and an extra field raise.
- `test_source_poll_output_invariants`: empty allowed; mismatched source, duplicate `external_id`
  and more than 100 events raise.
- `test_contract_models_round_trip_json`: `model_dump_json` then `model_validate_json` preserves an
  event (Temporal converter compatibility).

`tests/test_sources_registry.py`

- `test_registry_contains_only_hn`: one entry; key equals `definition.name`; label and
  `poll_activity` non-empty; activity matches `HNSourceActivities.poll_hn_source.__name__`.
- `test_get_source_unknown_raises`: `get_source(SourceName.FAKE)` raises `UnknownSourceError`.
- `test_enabled_sources_filters_and_keeps_order`: empty set gives `[]`; `{HN}` gives the HN
  definition; an unregistered name raises.
- `test_registry_is_read_only`: item assignment raises `TypeError`.

`tests/test_sources_contract.py` (parameterised over the HN and fake connectors, using the same
logical cases: valid, duplicate, blank title, over-long title, discarded/dead/deleted/non-story/null,
invalid URL scheme, no URL, missing `time`)

- `test_contract_valid_items_are_normalized`: returns `SourcePollOutput`; source matches; external
  IDs are strings; titles are non-empty; URLs are `http`/`https` or `None`; timestamps are aware UTC;
  every event has the same `observed_at`.
- `test_contract_excludes_discarded_blank_and_oversized`: excluded IDs are absent and valid IDs
  remain in input order.
- `test_contract_collapses_duplicate_ids`: duplicate ID appears once with the first occurrence's
  title.
- `test_contract_drops_invalid_url_scheme_but_keeps_event`: event present with `url is None`.
- `test_contract_missing_published_time_is_none`: `published_at is None`; `observed_at` still set.
- `test_contract_respects_limit`: `len(events) <= limit`; `SourcePollInput(limit=0)` and `101` are
  rejected.
- `test_contract_empty_source_gives_empty_output`: no error, empty `events`.

`tests/test_sources_hn.py`

- `test_hn_poll_matches_fixture_without_network`: `httpx.MockTransport` handler allows only the
  fixture's top-stories and item paths; output order and fields match expectations.
- `test_hn_poll_limit_caps_item_requests`: `limit=3` makes one top-stories request and at most three
  item requests.
- `test_hn_poll_http_error_raises`: a 500 from `topstories.json` raises `httpx.HTTPStatusError`.
- `test_hn_poll_rejects_wrong_source_before_http`: `SourcePollInput(source=SourceName.FAKE)` raises
  `ValueError` and the mock handler records no request.
- `test_hn_activity_returns_source_poll_output`: `ActivityEnvironment().run` on `poll_hn_source`
  returns `SourcePollOutput`.
- `test_make_source_hn_client_uses_setting_timeout`: base URL is `HN_BASE_URL` and read timeout
  equals `source_poll_timeout_s`; `make_hn_client()` still uses 10.0 s.

`tests/test_config.py` (extend; keep existing tests unchanged)

- `test_source_settings_defaults`: `{HN}`, 30 and 15.0; empty env values equal the defaults and
  `Settings()`.
- `test_source_enabled_parsing`: `"hn"`, `" HN "`, duplicate entries collapse to `{HN}`; `"none"`
  gives an empty set.
- `test_source_enabled_unknown_raises`: `"google-trends"` and `"fake"` raise `ValueError` naming the
  registered sources.
- `test_source_poll_limit_bounds`: 1 and 100 accepted; 0, 101, `-1`, `abc` and `2.5` raise.
- `test_source_poll_timeout_bounds`: 0.5 and 60 accepted; 0, `-1`, 61 and `abc` raise.

## Out of scope

- `SourceIngestionWorkflow`, timers, cursor/processed-ID state, continue-as-new, task queues,
  worker registration or startup wiring (048). Do not change `worker.py`; the new activities are
  exercised through `ActivityEnvironment` only in this task.
- Batch LLM selection and prefiltering (043); candidate workflow, gateway, service, visibility,
  tables or UI (044-047); operational logs, budgets, runbooks (049).
- Any Postgres table, migration, search attribute or compose/env-file change. The new settings
  default safely, so no deployment config is required here.
- Editing `hn.py`, `tests/test_hn.py`, `DailyChartWorkflow`, chart activities, CLI or chart web
  routes; changing `HNTopStoriesInput`/`HNTopStoriesOutput`/`HNStory` or the 10 s HN timeout.
- Registering `fake` or adding a fake activity; Google Trends, RSS or any other real connector.
- Templates, CSS, view models or any UI work. No UI in this task, so the frontend-design skill does
  not apply.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: Spec written. No UI, so frontend-design was not applied.
Pinned the contract: frozen bounded models, `SourceName{hn,fake}`, HN-only registry, one shared
normalizer, fake connector in `sources/fake.py` (unregistered), `poll_hn_source` reusing
`HNActivities.fetch_hn_top_stories` with `hn.py` untouched.
Assumptions not in the plan: env names/defaults `SOURCE_ENABLED=hn`, `SOURCE_POLL_LIMIT=30`,
`SOURCE_POLL_TIMEOUT_S=15.0`, `none` disables all; invalid URLs are dropped per event, not the poll;
044 should reuse `SourceName` as `CandidateSource`.
Next: orchestrator approves (approval no), builder implements on `task/042-source-contracts`.

**builder**: Added `sources/` contracts, registry, shared normalizer, HN connector/`poll_hn_source`
activity, unregistered fake connector, and the three `SOURCE_*` settings; tests and docs updated.
`hn.py`, `worker.py`, chart code and `tests/test_hn.py` are unchanged. `just lint` is clean and the
suite passes (530) with the harness `ANTHROPIC_*` base-url vars unset; with them set, pre-existing
LLM tests fail on connection. Next: verifier runs `just lint` and `just test`.
**verifier**: `just lint` clean; `just test` 530 passed with harness `ANTHROPIC_*` base-url/token vars unset. With them set, only 43 pre-existing LLM tests fail on `anthropic.APIConnectionError`, unrelated to 042; all `tests/test_sources_*` pass.
Next: reviewer checks the diff against the checklist.
**reviewer**: Approved. Verified contracts are frozen/bounded, URLs restricted to http/https, duplicates collapse, dead/deleted/title-less items are excluded, `hn.py`/`worker.py`/chart code/`tests/test_hn.py` untouched, settings only in `config.py`, and no table or scope creep. `just lint` clean; `just test` 530 passed.
Next: scribe updates docs and opens the PR.
**scribe**: Docs were already updated in the build commit: `tech.md` (activity, sources section, settings), `architecture/README.md` (module row), `README.md` (settings and HN note); verified accurate, no further edits needed. Pushed and opened the PR. Next: human merges.
