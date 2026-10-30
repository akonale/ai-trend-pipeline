# Tech side: workflow, web, infra, development

How the system is built. For what it does and the business rules see [business.md](business.md).

Contents: [1 Temporal](#1-temporal) | [2 Web app](#2-web-app) | [3 Infra](#3-infra-and-deployment) | [4 Settings](#4-settings-configpy) | [5 Development](#5-development)

## 1. Temporal

### Workflow (`daily_chart.py`)

`DailyChartWorkflow.run(DailyChartInput) -> DailyChartResult`, task queue
`DAILY_CHART_TASK_QUEUE = "daily-chart-task-queue"`.

```
 drafting ─▶ awaiting_decision ─┬─ decide(approved) ─▶ posting ─▶ approved
                                ├─ decide(rejected) ─────────────▶ rejected
                                └─ 20 h timeout ─────────────────▶ expired
```

- Steps: HN stories and `pick_topic` (skipped with a manual topic), `fetch_owid_dataset`,
  `draft_chart_with_critique`, `render_chart_png`, wait, `post_chart_to_bluesky`.
- Signal `decide(ApprovalDecision)`: ignored unless status is `awaiting_decision` and no decision
  exists yet. Approve and reject are one signal (`approved: bool`, `note`, `decided_by`).
- Query `review_state() -> ReviewState` (status, `ReviewRequest`, note, `requested_by`,
  `decided_by`, `options`, `option_notes`, `post_uri`). Web and CLI read all run state from this query.
  `post_uri` is set only on the final `approved` update (the URI `post_chart_to_bluesky` returned);
  it adds no command, so replay is safe. Querying a closed run replays its history with the current
  code, so runs approved before this field existed also get it (covered by a test).
- Timeouts: I/O activities 60 s, 3 attempts (`IO_ACTIVITY_TIMEOUT`, `IO_RETRY_POLICY`); LLM
  activities 120 s, 3 attempts (`LLM_ACTIVITY_TIMEOUT`, `LLM_RETRY_POLICY` in `chart_loop.py`);
  approval wait `APPROVAL_TIMEOUT` = 20 hours. Non-retryable `ApplicationError` types:
  `NoMatchingDataset`, `InvalidChartDraft`, `InvalidVegaLiteSpec`, `InvalidChartCritique`,
  `ImageTooLarge`, `ChartFileMissing`, `BlueskyAuthFailed`. `TopicNotInCatalog` is retryable.
- Requester and approver: `requested_by` is in `DailyChartInput` (also stored as workflow memo
  `requested_by` for the cap); `decided_by` is in the signal. Both are copied into `ReviewState`.

### Story candidate workflow (`story_candidate.py`)

`StoryCandidateWorkflow.run(StoryCandidateInput) -> StoryCandidateResult`, task queue
`CANDIDATE_TASK_QUEUE = "story-candidate-task-queue"`. It runs no activities.

```
 awaiting_review ─┬─ approve(CandidateDecision) ─▶ approved
                  ├─ reject(CandidateDecision)  ─▶ rejected
                  └─ review_timeout_seconds     ─▶ expired
```

- Signals `approve` and `reject` take a `CandidateDecision` (`note`, `decided_by`); the first
  terminal decision wins and later ones are logged and ignored. Query
  `review_state() -> CandidateReviewState` (status, candidate, note, `decided_by`, `decided_at`);
  `decided_at` comes from `workflow.now()`.
- Workflow id: `candidate_id_for(source, external_id)` = `candidate-v1-` + the first 16 hex chars
  of SHA-256 over `"<source.value>\x1f<external_id>"`. The gateway starts with
  `id_conflict_policy=USE_EXISTING` and `id_reuse_policy=REJECT_DUPLICATE`, and treats
  `WorkflowAlreadyStartedError` as success, so one source/external id always has one run.
- Search attributes: keyword `CandidateStatus` and `CandidateSource`, both set at start
  (`awaiting_review`, `source.value`); the terminal transition upserts the final status. Pending
  candidates are a visibility query (`WorkflowType = 'StoryCandidateWorkflow' AND CandidateStatus =
  'awaiting_review'`); list rows read title, proposed angle and observed time from workflow memo.
- `temporal_client.ensure_candidate_search_attributes(client)` registers both attributes and
  ignores `ALREADY_EXISTS`; the worker calls it once after connecting.

### Source ingestion workflow (`source_ingestion.py`)

`SourceIngestionWorkflow.run(SourceIngestionInput) -> SourceIngestionResult`, task queue
`SOURCE_INGESTION_TASK_QUEUE = "source-ingestion-task-queue"`, one long-running run per enabled
source with id `source_ingestion_workflow_id(source)` = `source-ingestion-v1-<source>`.

- Loop: `poll_hn_source` (registry activity name) -> drop `processed_ids` -> `prefilter_events` ->
  `select_story_candidates` if the batch is non-empty -> mark the batch processed -> start one
  `StoryCandidateWorkflow` child per selection (deterministic id, `CANDIDATE_TASK_QUEUE`,
  `ParentClosePolicy.ABANDON`, `REJECT_DUPLICATE`) -> stop check -> continue-as-new -> wait
  `poll_interval_seconds` with `workflow.wait_condition`.
- State lives only in workflow history: an opaque `cursor` (HN: highest numeric external id), at
  most 1000 unique processed ids in first-seen order, `polls_in_run` and `total_polls`.
  Continue-as-new carries all of it in `SourceIngestionInput` after `max_polls_per_run` (default 50)
  or when the server suggests it; `polls_in_run` resets while `total_polls` accumulates.
- Candidate budget (`SourceIngestionInput.candidate_budget`, default 20, 1-500; also
  `budget_day` and `candidates_started`): counted per source per UTC day, using `workflow.now()`.
  `budget_remaining(budget, started, day, now)` returns the full budget for a new/missing day and
  clamps at zero; `_roll_budget` resets the counter when the UTC day changes. Before selection, a
  zero remainder skips `select_story_candidates` entirely (no LLM call), does **not** mark the batch
  processed, and logs `budget_exhausted` once for that day; the batch is re-evaluated after midnight.
  Otherwise the selector and `_start_candidates` get `max_candidates=min(max_candidates, remaining)`,
  and each successful child start consumes one. Duplicates consume none. A batch whose selector
  ran is marked processed in full even when starts were truncated. Continue-as-new carries the day
  and count, and the exhaustion-log day; terminating the chain starts a fresh count. `ingestion_state`
  reports `candidate_budget`, `budget_day`, `candidates_started` and `budget_remaining`.
- Activity failures: poll 60 s / 3 attempts, selector 120 s / 3 attempts. After retries an
  `ActivityError` is logged with `error_type` (never the exception text) and the loop continues: a
  failed poll only advances counters, and a failed selection still marks its batch processed.
  Cancellation propagates. Duplicate child starts (`WorkflowAlreadyStartedError`) are logged and
  ignored.
- Signals and query: `stop` returns `SourceIngestionResult(reason="stopped")` after the current
  activity; `ingestion_state() -> SourceIngestionState` reports source, cursor, processed ids,
  counters, `last_poll_at`, the last event/new/selected counts and the budget fields. Children are
  abandoned, so stop, cancellation and continue-as-new never close a review.
- Operational logs use the fixed `candidate_ingestion.*` events from `ingestion_logging.py`
  (below). Workflow code logs through `workflow.logger` (replay-safe); `poll_hn_source` and
  `poll_fake_source` log through `activity.logger`. The helper rejects forbidden field names
  (titles, URLs, summaries, angles, reasons, prompts, payloads, credentials) and non-primitive
  values, and truncates strings to 256 characters. No `print()`.
- Operator command: `just ingest` (dev) or `docker compose -f infra/compose.prod.yaml --env-file
  .env.prod exec worker python -m ai_trend_pipeline.ingestion_starter` (prod) calls
  `SourceIngestionService.ensure()`, which starts one workflow per `SOURCE_ENABLED` source with
  `USE_EXISTING` and `ALLOW_DUPLICATE`. Worker startup does not start ingestion; the worker only
  registers the workflow and activities. Budget, poll limit, interval and review timeout are baked
  into the input when the workflow starts, so changing them requires stop plus ensure. Procedures:
  [ingestion](../runbooks/ingestion.md) and [search attributes](../runbooks/search-attributes.md).

### Structured ingestion log events

The message is the event value; the payload carries `event`, `source` and the listed fields.

| Event | Emitted from | Level | Fields (besides `event`, `source`) |
| --- | --- | --- | --- |
| `candidate_ingestion.started` | workflow, start of `run` | INFO | `polls_in_run`, `total_polls` |
| `candidate_ingestion.poll` | workflow, after a successful poll | INFO | `poll`, `event_count`, `new_event_count`, `batch_size`, `cursor`, `budget_remaining` |
| `candidate_ingestion.poll_failed` | workflow, retries exhausted | ERROR | `poll`, `error_type` |
| `candidate_ingestion.events_normalized` | `poll_hn_source`, `poll_fake_source` | INFO | `requested_limit`, `event_count` |
| `candidate_ingestion.selection` | workflow, selector returned | INFO | `batch_size`, `selected_count`, `max_candidates`, `budget_remaining` |
| `candidate_ingestion.selection_failed` | workflow, retries exhausted | ERROR | `batch_size`, `error_type` |
| `candidate_ingestion.selection_outside_batch` | workflow | WARNING | `external_id` |
| `candidate_ingestion.candidate_started` | workflow | INFO | `external_id`, `candidate_id`, `budget_remaining` |
| `candidate_ingestion.candidate_duplicate` | workflow | WARNING | `external_id`, `candidate_id` |
| `candidate_ingestion.budget_exhausted` | workflow, once per budget day | WARNING | `day`, `budget`, `candidates_started` |
| `candidate_ingestion.continue_as_new` | workflow, before continue-as-new | INFO | `total_polls`, `processed_count`, `cursor` |
| `candidate_ingestion.stopped` | workflow, before returning | INFO | `total_polls` |

### Activities and models

| Activity | Class / function | Input -> output |
| --- | --- | --- |
| `fetch_hn_top_stories` | `HNActivities` | `HNTopStoriesInput` -> `HNTopStoriesOutput` |
| `poll_hn_source` | `HNSourceActivities` | `SourcePollInput` -> `SourcePollOutput` |
| `poll_fake_source` | `FakeSourceActivities` | `SourcePollInput` -> `SourcePollOutput` |
| `select_story_candidates` | `StorySelectionActivities` | `SelectStoryCandidatesInput` -> `SelectStoryCandidatesOutput` |
| `pick_topic` | `TopicActivities` | `PickTopicInput` -> `PickTopicOutput` |
| `fetch_owid_dataset` | `OwidActivities` | `FetchOwidInput` -> `OwidTable` |
| `write_chart_spec` | `ChartSpecActivities` | `WriteChartSpecInput` -> `ChartSpec` |
| `critique_chart` | `CritiqueActivities` | `CritiqueChartInput` -> `ChartCritique` |
| `render_chart_png` | module function | `RenderChartInput` -> `RenderChartOutput` |
| `post_chart_to_bluesky` | `BlueskyActivities` | `PostChartInput` -> `PostChartOutput` |
| `fetch_context` | `StoryResearchActivities` | `FetchContextInput` -> `FetchContextOutput` |
| `match_owid_evidence` | `StoryResearchActivities` | `MatchEvidenceInput` -> `MatchEvidenceOutput` |
| `research_story` | `StoryResearchActivities` | `ResearchInput` -> `ResearchOutput` |

Activity classes take their clients in `__init__` (httpx / Anthropic), so tests pass fakes with
`MockTransport`.

### Sources (`sources/`)

`sources/models.py` holds the frozen, `extra="forbid"` boundary models (`SourceName`,
`SourceEvent`, `SourcePollInput`, `SourcePollOutput`) and the `SourceConnector` protocol. Fields are
bounded (IDs 1-128, titles 1-200, URLs at most 1024 and only `http`/`https`, at most 4 metrics and
100 events), timestamps are timezone-aware UTC, and one output holds one source with unique
`external_id` values. `sources/normalize.py` is the single normalization path both connectors use.
The static registry (`sources/registry.py`) maps registered names to labels and poll activity names
and imports only the models, so workflow code can import it without I/O. It lists `hn` then `fake`.
`sources/hn.py` wraps `HNActivities.fetch_hn_top_stories` in `HNSourceConnector` and
`HNSourceActivities.poll_hn_source`; `sources/fake.py` holds `FakeSourceConnector`,
`DEFAULT_FAKE_ITEMS` (two fixed stories, ids `fake-1001` and `fake-1002`) and
`FakeSourceActivities.poll_fake_source`. The fake source is registered and its activity is always
registered on the ingestion worker, but `SOURCE_ENABLED` still defaults to `hn`, so it never runs by
default and must never appear in `.env.prod`; it is a dev/test source used to prove pluggability.
Both poll activities log `events_normalized` with counts only. The chart workflow and chart queue
are unchanged.

### Research (`research/`)

`research/models.py` holds the frozen, `extra="forbid"` contracts (`StoryInput`, `StoryContext`,
`EvidenceSource`, `ResearchInput`, `ResearchOutput`, failures and notes) with bounded text, at most
3 contexts, 4 evidence items, 8 failures/notes and 16 000 characters of context text.
`research/fetch.py` is the safe fetcher: it accepts only `http`/`https`, no userinfo, ports `80`/`443`, and
rejects `localhost`, single-label and local/internal host names. It resolves every hop through an
injectable resolver and rejects any non-global destination (private, loopback, link-local,
multicast, reserved, unspecified, carrier-grade NAT and IPv4-mapped IPv6), follows at most
`RESEARCH_MAX_REDIRECTS` redirects with full re-validation, honours exact approved-host lists,
streams at most `RESEARCH_MAX_RESPONSE_BYTES`, and normalizes HTML/plain text with the stdlib
parser. `httpx` resolves again at connect time, so a DNS-rebinding time-of-check gap remains; the
pre-connect checks are the mitigation, not an absolute guarantee. `research/activities.py` exposes
three read-only, idempotent activities. `supported` requires a fetched original article of at least
300 characters; a dataset match alone cannot support a story, and a missing catalog match is only a
note. OWID problems degrade to notes. The activities are not registered on any worker yet (053 owns
registration and retry policies), and no LLM or new credential is involved.

### Determinism, idempotency, replay

- No I/O, clock, randomness or LLM calls in workflow code. The workflow uses `workflow.now()` and
  `workflow.info().workflow_id`. Third-party imports go inside
  `workflow.unsafe.imports_passed_through()`.
- Idempotency: `render_chart_png` names the file by spec hash and replaces atomically. Posting:
  the Bluesky record key is a TID built from `created_at` (`workflow.now()`) and the workflow id
  (`make_tid`); the activity creates a session, calls `getRecord` for that key, and returns
  `already_posted=True` if it exists, otherwise uploads the blob and `createRecord`s with that key.
- Replay compatibility: new fields on workflow inputs and state must be optional with defaults
  (`requested_by`, `style`, `options`, `option_notes`, `decided_by` all are), so open runs from older
  code still replay.
- Pydantic everywhere: `temporal_client.connect(settings)` uses `pydantic_data_converter`.

### Worker (`worker.py`)

`main()` loads settings, connects with retry (30 s), registers the candidate search attributes,
builds the Anthropic, HN, OWID and Bluesky clients and runs four workers: `hello-task-queue`
(`HelloWorkflow`, `say_hello`), the daily chart queue (`DailyChartWorkflow` and the seven
activities from `daily_chart_activities`), `story-candidate-task-queue` (`CANDIDATE_WORKFLOWS =
[StoryCandidateWorkflow]`, no activities), and `source-ingestion-task-queue` (`INGESTION_WORKFLOWS =
[SourceIngestionWorkflow]`, with `poll_hn_source`, `poll_fake_source` and
`select_story_candidates` from `ingestion_activities`). It exits at startup if `ANTHROPIC_API_KEY`,
`BLUESKY_HANDLE` or `BLUESKY_APP_PASSWORD` is missing. Worker startup does not start ingestion;
`just ingest` does. `DraftChartWorkflow` (in `chart_loop.py`) is not registered.

## 2. Web app

### Layering

```
 web routes ─┐
             ├──▶ services ──▶ Temporal client ──▶ workflows ──▶ activities
 CLI ────────┘     (charts)                        (no web or service imports)
```

| Folder | Holds | Never does |
| --- | --- | --- |
| `src/ai_trend_pipeline/services/` | Use cases: start a chart, get its state, decide, style profiles. Plain async classes taking Pydantic models | Read HTTP requests, render HTML, print |
| `src/ai_trend_pipeline/web/app.py` | `create_app(settings)` factory: wires routes, templates, static files | Business logic |
| `src/ai_trend_pipeline/web/routes/` | One module per page (`home.py`, `runs.py`, `candidates.py`, `settings.py`, `health.py`). Parse input, call one service, render a template | Call Temporal or the LLM directly |
| `src/ai_trend_pipeline/web/templates/` | Jinja templates; `partials/` for htmx fragments | Logic beyond loops and ifs |
| `src/ai_trend_pipeline/web/view_models.py` | Pydantic models shaped for templates (`HomeView`, `RunView`, `SettingsView`, `CandidateDetailView`, `MetricChip`, `build_*_view`) | I/O |
| `src/ai_trend_pipeline/config.py` | One `Settings` model read from env vars, passed in, not imported as a global | Read env vars anywhere else |

- Routes get services and settings through FastAPI dependencies, so tests swap in fakes. Route tests
  never need Temporal running.
- The signed-in user is one parsing path, `optional_user` (user or `None`). `current_user` wraps it
  and raises 401 on `None`; it guards every route that changes something, plus `GET /settings`.
- No JavaScript build step: htmx and a small CSS file, served from `web/static/`.

### Dependencies (`web/dependencies.py`)

`get_settings`, `get_templates` (read `app.state`), `get_style_store` (builds one
`PostgresStyleStore` per process, no I/O), `get_style_service`, `get_chart_service` (connects to
Temporal once, caches the client on `app.state`, returns `ChartService(TemporalChartRunGateway,
settings, styles=...)`), `optional_user`, `current_user`, `build_request_shell`, `shell_context`.

`optional_user` returns `CurrentUser(email=DEV_USER_EMAIL)` in local mode and, in cloudflare mode, the
lower-cased header email or `None` when it is missing or blank. `build_request_shell(request, *,
pending=None)` builds the `ShellView` (`build_shell(path, email, auth_mode, pending=...)`); it also
reads a badge a route stored on `request.state` because this Starlette version runs context
processors after the route context. `shell_context(request)` is the context processor passed to
`Jinja2Templates` in `create_app`: it delegates to `build_request_shell` and returns `{"shell": ...}`,
so every `TemplateResponse`, including error re-renders, gets the shell with no route code. Neither
helper does I/O, and the badge is built only from an already-fetched `CandidatePage`.

### Routes

| Method | Path | Purpose | Notes |
| --- | --- | --- | --- |
| GET | `/health` | Health page | no Temporal call |
| GET | `/` | Step 1: topic box and chips (GET form to `/new/style`) | `?topic=` and the fourteen option and style params prefill; options come back as hidden carry fields; never validates |
| GET | `/new/style` | Step 2: per-chart options plus an editable style panel prefilled from the saved style | uses `optional_user`; 303 to `/` without a topic, 400 (step 1 with an error) over 200 characters; never fails on the style read; without a user or when the store is down the page shows a note instead of the "Save as my default" checkbox |
| POST | `/runs` | Validate form, start a run, 303 to `/runs/{id}` | uses `current_user`; 400 bad options, or empty topic or over `MAX_TOPIC_LENGTH` (200 characters), 429 cap, 409 id exists, 503 style store down (read or "Save as my default" write); style overrides and `save_default` are form fields |
| GET | `/runs/{id}` | Run page | 404 page if unknown |
| GET | `/runs/{id}/status` | htmx fragment, polled every 2 s while `polling` | |
| GET | `/runs/{id}/chart.png` | The PNG | 404 unless the file is inside `CHART_OUTPUT_DIR` |
| POST | `/runs/{id}/decision` | Send approve/reject, 303 to `?sent=1` | uses `current_user`; 400 bad value, 409 not awaiting, 404 unknown |
| GET | `/candidates` | Story candidate inbox | `?page=` is an opaque forward cursor; `?notice=` accepts only `approved`, `rejected` or `closed`; 400 with a friendly notice when the token is invalid, 503 full-shell unavailable page; the header badge shows `N` (or `N+` with a next page) only on a successful first page |
| GET | `/candidates/{id}` | Candidate detail and decision form | 404 not-available page for a missing candidate, 410 status-specific page for approved/rejected/expired; renders only an `http`/`https` source link |
| POST | `/candidates/{id}/decide` | Approve or reject, 303 to the inbox | uses `current_user`; 400 invalid decision re-renders the detail with an error; repeated/raced decisions 303 with `notice=closed`; 404 unknown |
| GET, POST | `/settings` | Style profile form | uses `current_user`; 400 invalid, 503 store down |

Which routes touch Postgres: `POST /runs` (style lookup, and the "Save as my default" write), `/settings`, and `GET /new/style`
(read-only; if the store is down the page still renders 200 with a notice). Other routes work
while Postgres is down.

### Services

- `ChartService` (`services/charts.py`): `start`, `show`, `decide` (waits for the result, used by
  the CLI), `submit_decision` (signal only, used by the web), `chart_image`. Talks to a
  `ChartRunGateway` protocol: `start`, `handle(wf_id)` (query `review_state`, signal, `result`),
  `count_started_since`. Errors: `RunNotFound`, `RunAlreadyExists`, `NotAwaitingDecision`,
  `DailyCapReached`, `ChartImageNotFound`.
- `TemporalChartRunGateway` (`services/temporal_gateway.py`): starts with `id_conflict_policy=FAIL`
  and memo `requested_by`; maps gRPC NOT_FOUND to `RunNotFound`. The daily cap runs
  `list_workflows("WorkflowType = 'DailyChartWorkflow' AND StartTime >= '<00:00 UTC>'")` and counts
  matching memos. Runs without the memo are not counted.
- `CandidateService` (`services/candidates.py`): `start`, `list_pending`, `show`, `decide` over a
  `CandidateGateway` protocol (`start`, `list_pending`, `handle`). Errors: `CandidateNotFound`,
  `CandidateClosed`, `InvalidPageToken`, `CandidateUnavailable`. `start` derives the candidate id
  and defaults the timeout from `Settings.candidate_review_timeout_s`; `show` and `decide` query
  `review_state` first and map a missing candidate to `CandidateNotFound` and a non-`awaiting_review`
  state to `CandidateClosed`.
- `TemporalCandidateGateway` / `TemporalCandidateHandle` (`services/temporal_gateway.py`): start
  with `id_conflict_policy=USE_EXISTING` and `id_reuse_policy=REJECT_DUPLICATE`, set both search
  attributes and the memo keys `candidate_title`, `candidate_proposed_angle`,
  `candidate_observed_at`; `list_pending` runs the visibility query with opaque URL-safe base64 page
  tokens (default size 20, max 100). Any `RPCError` from visibility becomes `CandidateUnavailable`.
- `candidates.html` backed by `build_candidate_inbox_view` / `build_candidate_unavailable_view`
  (`web/view_models.py`): one ledger of whole-row links with a `.source-badge` pill, relative
  detection time and a one-line angle; server-side forward paging plus "Back to newest", no
  JavaScript. `page_title` (default `Story candidates`) sets the `<title>`. Empty and unavailable
  cards share `.candidate-state` (`.candidate-state--empty|unavailable` add the top edge). A redirect
  from a decision lands on `#candidate-notice`, which carries `tabindex="-1"` and
  `.candidate-flash` so the notice gets focus without a script.
- `candidate_detail.html` / `candidate_not_available.html` backed by
  `build_candidate_detail_view` / `build_candidate_not_available_view`: one `.candidate-back` link
  above the heading, title, `.source-badge` and safe source link first, then summary, angle, reason
  and metric chips, with a two-form `.candidate-decisions` rail. Copy, metric labels, the safe URL
  check, the notice allow-list and the 404/410 copy all live in `web/view_models.py`; the decision
  route calls only `CandidateService.decide`.
- `StyleService` / `StyleStore` protocol (`services/styles.py`); `PostgresStyleStore`
  (`services/postgres_style_store.py`): psycopg 3, one short connection per call, schema and table
  created on first use and retried if setup fails; `psycopg.Error` or `OSError` become
  `StyleStoreUnavailable`; a stored profile that no longer validates is ignored (defaults used).
  Table `topic_studio.style_profiles(email primary key, profile jsonb, updated_at)`.

### Auth (`optional_user`, `current_user`)

| `AUTH_MODE` | Behaviour |
| --- | --- |
| `local` (default) | everyone is `dev@localhost` |
| `cloudflare` | email from header `Cf-Access-Authenticated-User-Email`, lower-cased; missing header gives 401 on POST routes and `GET /settings` (it needs an email); other GET pages render with no user chip |

The Access JWT is not verified. That is acceptable only because prod publishes the web port on
`127.0.0.1` and the only way in is the tunnel, which sits behind Access; nothing else can set the
header. Do not expose the web port publicly.

### Templates, view models, design

Templates (`base.html`, `home.html`, `new_style.html`, `run.html`, `settings.html`, `health.html`,
`run_not_found.html`, `partials/` incl. `stepper.html`) hold only loops and ifs; labels, badges, option lines and error
text are computed in `view_models.py` (`STATUS_LABELS`, `badge_for`, `option_lines`,
`options_error_message`, `style_error_message`). `ShellView` (brand, `NavItem`s with a `current` flag
and optional `badge`, `user_email`, `env_tag` "dev" in local mode, `SHELL_TAGLINE`) feeds
`base.html`: a skip link, `<header class="site-header">` with brand, `<nav aria-label="Main">`
("Candidates", "New chart", "Your style", `aria-current="page"` on the current one) and the
`.user-chip` (only when an email is known), `<main id="main">`, and `<footer class="site-footer">` with
the tagline and the `.dev-tag`. The brand always links to `/`. A `NavBadge` (`text`, `label`) renders
as an `aria-hidden` `.nav-badge` count plus a visually hidden sentence; `pending_nav_badge` derives it
only from the fetched first inbox page (`N`, or `N+` with a next page). The body is a flex column so
the footer sticks to the bottom; no JavaScript, the nav wraps on phones. From `48rem` up the shared
header and footer measure widens to `50rem` (with a tighter tablet nav gap) so brand, nav and chip
stay on one row. Static assets in `web/static/`: `app.css` and vendored `htmx.min.js` (2.0.4).

`app.css` is a neo-brutalist "graph paper and ink" system: 3px ink borders, hard offset shadows
(no blur), flat fills, a faint 24px grid on the page, and the chart frame (ruler ticks, marigold
shadow) as the one bold element. Tokens are CSS custom properties on `:root` (`--color-*`,
`--font-*`, `--text-*`, `--space-*`, `--border-*`, `--radius-*`, `--shadow-*`), redefined under
`@media (prefers-color-scheme: dark)`. Every `--color-*` is a literal 6-digit hex in both themes,
so `tests/test_design_tokens.py` can parse `app.css` and check each text/background pair against
WCAG AA (4.5 for text, 3 for borders and focus). Text on a fill token uses `--color-on-fill`, never
`--color-text`. Element defaults sit in `:where(...)` so component classes win without fights.
Fonts are system stacks (no web fonts). The stepper (`.stepper`, `.step`, `.step--done|current|upcoming`;
labels never break mid-word) and `.btn--secondary` (the Back button) are used by the flow; `.check`
is still reserved. All candidate rules live in one labelled section at the end of `app.css` and use
tokens only: `.nav-badge`, `.source-badge` (a pill built on the `.badge` stamp),
`.candidate-state`, `.candidate-flash`, `.candidate-back`, the inbox row hover/active pair inside one
`@media (hover: hover)` block, and `.candidate-decisions`. Reduced motion turns off transitions and
the progress animation, plus the candidate row transition.

Flow: `StepperView` / `build_stepper(current, topic_href)` (view_models) shape the four steps
(Topic, Style, Review, Done); shown on step 1, step 2 and the run page (`RUN_STEP` maps status to
step 3 or 4). On the run page the stepper sits outside `#run-status`; the `/runs/{id}/status`
fragment re-emits it with `hx-swap-oob="true"` so it moves from 3 to 4 when polling ends. All flow
state travels in the query string or form fields (`flow_query`); no session.

Run page states (flow steps 3 and 4, `build_run_view`): the `<h1>` lives inside `#run-status` so it
changes as polling moves on (Drafting your chart, Review your chart, Sending your decision, Posting
to Bluesky, Posted to Bluesky, Rejected, Expired). Polling states show a stage message in the
`role="status"` block; end states show an outcome card with `Make another chart` (and `View the
post` when approved). The link comes from `bluesky_post_url`, which accepts only a `did:plc` post
URI of the exact shape our activity makes and builds `https://bsky.app/profile/<did>/post/<rkey>`;
anything else gives no link and the page says so. The anchor uses `target="_blank"
rel="noopener noreferrer"` (it is a click-through link, not a loaded asset). There is no `stage`
field: draft sub-steps run inside `chart_loop`, so honest per-stage progress would touch two modules
and was left out; the page shows only the stages `ReviewState.status` carries.

## 3. Infra and deployment

### Image (`Dockerfile`)

`python:3.12.16-slim`, `uv` 0.13.6 copied in, `uv sync --frozen --no-dev` into `/opt/venv`,
`fonts-liberation` installed (needed for chart fonts), non-root user `app` (uid 1000), `/data/charts`
owned by it. Default command is the worker; compose overrides it for web.

### Compose

| | `infra/compose.dev.yaml` | `infra/compose.prod.yaml` (project `ai-trend-prod`) |
| --- | --- | --- |
| Services | `postgres`, `temporal` | `postgres`, `temporal`, `temporal-ui`, `worker`, `web`, `cloudflared` |
| Postgres | `postgres:16.15`, 127.0.0.1:5432 | `postgres:16.15`, no published port |
| Temporal | `temporalio/temporal:1.9.1` `start-dev` (in memory), 127.0.0.1:7233 and 8233 | `temporalio/auto-setup:1.29.7` on Postgres (`DB=postgres12`) |
| UI | built into dev server (8233) | `temporalio/ui:2.51.1`, 127.0.0.1:18233 |
| Worker and web | run from the host (`just dev`, `just web` on 8000) | built from `Dockerfile`; web on 127.0.0.1:18000 |
| Tunnel | none | `cloudflare/cloudflared:2026.10.4`, token from env |
| Volumes | `postgres-data` | `postgres-data`, `charts` (shared by worker and web at `/data/charts`) |

Dev Postgres is used only for style profiles; the dev Temporal server keeps nothing across
restarts. Prod Temporal stores its history in the same Postgres server (its own databases, created
by auto-setup) next to the `topic_studio` schema in the default `postgres` database. The worker
gets Anthropic and Bluesky settings, the web gets `AUTH_MODE: cloudflare`, `WEB_HOST: 0.0.0.0`,
`DATABASE_URL` and `TEMPORAL_UI_URL`. Prod `DATABASE_URL` is built in compose in key/value form
from `POSTGRES_PASSWORD`, so the password may not contain spaces, quotes or backslashes. The worker
gets no `DATABASE_URL`.

### Env files and deploy

- Dev: `.env` (gitignored), loaded by `set dotenv-load := true` in the `justfile`.
- Prod: `.env.prod` on the server only (gitignored); keys are listed in `docs/runbooks/deploy.md`.
- `just deploy` = `git pull --ff-only`, then compose under `env -i PATH HOME` with
  `--env-file .env.prod`. `env -i` matters because dotenv-load exports the dev `.env` and shell
  variables beat `--env-file`.
- Never read or commit either file.

### Cloudflare Tunnel and Access

`cloudflared` runs `tunnel run` with a token. Two public hostnames (placeholders `app.example.com`
-> `http://web:8000`, `temporal.example.com` -> `http://temporal-ui:8080`) each have an Access
application with one-time-PIN login: the app hostname allows the owner and invited people, the
Temporal UI hostname the owner only. Set `TEMPORAL_UI_URL` in `.env.prod` to the public Temporal
hostname. See `docs/runbooks/cloudflare.md`; operations in `docs/runbooks/deploy.md`.

### CI and bootstrap

`.github/workflows/ci.yml`: on pull requests and pushes to `main`, `uv sync --locked`,
`just lint`, `just test`. Server setup: `infra/cloud-init.yaml` (new Ubuntu 24.04 server: user, SSH
hardening, Docker, GitHub CLI, uv, Temporal CLI) or `infra/bootstrap.sh` (the same on an existing
server).

## 4. Settings (`config.py`)

`Settings` is a frozen Pydantic model returned by `load_settings()`; it is the only reader of
environment variables.

| Env var | Field | Purpose | Default |
| --- | --- | --- | --- |
| `TEMPORAL_ADDRESS` | `temporal_address` | Temporal frontend | `localhost:7233` |
| `TEMPORAL_UI_URL` | `temporal_ui_url` | base for run links | `http://localhost:8233` |
| `WEB_HOST` | `web_host` | bind address of `python -m ai_trend_pipeline.web` (port fixed at 8000) | `127.0.0.1` |
| `CHART_OUTPUT_DIR` | `chart_output_dir` | where PNGs are written and served from | unset (`charts`) |
| `ANTHROPIC_API_KEY` | `anthropic_api_key` | LLM calls (worker) | unset |
| `BLUESKY_HANDLE` | `bluesky_handle` | posting account (worker) | unset |
| `BLUESKY_APP_PASSWORD` | `bluesky_app_password` | app password (worker) | unset |
| `BLUESKY_SERVICE` | `bluesky_service` | PDS base URL | `https://bsky.social` |
| `AUTH_MODE` | `auth_mode` | `local` or `cloudflare` | `local` |
| `DAILY_CHART_CAP` | `daily_chart_cap` | runs per person per UTC day, at least 1 | `10` |
| `DATABASE_URL` | `database_url` | Postgres for style profiles | local dev Postgres URL |
| `SOURCE_ENABLED` | `source_enabled` | comma-separated registered sources; `none` disables all | `hn` |
| `SOURCE_POLL_LIMIT` | `source_poll_limit` | raw items requested and events returned per poll, 1-100 | `30` |
| `SOURCE_POLL_INTERVAL_S` | `source_poll_interval_s` | seconds between source ingestion polls, 30-3600 | `300` |
| `SOURCE_POLL_TIMEOUT_S` | `source_poll_timeout_s` | HN source poll HTTP timeout, over 0 and at most 60 | `15.0` |
| `CANDIDATE_REVIEW_TIMEOUT_S` | `candidate_review_timeout_s` | story candidate review timeout, 1-604800 seconds | `172800` |
| `SOURCE_CANDIDATE_BUDGET` | `source_candidate_budget` | new candidate workflows per source per UTC day, 1-500 | `20` |
| `RESEARCH_FETCH_TIMEOUT_S` | `research_fetch_timeout_s` | article fetch timeout in seconds, over 0 and at most 60 | `10.0` |
| `RESEARCH_MAX_RESPONSE_BYTES` | `research_max_response_bytes` | streamed response byte cap, 1024-4194304 | `524288` |
| `RESEARCH_MAX_REDIRECTS` | `research_max_redirects` | redirects followed with full re-validation, 0-5 | `3` |
| `RESEARCH_USER_AGENT` | `research_user_agent` | request user agent, stripped 1-200; blank means the default | `ai-trend-pipeline-research/0.1` |
| `RESEARCH_EXTRA_CONTEXT_HOSTS` | `research_extra_context_hosts` | comma-separated exact hosts allowed for extra context; empty or `none` disables | empty |

Secrets use `SecretStr`. Compose-only keys (`POSTGRES_PASSWORD`, `CLOUDFLARE_TUNNEL_TOKEN`,
`APP_URL` documentation only) are not read by the app.

## 5. Development

- Setup: `uv sync`; `just dev` (compose Postgres and Temporal, then the worker); `just web`;
  `just ingest` (idempotently ensure enabled source ingestion workflows); `just chart [--topic
  "..."] [--id ...]`, `just show|approve|reject <id>`, `just hello`, `just lint`, `just fmt`,
  `just test`, `just down`.
- Tests (`tests/`): TDD, no live calls. Activities use `httpx.MockTransport` and recorded
  fixtures; web routes use `app.dependency_overrides` with fake services (`tests/style_fakes.py`
  has `InMemoryStyleStore`); workflows use `WorkflowEnvironment.start_time_skipping()`
  (`test_daily_chart.py`, `test_chart_loop.py`, `test_hello.py`, `test_source_ingestion.py`).
  Ingestion tests gate each activity attempt so they step polls without wall-clock waits. Candidate
  visibility and gateway tests use `WorkflowEnvironment.start_local` with the candidate search
  attributes pre-registered, because the time-skipping server has no list-workflows; the ingestion
  end-to-end visibility test does the same. Expiry is tested with a 1 s timeout. Guard tests check
  `CLAUDE.md` structure, compose files, CI and the committed ingestion runbooks. The two-source
  `start_local` test runs the real HN poll (recorded fixture), the real fake poll, the real
  selector (mock Anthropic client) and `CandidateService` over `TemporalCandidateGateway`; it
  asserts one inbox row per source and unchanged approve/reject, proves the budget is per source,
  and proves one failing source does not block the other. `test_ingestion_logging.py` captures
  workflow and activity records and asserts the fixed event set, the forbidden-field guard and that
  no source title or credential appears.
- Lint: Ruff (line length 100) and Pyright strict; no `# type: ignore` or `Any` without a comment.
- Agent relay: planner, builder, verifier, reviewer, scribe with human checkpoints; see
  `docs/mvp-plan.md` and `CLAUDE.md` (do not copy them).

Add an activity: write tests with a fixture; Pydantic input and output; make it idempotent; define
it on a class that takes its clients; register it in `worker.daily_chart_activities`; call it from
the workflow with a timeout and retry policy; new workflow fields optional with defaults.

Add a route: parse input only; call one service obtained via a dependency; shape data in
`view_models.py`; template with no logic; test with a fake service.

Add a setting: field on `Settings` and a line in `load_settings` (the only env reader); pass
`Settings` in; add to compose and `docs/runbooks/deploy.md` if prod needs it; update `README.md`.

Pitfalls (details in `docs/history.md`): shell variables beat `--env-file`, so deploy under `env -i`;
LLM activities use `tool_choice` auto; the postgres image bump needed a collation refresh; the
Postgres password format restriction; `vl-convert` needs `fonts-liberation`; the worker will not
start without LLM and Bluesky credentials.
