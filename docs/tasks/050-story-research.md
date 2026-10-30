# 050: Story research and evidence contracts

Status: pr-open

## Goal

Add bounded Pydantic research contracts and three standalone, idempotent activities that safely
fetch and normalize the original article, optionally fetch approved extra context, optionally attach
OWID supporting data, and return an explicit `supported` / `unsupported` outcome with sanitized
failures. No workflow, worker, UI, LLM, or publishing behaviour changes in this task.

## Design

### Why no LLM here

050 is I/O and contracts only: safe HTTP, normalization, deterministic OWID matching, and the
evidence-sufficiency decision. The LLM writer and checker arrive in 052; deterministic facts arrive
in 051. There is therefore no model/provider call, no new secret, and no `anthropic` client in this
task. Any model/provider added later for research must be configured in `config.py`.

### New package `research/`

| File | Responsibility |
| --- | --- |
| `research/models.py` | Enums, constants, type aliases, every Pydantic boundary model. Imports only `sources.models` and the stdlib; no I/O and no workflow imports. |
| `research/fetch.py` | URL policy, injectable DNS resolution, redirect loop, bounded streaming, content-type checks, HTML/plain-text normalization. Takes explicit limits in its constructor; does not import `config.py`. |
| `research/activities.py` | `StoryResearchActivities` (`fetch_context`, `match_owid_evidence`, `research_story`). Takes `httpx.AsyncClient` instances, `Settings`, and an optional resolver in `__init__`; imports `config.py` and `owid.py`. |

`research/__init__.py` re-exports the contract models only (no `fetch`/`activities` imports), so
`config.py` can import `HostName` and the defaults cheaply.

### Constants and limits

Content caps are code constants; network limits are settings (below).

| Constant | Value | Use |
| --- | --- | --- |
| `MAX_CONTEXTS` | `3` | primary article plus at most two approved extras |
| `MAX_EVIDENCE` | `4` | article evidence per context plus at most one dataset |
| `MAX_CONTEXT_TEXT_CHARS` | `8_000` | normalized text per context |
| `MAX_TOTAL_CONTEXT_CHARS` | `16_000` | sum of all context text in one output |
| `MIN_CONTEXT_TEXT_CHARS` | `300` | primary article text needed to support a story |
| `MAX_DATASET_ROWS` | `300` | OWID rows kept in source order after existing filters |
| `MAX_DATASET_COUNTRIES` | `20` | `matched_countries` list |
| `MAX_EXTRA_CONTEXT_URLS` | `2` | `ResearchInput.extra_context_urls` |
| `MAX_EVIDENCE_EXCERPT_CHARS` | `500` | article excerpt in `EvidenceSource` |
| `MAX_FAILURES`, `MAX_NOTES` | `8`, `8` | output lists |
| `MAX_FAILURE_MESSAGE_CHARS` | `200` | sanitized failure message |
| `MAX_HOST_CHARS` | `253` | host fields and allowlist entries |
| `MAX_TITLE_CHARS` | `300` | article/evidence titles |
| `ALLOWED_CONTENT_TYPES` | `text/html`, `text/plain`, `application/xhtml+xml` | fetched article media types |
| `ALLOWED_PORTS` | `None` (scheme default), `80`, `443` | outbound URLs |
| `REDIRECT_STATUSES` | `301`, `302`, `303`, `307`, `308` | followed redirects |
| `MAX_RESEARCH_OUTPUT_BYTES_TARGET` | `96 * 1024` | test-only serialized ceiling |

### Models

All boundary models are frozen, `extra="forbid"`, and use `sources.models` helpers where they
already exist (`SourceName`, `HttpUrl1024`, `MetricKey`, `MetricValue`, `ensure_aware_utc`). All
datetimes must be timezone-aware and are stored in UTC. `StoryInput` carries the 044
`candidate_id` so 053 can map a `StoryCandidate` without research importing workflow code.

| Model | Fields and limits |
| --- | --- |
| `StoryInput` | `candidate_id` 1-64; `source: SourceName`; `external_id` 1-128; `title` 1-200; `url: HttpUrl1024 \| None`; `published_at: datetime \| None`; `observed_at: datetime`; `summary`, `proposed_angle`, `selection_reason` each stripped 1-500; `metrics` at most 4. Text fields strip whitespace. |
| `DatasetRow` | `entity` 1-100; `code: str \| None` 1-20; `year` 1800-2100; `value: float` must be finite. |
| `DatasetEvidence` | `slug` 1-100; `title` 1-200; `value_column` 1-100; `since_year` 1800-2100; `rows` at most 300; `matched_countries` at most 20 each 1-100; `notes` at most 5 each 1-200; `truncated: bool`. |
| `StoryContext` | `context_id` 1-80; `requested_url`, `url` `HttpUrl1024`; `title: str \| None` 1-300; `publisher` 1-253 (host only); `content_type` 1-100; `text` stripped 1-8000; `truncated: bool`; `retrieved_at: datetime`. |
| `EvidenceSource` | `evidence_id` 1-80; `kind: EvidenceKind`; `title` 1-300; `url: HttpUrl1024 \| None`; `publisher: str \| None` 1-253; `retrieved_at: datetime`; `observation_date: date \| None`; `context_id: str \| None` 1-80; `excerpt: str \| None` 1-500; `dataset: DatasetEvidence \| None`. A validator requires `context_id` and forbids `dataset` for `article`; requires `dataset` and forbids `context_id` for `dataset`. |
| `ResearchFailure` | `code: ResearchFailureCode`; `message` stripped 1-200 with control characters removed; `host: str \| None` 1-253 (host only, never a path or query). |
| `ResearchInput` | `story: StoryInput`; `extra_context_urls: list[HttpUrl1024]` at most 2 and unique; `include_owid: bool = True`. Frozen. |
| `ResearchOutput` | `story_id` 1-64; `status: ResearchStatus`; `contexts` at most 3; `evidence` at most 4; `failures` at most 8; `notes: list[ResearchNoteCode]` at most 8; `researched_at: datetime`. Validators enforce unique `context_id`/`evidence_id`, total text at most 16 000, article evidence points at a context, `supported` requires at least one article context of at least 300 chars, and `unsupported` requires at least one failure. |
| `FetchContextInput` | `url: HttpUrl1024`; `approved_hosts: frozenset[HostName] \| None = None` (at most 50). When set, the initial host and every redirect host must be in it. |
| `FetchContextOutput` | `requested_url: HttpUrl1024`; exactly one of `context: StoryContext \| None` or `failure: ResearchFailure \| None`; `notes` at most 8. |
| `MatchEvidenceInput` | `story: StoryInput`; `include_owid: bool = True`. |
| `MatchEvidenceOutput` | `evidence: EvidenceSource \| None`; `notes` at most 8. |

Enums:

- `ResearchStatus`: `supported`, `unsupported`.
- `EvidenceKind`: `article`, `dataset`.
- `ResearchFailureCode`: `no_source_url`, `unsafe_url`, `blocked_network`, `too_many_redirects`,
  `missing_article`, `fetch_failed`, `unsupported_content_type`, `malformed_content`,
  `insufficient_evidence`.
- `ResearchNoteCode`: `no_catalog_match`, `owid_disabled`, `dataset_unavailable`, `dataset_empty`,
  `dataset_truncated`, `response_truncated`, `extra_context_not_allowed`,
  `extra_context_fetch_failed`, `duplicate_context`, `text_budget_exhausted`.

Stable IDs: `context_id` is `ctx-v1-<16 hex>` of SHA-256 over the normalized final URL;
`evidence_id` is `ev-v1-<16 hex>` over `f"<kind>\x1f<context_id-or-slug>"`. Repeated research of the
same URL/dataset therefore produces the same IDs, which 051 can use for fact lineage.

### Support rule (explicit, deterministic)

`supported` means the **original article** was fetched, normalized, and has at least
`MIN_CONTEXT_TEXT_CHARS` (300) characters of text. Additional approved context and OWID dataset
evidence are supplementary; neither can substitute for a missing or unreadable original article.
This is why a missing article is `unsupported` even if an OWID dataset matches. No catalog match is
fine: an article-only story is supported, and `no_catalog_match` is a note.

### Safe HTTP and SSRF rules

`research/fetch.py` must enforce all of the following before and during every request. Define
`HostResolver = Callable[[str], Awaitable[Sequence[str]]]` there; it returns resolved IP strings and
is injectable so tests never resolve real names.

1. Only `http`/`https`; no userinfo; host present; port `None`, `80`, or `443`; URL at most 1024
   characters. Reject `localhost`, single-label hosts, and hosts ending in `.localhost`, `.local`,
   `.internal`, `.home.arpa`, or `.lan`.
2. Resolve all A/AAAA addresses through an injectable async resolver before each hop. Reject the
   URL if any address is not global (`ipaddress` `is_global` false), including private, loopback,
   link-local, multicast, reserved, unspecified, carrier-grade NAT, and IPv4-mapped IPv6. A literal
   IP must also be global. DNS resolution failure is retryable (propagate `httpx.ConnectError`);
   resolving to any private address is a returned `blocked_network` failure.
3. Use an `httpx.AsyncClient` with `follow_redirects=False` and `trust_env=False`; never use
   environment proxies. Follow redirects manually, at most `research_max_redirects` (default 3),
   only for the statuses above, resolving relative `Location` values. Validate scheme, host,
   port, URL length, and destination network again on every hop. A non-http(s) redirect is
   `unsafe_url`; a private/internal destination is `blocked_network`; exceeding the limit is
   `too_many_redirects`.
4. When `approved_hosts` is set, require the initial host and every redirect host to match an
   entry exactly (case-insensitive, trailing dot stripped). The primary candidate URL is fetched
   with `approved_hosts=None`; extra context uses the setting's allowlist.
5. Allowed response types only: `text/html`, `text/plain`, `application/xhtml+xml` (parameters
   ignored, media type lowercased). Missing or other media type is `unsupported_content_type`
   without reading the body.
6. Send `Accept-Encoding: identity` and the configured user agent. Stream the body and read at
   most `research_max_response_bytes`; on exceeding it, stop reading, keep the prefix, set
   `truncated=True`, and add `response_truncated` (do not fail the whole research). `404`/`410` is
   `missing_article`; other 4xx is `fetch_failed`; `408`, `425`, `429`, and 5xx raise for Temporal
   retry; timeouts, transport errors, and connection errors raise for retry.
7. Normalize with the stdlib `html.parser.HTMLParser`: extract `<title>`, drop comments and
   `script`, `style`, `noscript`, `template`, `svg`, `iframe`, and `head` body text, decode
   character references, insert separators at block tags, remove control characters, collapse
   whitespace, and stop collecting once the text cap is reached. Plain text is only whitespace-
   normalized. Cap parsed text at 8000 chars; mark `truncated` when body or text was cut. Empty or
   script-only content is `malformed_content`.
8. Never log, return, or put in a failure message the response body, query string, credentials, or
   a full URL. Failures carry only a code, a short sanitized message, and the host.

Residual risk to document, not over-claim: resolution and validation happen before each hop, but
`httpx` resolves again at connect time, so a DNS-rebinding TOCTOU remains. The task's tests must
prove the pre-connect checks and redirect validation; docs must not claim absolute prevention. Do
not add an SSRF dependency or a bespoke HTTP client.

### Activity boundaries and error policy

`StoryResearchActivities` constructor:
`(article_client: httpx.AsyncClient, owid_client: httpx.AsyncClient, settings: Settings, *,
resolver: HostResolver | None = None)`. `article_client` must be created with
`follow_redirects=False`, `trust_env=False`, and `Accept-Encoding: identity`; provide
`make_research_client(settings)` in `activities.py` for 053. `owid_client` can be
`make_owid_client()`.

| Activity | Behaviour |
| --- | --- |
| `fetch_context` | Runs the safe fetcher for one `FetchContextInput`; returns exactly one context or one failure. Expected URL/content outcomes are values, not exceptions. |
| `match_owid_evidence` | Pure `match_dataset` over `title + summary + proposed_angle`. On a hit, calls `OwidActivities(owid_client).fetch_owid_dataset` with the matched dataset title and `since_year=2000`; converts the table to bounded `DatasetEvidence` (first 300 rows, note `dataset_truncated` if cut). No match -> `no_catalog_match`; `include_owid=False` -> `owid_disabled`; any OWID `ApplicationError` or `httpx` error -> `dataset_unavailable`; empty rows -> `dataset_empty`. OWID is optional, so OWID failures never fail the research. |
| `research_story` | Orchestrates the two helpers (plain calls, never nested activity scheduling). Fetches the primary URL first; fetches at most two extras only when their host is exactly allowlisted. Primary retryable HTTP errors propagate; extra-context retryable errors become non-fatal `extra_context_fetch_failed` issues. Dedupes contexts, enforces the 3-context and 16 000-char budgets, builds article evidence then dataset evidence, applies the support rule, dedupes notes/failures, and caps both lists. |

All three are read-only and idempotent. They start no workflow, make no Temporal client call, and
never directly import `services/`, `web/`, `worker.py`, `daily_chart.py`, `story_candidate.py`,
`source_ingestion.py`, or `story_selection.py`. They are not registered on any worker in 050; 053
owns registration and retry policies (recommended: 120 s start-to-close, 3 attempts for the
network activities).

### Settings (`config.py`)

| Env var | Field | Default | Rules |
| --- | --- | --- | --- |
| `RESEARCH_FETCH_TIMEOUT_S` | `research_fetch_timeout_s: float` | `10.0` | greater than 0, at most 60 |
| `RESEARCH_MAX_RESPONSE_BYTES` | `research_max_response_bytes: int` | `524288` | 1024-4194304 |
| `RESEARCH_MAX_REDIRECTS` | `research_max_redirects: int` | `3` | 0-5 |
| `RESEARCH_USER_AGENT` | `research_user_agent: str` | `ai-trend-pipeline-research/0.1` | stripped, 1-200; empty means default |
| `RESEARCH_EXTRA_CONTEXT_HOSTS` | `research_extra_context_hosts: frozenset[HostName]` | empty | comma-separated exact hostnames, case-insensitive; empty or `none` means no extra context; reject schemes, ports, paths, userinfo, IP literals, single-label hosts, and local/internal suffixes |

`HostName` is defined in `research/models.py` (lowercased, 1-253 chars, at least one dot, no
leading/trailing dot/hyphen, no scheme/port/path). `load_settings` parses the comma-separated string
and lets Pydantic validate each entry, so an invalid host fails startup. `Settings()` must equal
`load_settings({})`. All five fields are read only in `config.py`; the activity class receives
`Settings` rather than reading the environment.

## Acceptance criteria

- [ ] `research/models.py` defines every enum, constant, and model above with the stated bounds,
      frozen/`extra="forbid"` config, UTC-aware datetimes, stable `ctx-v1-`/`ev-v1-` IDs,
      evidence-kind consistency, unique IDs, the 16 000-char total, and the supported/unsupported
      invariants; a near-maximum `ResearchOutput` serializes under 96 KiB.
- [ ] The safe fetcher enforces the scheme/userinfo/port/host rules, resolves and rejects non-global
      destinations before every hop, follows at most the configured redirects with full
      re-validation, honours approved-host lists, enforces the content-type allowlist and byte cap,
      never uses environment proxies or automatic redirects, and normalizes HTML/plain text as
      specified without ever logging or returning raw bodies or full URLs.
- [ ] `fetch_context`, `match_owid_evidence`, and `research_story` return the typed models above;
      the support rule requires a readable original article; no catalog match is allowed; OWID
      failures degrade to notes; expected outcomes are failures/notes while retryable HTTP errors
      propagate; the activities start no workflow or Temporal client and are idempotent.
- [ ] `config.py` adds the five `RESEARCH_*` settings with the stated defaults, bounds and hostname
      parsing; `Settings()` equals `load_settings({})`; no secret or model setting is added because
      050 makes no LLM/provider call.
- [ ] No workflow behaviour changes: `worker.py`, `daily_chart.py`, `chart_loop.py`,
      `story_candidate.py`, `source_ingestion.py`, `story_selection.py`, `owid.py`, `sources/`,
      services, web, templates, CSS and existing chart/candidate tests are untouched; a test proves
      the research modules directly import no workflow modules and `worker.py` does not reference
      the new activities.
- [ ] Fixtures cover supported, missing article, irrelevant dataset, insufficient evidence,
      malformed content, oversized response, unsafe URL/redirect, internal-network block, and source
      prompt injection; no live HTTP, DNS, LLM or Temporal calls; `just lint` (Ruff + strict
      Pyright) and `just test` pass.

## Files to touch

- `src/ai_trend_pipeline/research/__init__.py` (new; re-export contract models only)
- `src/ai_trend_pipeline/research/models.py` (new)
- `src/ai_trend_pipeline/research/fetch.py` (new)
- `src/ai_trend_pipeline/research/activities.py` (new)
- `src/ai_trend_pipeline/config.py` (modify: five fields, parsing, `HostName` import)
- `tests/fixtures/research/article_supported.html` (new)
- `tests/fixtures/research/article_short.html` (new)
- `tests/fixtures/research/article_malformed.html` (new: script/style only, no readable text)
- `tests/fixtures/research/article_injection.html` (new: enough real text plus injection text)
- `tests/test_research_models.py` (new)
- `tests/test_research_fetch.py` (new)
- `tests/test_research_activities.py` (new)
- `tests/test_config.py` (extend)
- `docs/architecture/README.md` (module map row for `research/`)
- `docs/architecture/tech.md` (activities/models table, a short research subsection, settings table)
- `README.md` (Configuration rows and one sentence that research activities are not yet wired)

Reuse the existing `tests/fixtures/owid_life_expectancy.csv` for the OWID success case. Oversized,
binary, redirect, and error responses are generated inside `httpx.MockTransport` handlers in tests;
no multi-megabyte fixture is committed.

## Tests to write

`tests/test_research_models.py`

- `test_story_input_bounds_and_rejects_non_http_urls`: required fields, 1-200 title, 1-500 text,
  `javascript:`/`ftp:`/`file:` rejected, extra fields rejected, frozen assignment raises.
- `test_context_dataset_and_evidence_models_are_bounded_and_frozen`: per-field caps, 300 dataset
  rows, 500-char excerpt, extra fields rejected.
- `test_evidence_kind_consistency_and_stable_ids`: article requires `context_id`; dataset requires
  `dataset`; `ctx-v1-`/`ev-v1-` helpers are repeatable and change with URL/slug.
- `test_research_input_limits_extra_urls_and_validates_allowlist_hosts`: max 2, duplicates
  rejected, `HostName` rejects schemes/ports/paths/IPs/single labels, `include_owid` defaults true.
- `test_research_output_supported_requires_qualifying_article_and_linked_contexts`: invalid
  supported output and dangling article evidence raise.
- `test_research_output_enforces_total_text_and_list_caps`: over 16 000 chars, 4 contexts, 5
  evidence, 9 failures/notes raise; near-max output serializes under 96 KiB.
- `test_research_failure_sanitizes_message_and_keeps_host_only`: control chars stripped, 201 chars
  rejected, a full URL/query string is not accepted into `host`.
- `test_datetimes_are_aware_utc_and_models_round_trip_json`: naive datetimes raise; `+02:00` is
  stored as UTC; `model_dump_json`/`model_validate_json` preserves the output.

`tests/test_research_fetch.py`

- `test_fetch_normalizes_html_extracts_title_and_strips_scripts`: tags removed, entities decoded,
  whitespace collapsed, title captured, script text absent.
- `test_fetch_rejects_userinfo_disallowed_ports_and_non_http_redirects`: unsafe URL failures, no
  request for rejected URLs.
- `test_fetch_rejects_localhost_single_label_and_internal_suffixes`: each host variant fails with
  `unsafe_url`.
- `test_fetch_blocks_private_loopback_link_local_reserved_and_ipv6_destinations`: parameterised
  resolver addresses all produce `blocked_network` and no HTTP request.
- `test_fetch_allows_public_hosts_and_public_literal_ips`: the same request succeeds through
  `MockTransport`.
- `test_fetch_follows_and_revalidates_public_redirects`: relative and cross-host public redirects
  reach the final URL; the requested URL is preserved.
- `test_fetch_redirect_to_internal_or_non_http_is_blocked`: redirect target is never requested.
- `test_fetch_redirect_limit_and_missing_location`: fourth redirect is `too_many_redirects`;
  missing `Location` is `fetch_failed`.
- `test_fetch_extra_context_redirect_must_stay_within_approved_hosts`: redirect outside the
  allowlist is `unsafe_url` and is not followed.
- `test_fetch_content_type_and_status_outcomes`: missing/other media type is
  `unsupported_content_type`; 404/410 is `missing_article`; 403 is `fetch_failed`.
- `test_fetch_5xx_408_425_429_timeouts_and_transport_errors_propagate`: each raises for Temporal
  retry.
- `test_fetch_oversized_response_is_truncated_and_bounded`: body over the byte cap yields at most
  8000 text chars, `truncated=True`, and `response_truncated`.
- `test_fetch_script_only_or_empty_content_is_malformed`: no context, `malformed_content`.
- `test_make_research_client_disables_env_proxies_and_auto_redirects`: client flags and headers.
- `test_fetch_does_not_log_source_text_or_full_urls`: captured records contain neither the body nor
  the query string.

`tests/test_research_activities.py`

- `test_supported_story_returns_article_and_owid_evidence`: article fixture plus
  `owid_life_expectancy.csv`; status supported, one article and one dataset evidence, stable IDs.
- `test_missing_article_is_unsupported`: 404 primary; `missing_article` plus
  `insufficient_evidence`; no article evidence.
- `test_irrelevant_or_missing_dataset_does_not_block_article_support`: no catalog keyword; status
  supported, `no_catalog_match`, no OWID request.
- `test_dataset_match_alone_cannot_support_a_story`: readable dataset but missing/short primary;
  status unsupported.
- `test_insufficient_evidence_is_unsupported`: primary below 300 chars; unsupported.
- `test_malformed_and_unsupported_content_types_are_unsupported`: script-only HTML and binary body.
- `test_oversized_response_stays_bounded_and_marks_truncated`: activity output respects text and
  serialized caps.
- `test_unsafe_primary_url_and_internal_network_are_blocked_without_requests`: no handler call.
- `test_extra_context_is_fetched_only_from_exactly_approved_hosts`: one allowed and one denied URL;
  denied host produces `extra_context_not_allowed` and no request.
- `test_extra_context_retryable_failure_is_non_fatal`: primary supports; extra raises; output still
  supported with an extra-context failure.
- `test_owid_unavailable_or_empty_degrades_to_notes`: 404/500/empty CSV; supported article-only,
  note only, no raised error.
- `test_source_prompt_injection_is_inert_data`: injection text in article and candidate remains
  plain normalized text, scripts removed, status and control fields unchanged, no instruction
  field exists, and no log record contains it.
- `test_research_story_is_idempotent_for_the_same_inputs`: repeated runs produce the same status,
  context/evidence IDs, and bounds (timestamps may differ).
- `test_activity_starts_no_workflow_or_temporal_client`: AST/import checks plus
  `ActivityEnvironment` runs.
- `test_research_modules_do_not_import_workflow_or_worker_modules`: AST check for
  `temporalio.workflow`, `daily_chart`, `story_candidate`, `source_ingestion`,
  `story_selection`, `worker`, `services`, `web`; assert `worker.py` has no
  `StoryResearchActivities` reference.

`tests/test_config.py` (extend, keep existing tests unchanged)

- `test_research_settings_defaults_and_empty_values`: 10.0, 524288, 3, empty hosts, default user
  agent; `Settings()` equals `load_settings({})`.
- `test_research_extra_context_hosts_parsing_and_validation`: comma/whitespace/case normalisation;
  `none`/empty gives an empty set; schemes, ports, paths, IPs, single labels, local suffixes and
  overlong hosts raise.
- `test_research_network_limit_bounds`: timeout 0/61, bytes 1023/4194305, redirects -1/6 and
  non-numeric values raise; boundary values pass.
- `test_research_user_agent_bounds`: blank means default; 200 passes; 201 raises.

## Out of scope

- No `StoryGenerationWorkflow`, workflow orchestration, retry policy, task queue, candidate
  approval handoff, visibility attributes, or `worker.py` registration (053).
- No Fact/FactSet models, deterministic calculations, rankings, or analysis activity (051).
- No LLM writer, critique, `StoryPackage`, prompt, model setting, or Anthropic client (052).
- No web routes, view models, templates, CSS, `/stories`, status badges, or UI (054-055).
- No publishing, Bluesky, chart generation, rendering, OWID chart output, or `DailyChartWorkflow`
  changes.
- No Postgres tables, migrations, search attributes, or Temporal visibility work.
- Do not edit `owid.py`, `sources/`, `story_candidate.py`, `story_selection.py`,
  `source_ingestion.py`, `daily_chart.py`, `chart_loop.py`, `worker.py`, `services/`, `web/`,
  templates, CSS, or existing chart/candidate tests.
- No new dependency (no BeautifulSoup, SSRF library, aiohttp, or HTTP client rewrite); stdlib
  `html.parser` and `ipaddress` only.
- No live HTTP, DNS, LLM, or Temporal calls in tests; no Google Trends/RSS connector work.
- No semantic/LLM relevance scoring of datasets; OWID matching stays the existing deterministic
  `match_dataset`.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

- **planner**: Wrote the spec; no UI, so `frontend-design` was not applied. Next: human approves.
- Pinned: `research/` package, safe fetch with injectable resolver and per-hop validation, five
  `RESEARCH_*` settings, three standalone activities (not registered; 053 wires them).
- Assumptions: support needs a readable original article of at least 300 chars; extra context is
  at most two caller URLs gated by an exact-host allowlist (default empty); 050 has no LLM/provider
  call and adds no model/credential setting; oversized bodies are truncated, not rejected.
- Residual risk: DNS-rebinding TOCTOU is documented, not claimed solved; tests inject the resolver.
- **builder**: Added `research/` models, safe fetcher and three unregistered activities; five
  `RESEARCH_*` settings; fixtures/tests and the architecture/README notes. Research modules import
  no workflow/worker code and `worker.py` is untouched. 761 tests pass with `just lint` clean.
  Next: verifier. Unsure: added a narrow httpx access-log filter so URLs/queries cannot leak.
- **verifier**: `just lint` (Ruff, format, strict Pyright) and `just test` pass on `task/050-story-research` (755 passed). First test run showed 54 `anthropic.APIConnectionError` failures from harness `ANTHROPIC_*` base-url vars; rerun with those unset is fully green. Next: reviewer.
- **reviewer**: Needs fix (2). 1. `research/activities.py:250`: `supported` is computed over all
  contexts, so a 404 primary plus a readable allowlisted extra returns `supported` with
  `missing_article`; base support only on the primary article context (repo with a test).
- 2. `research/fetch.py:387`: `raise_for_status()` puts the full final URL and query string into the
  exception message Temporal records; raise a retryable error with a sanitized constant message.
- SSRF pre-connect and per-hop checks otherwise hold (literal/octal/IPv6-mapped/reserved blocked,
  redirects re-validated, TOCTOU documented). Next: builder fixes, then verifier.
- **builder**: Fixed both. Support now depends only on the primary article context (regression test
  with a 404 primary plus readable allowlisted extra). Retryable statuses raise
  `RetryableFetchError(status)` with a constant sanitized message and no URL/query (tests assert
  both). Lint and the full suite pass. Next: verifier.
- **builder**: Stabilized the two pre-existing `test_ingestion_starter.py` visibility tests by
  polling `list_workflows` until the expected count or a 10 s timeout; assertions unchanged. The
  file passed 20/20 repeated runs, and `just lint` / full `just test` are green. Next: verifier.
- **verifier**: Re-verify after fix loop 1: `just lint` (Ruff, format, strict Pyright) clean and `just test` 757 passed on `task/050-story-research`. First run had 54 `anthropic.APIConnectionError` failures from harness `ANTHROPIC_*` vars; rerun with those unset was fully green. Both review fixes confirmed (primary-only support, sanitized retry error). Next: reviewer.
- **reviewer**: Approved. Support now depends only on the primary article context, matching
  the spec, and the new 404-primary-plus-readable-extra test fails on the old code. Retryable
  statuses raise `RetryableFetchError` with a constant message; tests assert no URL/query.
  No other exception or log path leaks URLs or content (DNS errors are constant strings).
  Scope unchanged, lint clean, 757 tests pass.
- **scribe**: Updated README/architecture docs (research package, evidence models, safe-fetch and
  SSRF rules, five `RESEARCH_*` settings) and opened https://github.com/akonale/ai-trend-pipeline/pull/57.
  Next: human merges the PR.
- **verifier**: Re-verify after test-stability fix. `just lint` clean (Ruff, format, strict Pyright); `just test` 757 passed with harness `ANTHROPIC_*` vars unset (54 `anthropic.APIConnectionError` failures with them set). `tests/test_ingestion_starter.py` passed 5/5 repeated runs. Next: human merges PR.
- **scribe**: Pushed test-stability fix f5b9756 so CI re-runs on
  https://github.com/akonale/ai-trend-pipeline/pull/57; status back to pr-open. Next: human merges
  the PR once CI is green.
