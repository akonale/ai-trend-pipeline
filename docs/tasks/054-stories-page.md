# 054: Unified stories status page with 48-hour retention

Status: built

## Goal

Add `GET /stories`, a unified status ledger for candidate and story-generation workflows read from
the shared 053 visibility attributes, with the five primary statuses, simple filters, forward
pagination and a rolling 48-hour cutoff. Make Stories the primary nav destination, keep
`/candidates` as the pending-action inbox, apply the same cutoff to both pages, and add a light
htmx refresh only while a visible row is still active.

## Design decisions

The `frontend-design` skill was read and its plan -> critique -> build process applied. The brief and
the existing system win: the "graph paper and ink" tokens in `web/static/app.css`, the shared shell,
and the 045-047 candidate ledger/proof-sheet language are reused as-is. No new palette, typeface,
CDN asset, chart, analytics, sidebar, JavaScript framework or build step.

**Subject, audience, job.** One editor opening a newsroom status board: see what is waiting, being
written, awaiting final review, ready, or closed; switch views; open a story. It is a ledger, not a
kanban board or dashboard. The five status stamps are the only colour signals, and the filter bar is
the only control strip.

**Palette (existing tokens only, light / dark).**

- Page ground: `--color-bg` `#E3ECDF` / `#191C30`; grid `--color-grid` `#D2DFCC` / `#23273F`.
- Row surface: `--color-surface` `#FFFFFF` / `#262A44`.
- Title/body: `--color-text` `#15172B` / `#E3ECDF`; muted meta/detail: `--color-muted` `#4A4F5E` /
  `#B4BACB`.
- Borders and hard shadows: `--color-border` / `--color-shadow` `#15172B` / `#E3ECDF`.
- Accent links: `--color-accent` `#2448D8` / `#8DAEFF`; on accent:
  `--color-accent-contrast` `#FFFFFF` / `#15172B`; focus `--color-focus` `#2448D8` / `#FFC93C`.
- Active filter: `--color-highlight` `#FFC93C` with `--color-on-fill` `#15172B`.
- Status stamps use the existing `.badge` fills and ink text `--color-on-fill` `#15172B`:
  Pending `--color-neutral` `#D3D6DE`; Writing `--color-info` `#A8C8FF`; Review
  `--color-warning` `#FFC93C`; Ready `--color-success` `#7DDBA3`; Closed `--color-danger`
  `#FF9A9A`.
- Source badge: `.badge .source-badge` (neutral pill), exactly as the candidate pages.

**Type (existing families and scale).** `--font-display` (Arial Black stack) for the `<h1>` and row
titles; `--font-body` for meta and detail. Row title `--text-lg` (1.25rem), weight 900, line-height
1.15, letter-spacing -0.01em. Detail `--text-base` (1rem), one line, ellipsized. Filter labels and
meta `--text-sm` (0.875rem). No all-caps, no mono micro-labels, no numbered markers, no `->` glyphs.

**Spacing, borders and shadows.** `--space-1` 0.25rem through `--space-7` 4rem; `--border-width`
3px, `--border-thin` 2px, `--radius-sm` 4px, `--radius-pill` 999px, `--shadow-sm` 4x4,
`--shadow-lift` 6x6, `--duration-press` 80ms. All new rules use these tokens; no raw hex.

**Layout.** One ledger column inside the existing `main` (max 44rem). Same structure at 360, 768 and
1280; only wrapping changes. Filters sit above the results region and wrap on phones.

```
main (max-width 44rem)
+--------------------------------------------------------------+
| Stories                                                      |  h1, display
| 12 stories from the last 48 hours.                           |  .meta count
| [All] [Pending] [Writing] [Review] [Ready] [Closed]          |  filter links, active = marigold
| +----------------------------------------------------------+ |
| | Headline of the story, wrapping when long                | |  row title, display/lg
| | Starting story generation.                               | |  optional detail, muted, one line
| | [Hacker News] [Writing] Updated 2 minutes ago            | |  source pill + status stamp + time
| +----------------------------------------------------------+ |  whole row is one <a>
| | ...                                                      | |
| +----------------------------------------------------------+ |
| [Older stories]                       [Back to newest]       |  pager
+--------------------------------------------------------------+
```

**Principles.** Status must be scannable in one pass: one stamp per row, text plus colour, never
colour alone. Filters are obvious switchboard pills. The 48-hour window is explained by the count
line and the empty copy; rows show `Updated ...`, never a countdown or an expiry timer. The one
memorable move is the repeated status stamp across the ledger; rows stay quiet slips, reusing
`.candidate-row`. No per-row action buttons.

**Design critique (checked before building).** Rejected the skill's default cream/terracotta and
acid-accent palettes, the SaaS card grid, a kanban/column board, traffic-light charts, a sidebar, a
sticky filter rail, all-caps status labels, mono micro-labels, numbered markers and arrow glyphs.
The status stamp and the filter switchboard carry the identity; everything else is the existing
candidate ledger.

**Component notes (add to the candidate section of `app.css`; tokens only, no raw hex).**

- `.status-filters`: `display: flex; flex-wrap: wrap; gap: var(--space-2); margin: var(--space-4)
  0 var(--space-3); padding: 0; list-style: none;`
- `.status-filter`: `display: inline-flex; align-items: center; padding: var(--space-1)
  var(--space-3); color: var(--color-text); background: var(--color-surface); border:
  var(--border-thin) solid var(--color-border); border-radius: var(--radius-pill); box-shadow:
  2px 2px 0 0 var(--color-shadow); font-size: var(--text-sm); font-weight: 800;
  text-decoration: none;`
- `.status-filter--active`: `background: var(--color-highlight); color: var(--color-on-fill);
  box-shadow: var(--shadow-none);`
- Hover only inside the existing `@media (hover: hover)` block: `.status-filter:hover { background:
  var(--color-neutral); }` (the active pill keeps its marigold). No transitions, so no new
  reduced-motion entry; the global `:focus-visible` outline is the focus treatment.
- `.story-row__detail`: same rules as `.candidate-row__angle` (muted, `min-width: 0; overflow:
  hidden; white-space: nowrap; text-overflow: ellipsis;`). Reuse `.candidate-inbox`,
  `.candidate-row`, `.candidate-row__title`, `.candidate-row__meta` and `.pager` unchanged.
- Empty/unavailable states reuse `.candidate-state` with `--empty` (info blue top edge) and
  `--unavailable` (neutral top edge). The notice reuses `.candidate-flash` but does not take focus.
- No new colours, no new shadow/radius values, no new animation.

**Copy (exact strings live in `view_models.py`, never in the template).**

- `<title>`/`<h1>`: `Stories`. Count: `12 stories from the last 48 hours.`, `1 story ...`,
  `20 or more stories in Writing from the last 48 hours.`; filtered counts insert `in Pending`,
  `in Writing`, `in Review`, `in Ready` or `in Closed`.
- Filters: `All`, `Pending`, `Writing`, `Review`, `Ready`, `Closed`.
- Status stamps: `Pending`, `Writing`, `Review`, `Ready`, `Closed`.
- Updated: `Updated 2 minutes ago` (reuse `relative_time`); omit the `<time>` when the attribute is
  missing. Never show a countdown or "expires in".
- Optional detail: the bounded `StoryStatusDetail`; when absent, render nothing.
- Synthetic details: approved candidate with no story -> `Starting story generation.`; approved
  candidate whose execution closed without a story -> `Story generation did not start.`; candidate
  expired -> `The review window closed.`; candidate rejected -> no synthetic detail.
- Title fallback: `Untitled story`. Source fallback: `Unknown source` for blank; otherwise
  `source_label` as on the candidate pages.
- Empty (All): heading `No stories yet`, body `Stories appear here when a source finds a candidate.
  They leave 48 hours after they were first detected.`, action `Check again` -> `/stories`.
- Empty (filtered): heading `No stories in this view`, body `Nothing in the last 48 hours has this
  status. Stories leave this page 48 hours after they were first detected.`, action `Show all
  stories` -> `/stories`.
- Unavailable: heading `Stories are unavailable`, body `We couldn't reach the story service.
  Nothing is lost.`, action `Try again` -> `/stories`.
- Invalid page token: `That page link is out of date. Start from the newest stories.` with
  `Back to newest` -> the same filter without `page`.
- Polling hint: `Updating while stories are in progress.`

**View model contract (pure, no I/O; all labels, URLs, classes and fallbacks).**

- `StoryRowView`: `href`, `title`, `source_label`, `status_label`, `status_class`, `detail`,
  `updated_iso: str | None`, `updated_label`.
- `StoryFilterLink`: `label`, `href`, `current`.
- `StoryListView`: `page_title` (`Stories`), `heading`, `filters`, `rows`, `count_text`,
  `empty`, `empty_heading`, `empty_body`, `empty_action_label`, `empty_action_href`,
  `unavailable`, `unavailable_heading`, `unavailable_body`, `retry_href`, `retry_label`,
  `notice`, `older_href`, `older_label`, `newest_href`, `newest_label`, `polling`, `polling_href`,
  `polling_trigger` (`every 10s`), `polling_hint`, `results_id` (`stories-results`).
- `STORY_STATUS_LABELS` and `STORY_STATUS_BADGE_CLASSES` are the single mapping over
  `PrimaryStoryStatus`: pending -> `badge--muted`, writing -> `badge--progress`, review ->
  `badge--attention`, ready -> `badge--success`, closed -> `badge--danger`. No template conditionals.
- `build_story_list_view(page, *, now, filter, page_token, notice="")` and
  `build_story_unavailable_view(*, now, filter=ALL)`.
- `polling = any(row.status in {PENDING, WRITING, REVIEW} for row in rows)` and false when empty,
  unavailable or filtered to Ready/Closed. `polling_href` is
  `/stories/rows?filter=<key>[&page=<token>]`; the current page token is used, not the next token.
- Row href: `/stories/{quote(detail_id, safe="")}` (055 adds the route). Source label reuses
  `source_label`; detail is re-bounded with `story_visibility.bounded_detail`; untrusted text is
  escaped by Jinja.
- Filter hrefs reset pagination: `/stories` for All, `/stories?filter=<key>` otherwise. Pager hrefs
  preserve the filter; the active filter gets `aria-current="true"`.

## Service and gateway contract

New module `services/stories.py` (not imported by workflows or activities). Temporal remains the
system of record; no tables.

### Models and pure helpers

- `StoryFilter(StrEnum)`: `all`, `pending`, `writing`, `review`, `ready`, `closed`.
  `story_filter(value: str) -> StoryFilter` maps unknown/blank to `all`.
- `ListStoriesRequest` (`extra="forbid"`): `filter: StoryFilter = ALL`, `limit: int = 20` (1..100),
  `page_token: str | None`.
- `StoryRow` (final): `candidate_id`, `detail_id`, `title`, `source`, `status: PrimaryStoryStatus`,
  `status_detail`, `created_at`, `updated_at: datetime | None`.
- `StoryListPage`: `stories: list[StoryRow]`, `next_token: str | None`.
- `StoryVisibilityRow` (internal, built by the gateway): `workflow_type`, `workflow_id`,
  `candidate_id`, `title`, `source`, `internal_status`, `status_detail`, `created_at`,
  `updated_at`, `execution_closed: bool`.
- `encode_story_page_token(token) -> str`, `decode_story_page_token(value, *, filter) ->
  StoryPageToken`; `StoryPageToken` = `{v: 1, filter, raw: str | None, seen: list[str]}` base64url
  JSON without padding. Garbage, wrong version, filter mismatch or `seen` over
  `MAX_SEEN_CANDIDATES = 500` raises `InvalidStoryPageToken`. Invalid raw cursors reuse
  `services.candidates.decode_page_token` and raise the same error.
- `story_visibility_query(filter, cutoff) -> str`, `retained(created_at, cutoff) -> bool`
  (`created_at > cutoff`), `collapse_story_rows(rows, *, now) -> list[StoryVisibilityRow]`,
  `matches_story_filter(row, filter) -> bool`, `sort_story_rows(rows)` (newest `created_at` first,
  then `candidate_id`), `build_story_row(row) -> StoryRow`.
- Constants: `STORY_START_GRACE = timedelta(minutes=1)`, `MAX_STORY_SCAN_PAGES = 5`,
  `MAX_SEEN_CANDIDATES = 500`, `GENERATION_STARTING_DETAIL`, `STORY_START_FAILED_DETAIL`.

### Visibility query

One query per scan page, both workflow types, cutoff on `StoryCreatedAt`, filter as a prefilter:

```
(WorkflowType = 'StoryCandidateWorkflow' OR WorkflowType = 'StoryGenerationWorkflow')
AND StoryCreatedAt > '<cutoff RFC3339 UTC, e.g. 2026-10-28T12:00:00Z>'
AND <filter predicate>
```

Filter predicates: All none; Pending
`(StoryStatus = 'awaiting_review' OR CandidateStatus = 'awaiting_review')`; Writing
`(StoryStatus IN ('approved','researching','analyzing','writing','checking') OR CandidateStatus =
'approved')`; Review `StoryStatus = 'review'`; Ready `StoryStatus = 'ready'`; Closed
`(StoryStatus IN ('rejected','unsupported','failed','expired') OR CandidateStatus IN
('rejected','expired'))`. The service re-applies `matches_story_filter` after collapse; the query is
only a prefilter.

### Cutoff

`StoryStatusService.list_stories(request, *, now)` requires an aware UTC `now` (naive raises) and
passes `cutoff = now - STORY_UI_RETENTION` to the gateway. `retained()` uses strict `>`: an item
created at 47:59:59 before `now` is visible; one at exactly 48:00:00 is absent. The same cutoff is
never computed inside templates or from `StoryUpdatedAt`. The route computes `now` once and passes
the same value to the business and the view builder.

### De-duplication and five-status mapping

Group raw rows by `StoryCandidateId`; a `StoryGenerationWorkflow` row always wins over its
`StoryCandidateWorkflow` row (one row per candidate, never two). The chosen row supplies
`detail_id`, title, source, detail and status. `created_at` comes only from `StoryCreatedAt`
(fallback only to the paired row's `StoryCreatedAt`); `StoryUpdatedAt` and workflow start time never
reset the retention clock.

- Candidate `awaiting_review` -> Pending.
- Candidate `approved`: if a generation row exists it wins (usually Writing). If not, and the
  candidate execution is still running (or its `StoryUpdatedAt` is within `STORY_START_GRACE`) ->
  Writing with `Starting story generation.`; if the execution is closed and older than the grace ->
  Closed with `Story generation did not start.` This is the generation-start-failure mapping.
- Candidate `rejected` / `expired` -> Closed (expired keeps `The review window closed.` when the
  visibility detail is absent).
- Story `researching`/`analyzing`/`writing`/`checking` and candidate `approved` -> Writing; story
  `review` -> Review; `ready` -> Ready; `rejected`/`unsupported`/`failed`/`expired` -> Closed.
- Unknown or missing status -> Closed, never an error. Missing title/source/detail uses the view
  fallbacks; a row missing `StoryCandidateId` or `StoryCreatedAt` is dropped (it cannot be placed in
  the window).

### Pagination and filter tokens

`TemporalStoryStatusGateway.list_stories(request, *, now, cutoff)` decodes the page token, then
scans up to `MAX_STORY_SCAN_PAGES` raw visibility pages of `page_size=request.limit`, starting at
`token.raw`:

1. Map each `WorkflowExecution` to `StoryVisibilityRow` through the seven shared attributes; fall
   back to candidate memos `candidate_title` and `candidate_observed_at` and to
   `CandidateSource`/`CandidateStatus` for pre-053 candidate rows. Read memos only when an
   attribute is absent; this is part of the visibility row, not a workflow query.
2. Drop rows not `retained()`, rows missing `candidate_id`/`created_at`, and ids in `token.seen`.
3. Collapse the accumulated rows by `StoryCandidateId`, apply `matches_story_filter`, sort.
4. Stop when more than `limit` rows are available, the scan cap is reached, or visibility is
   exhausted.
5. Emit the first `limit` rows. If more remain, `next_token = {filter, raw: <encoded cursor at the
   start of the page that produced the overflow>, seen: token.seen + emitted candidate ids}`. A
   re-scan of that page skips already-emitted ids, so the overflow and cross-page duplicates are
   handled without a per-row query. If the scan cap is reached with exactly `limit` rows, the token
   continues from the last raw cursor. If `seen` would exceed 500, omit the next token (bounded).
6. The *current* page's token does not contain its own emitted ids, so the htmx refresh of that page
   can still see a newly started story replace its `approved` candidate placeholder.

Any `RPCError` from `list_workflows` maps to `StoryStatusUnavailable`. No
`get_workflow_handle`, `query`, `result`, `count_workflows` or per-row call exists in this path.

### Wiring and `/candidates`

- `services/stories.py` also exports `StoryStatusService(gateway)` and the errors
  `StoryStatusUnavailable` / `InvalidStoryPageToken`.
- `web/dependencies.py` adds `get_story_status_service`, caching the Temporal client on
  `app.state.temporal_client` (shared with the chart/candidate services) and mapping connect-time
  `RPCError`/`OSError`/`RuntimeError` to `StoryStatusUnavailable`.
- `web/routes/stories.py` adds `GET /stories` and `GET /stories/rows` (the fragment). Both are thin:
  parse the allow-listed filter and page token, call one service, render.
- `app.py` includes the router and registers one `StoryStatusUnavailable` handler: `stories.html`
  with status 503 for full pages, `partials/story_results.html` with status 200 for
  `/stories/rows` (htmx only swaps successful responses, and a failed poll must stop).
- `/candidates` keeps its own service but gains the same cutoff: `CandidateService.list_pending`
  takes `now`, computes `cutoff = now - STORY_UI_RETENTION`, and calls
  `gateway.list_pending(request, cutoff)`. `pending_candidates_query(cutoff)` becomes
  `WorkflowType = 'StoryCandidateWorkflow' AND CandidateStatus = 'awaiting_review' AND
  StoryCreatedAt > '<cutoff>'`. Rows, count text and the nav badge therefore all exclude items at
  or past 48 hours; pagination never shows a hidden row.
- `build_shell` nav order becomes `Stories` (`/stories`, active for `/stories` and `/stories/...`),
  `Candidates` (`/candidates`, active for `/candidates...`, badge only from the existing
  cutoff-filtered first inbox page), `New chart`, `Your style`. Brand still links to `/`. No new
  count query; Stories itself has no badge.

## Active-row polling rule

Poll only when at least one visible row on the current page/filter has `pending`, `writing` or
`review`. Then the `#stories-results` element carries `hx-get="<polling_href>"`,
`hx-trigger="every 10s"` and `hx-swap="outerHTML"`. The fragment re-renders the count, rows, notice,
states and pager, never the filter bar or heading. If the refreshed rows are all `ready`/`closed`,
the fragment omits `hx-get` and htmx stops. Empty, unavailable, invalid-token and Ready/Closed-only
views never poll. No full-page reload, no focus movement, no `aria-live` announcements.

## Acceptance criteria

- [ ] `GET /stories` renders the shared shell and `Stories`, six filter links with a correct active
      state, and rows showing title, source pill, one of five status stamps, optional detail,
      `Updated ...` time and a whole-row `/stories/{id}` link; it works without JavaScript, and
      untrusted text, long titles and missing optional metadata render safely.
- [ ] `StoryStatusService` lists both workflow types through one visibility query, maps every
      internal state to the five primary statuses, de-duplicates by `StoryCandidateId` preferring
      the generation workflow, maps a generation-start failure to Closed, and never resets
      `StoryCreatedAt` from `StoryUpdatedAt` or start time.
- [ ] The strict 48-hour cutoff (`created_at > now - 48h`) applies to views, filters, pagination,
      counts and both pages: an active or terminal item is visible at 47:59:59 and absent at
      48:00:00, and generation-start does not extend the window.
- [ ] Pagination is an opaque forward cursor carrying the filter and seen candidate ids; filter
      links and pager links preserve the filter; an invalid or mismatched token gives a friendly
      400 (or 200 fragment) notice; the list path makes no per-row workflow query.
- [ ] Stories is the first nav item and is active on `/stories*`; Candidates remains the
      pending-action inbox, active on `/candidates*`, with its cutoff-filtered pending badge; the
      same cutoff applies to `/candidates` rows, pagination and count.
- [ ] Polling runs every 10 seconds only while a visible row is Pending, Writing or Review, and
      stops on terminal-only, empty, unavailable and Ready/Closed views. Existing tokens only,
      360/768/1280 light and dark have no horizontal overflow, focus is visible, reduced motion is
      respected, and `just lint` / `just test` pass.

## Files to touch

- `src/ai_trend_pipeline/services/stories.py` (new: `StoryFilter`, request/page/row models,
  query/token/collapse/filter helpers, `StoryStatusService`, errors)
- `src/ai_trend_pipeline/services/temporal_gateway.py` (`TemporalStoryStatusGateway`,
  `list_stories`, visibility-row mapping, scan/de-dup, cutoff-aware `pending_candidates_query` and
  `list_pending`)
- `src/ai_trend_pipeline/services/candidates.py` (`list_pending(request, *, now)` and cutoff
  plumbing; no other candidate behaviour)
- `src/ai_trend_pipeline/web/dependencies.py` (`get_story_status_service`)
- `src/ai_trend_pipeline/web/routes/stories.py` (new: `/stories`, `/stories/rows`)
- `src/ai_trend_pipeline/web/routes/candidates.py` (pass `now` into `list_pending`; unchanged copy)
- `src/ai_trend_pipeline/web/app.py` (include the router, `StoryStatusUnavailable` handler)
- `src/ai_trend_pipeline/web/view_models.py` (`build_shell` Stories nav + active state;
  `StoryListView`/`StoryRowView`/`StoryFilterLink`, status label/class mapping, builders, copy)
- `src/ai_trend_pipeline/web/templates/stories.html` (new)
- `src/ai_trend_pipeline/web/templates/partials/story_results.html` (new)
- `src/ai_trend_pipeline/web/static/app.css` (status filters, story detail line, hover entry; no new
  tokens)
- `tests/test_story_status_service.py` (new)
- `tests/test_web_stories.py` (new)
- `tests/test_web_candidates.py` (cutoff and fake-signature updates)
- `tests/test_candidate_gateway.py` (query/cutoff assertions)
- `tests/test_web_shell.py`, `tests/test_web_candidate_polish.py` (nav order/current flags)
- `docs/architecture/tech.md` (routes, services, templates/design, candidate cutoff)
- `docs/architecture/README.md` (module map and state table note)
- `README.md` (web routes/nav description)
- `docs/tasks/054-stories-page.md` (status and handoff log only)

## Tests to write

`tests/test_story_status_service.py` (pure helpers and service; no Temporal)

- `test_every_candidate_and_story_internal_state_maps_to_one_primary_status` - all four candidate
  and all ten story status strings, unknown -> closed.
- `test_collapse_prefers_the_generation_workflow` - candidate `approved` plus story `researching`
  with the same `StoryCandidateId` produces one Writing row using the story id/title/source.
- `test_generation_start_failure_maps_to_closed_after_the_grace` - approved candidate, no story,
  closed execution, updated older than the grace -> Closed with `Story generation did not start.`;
  running or within the grace -> Writing with `Starting story generation.`.
- `test_created_at_never_comes_from_updated_at_or_start_time` - story created 47h ago and updated
  now stays a 47h-old row; a row at the cutoff is absent.
- `test_retention_boundary_is_strict_for_active_and_terminal_rows` - 47:59:59 visible and 48:00:00
  absent for pending, writing, review, ready and closed.
- `test_filters_match_after_collapse` - approved candidate only under Writing, awaiting only under
  Pending, ready/closed under their own filters, all includes every row.
- `test_visibility_query_covers_all_filters_and_the_cutoff` - exact query text for all six filters,
  `StoryCreatedAt >` with the UTC stamp, the workflow-type OR, and the status IN lists.
- `test_page_token_round_trip_and_rejects_stale_filter_or_garbage` - encode/decode, filter
  mismatch, malformed base64/JSON, bad version and seen over the cap.
- `test_pagination_does_not_repeat_rows_across_pages` - fake raw pages where a candidate and its
  story straddle pages; page 2 has no duplicate and the overflow row appears once.
- `test_missing_metadata_is_dropped_or_uses_fallbacks` - missing candidate id/created at dropped;
  missing title/source/detail use fallbacks; empty status detail is dropped.
- `test_list_uses_one_gateway_call_and_no_handle` - a fake gateway whose `handle` raises still lists
  rows with one `list_stories` call.
- `test_pending_count_helper_excludes_items_at_the_cutoff` - 47:59:59 counted, 48:00:00 not, for
  the candidate badge and the Stories count.
- `test_service_failure_maps_to_story_status_unavailable` - `RPCError` from visibility becomes the
  typed error.

`tests/test_web_stories.py` (route and template, fake service; no Temporal)

- `test_stories_page_renders_five_statuses_and_rows` - status labels/classes, source pill, detail,
  `Updated ...`, `/stories/{id}` hrefs, shared shell.
- `test_filter_links_and_active_state` - six links, `aria-current="true"` only on the selected one,
  All resets the URL.
- `test_pager_preserves_the_filter` - Older link includes `filter=` and the encoded page token;
  Back to newest keeps the filter and drops the page.
- `test_empty_all_and_filtered_states` - distinct copy and actions; no list markup.
- `test_unavailable_page_is_503_with_shell` - copy, retry link, skip link/header/footer.
- `test_invalid_page_token_is_a_friendly_400` - notice and Back to newest, shell intact.
- `test_untrusted_text_is_escaped` - `<script>`/`<img onerror>` in title, detail and source render
  escaped with no raw tags.
- `test_stale_metadata_and_long_text` - no updated time, no detail, unknown/blank source, very long
  title and detail render without error or clipping.
- `test_polling_only_while_active_rows_are_visible` - pending/writing/review row sets `hx-get` and
  `every 10s`; ready-only and closed-only pages have no `hx-get`; empty, unavailable and filter=ready
  have none.
- `test_fragment_returns_only_the_results_region` - `/stories/rows` has `id="stories-results"`, no
  `<html>`, no filter bar; active rows still poll.
- `test_fragment_service_failure_is_200_and_stops_polling` - partial unavailable copy, no `hx-get`;
  full page stays 503.
- `test_route_makes_one_service_call_and_no_per_row_query` - fake service/gateway records one list
  call; `handle` is never called.
- `test_page_works_without_javascript` - filters and rows are plain anchors; no `<button>` or
  script-dependent navigation.
- `test_nav_marks_stories_current_and_candidates_on_the_inbox` - nav order Stories, Candidates, New
  chart, Your style; `/stories/*` marks Stories, `/candidates/*` marks Candidates, `/health` none.

`tests/test_web_candidates.py` (update)

- `test_inbox_applies_48_hour_cutoff_to_rows_badge_and_count` - fake page/time: 47:59:59 visible,
  48:00:00 absent from rows, count text and badge.
- Update the fake `list_pending` signature and all existing inbox tests to pass the cutoff.

`tests/test_candidate_gateway.py` (update)

- `test_pending_query_includes_cutoff` - exact `pending_candidates_query(cutoff)` string.
- `test_list_pending_passes_the_cutoff` - fake client records the query containing the stamp.
- Update `test_list_pending_maps_rpc_error_to_unavailable` for the new signature.

`tests/test_web_shell.py`, `tests/test_web_candidate_polish.py` (update)

- `test_build_shell_fields` / `test_build_shell_nav_order_and_current_flags` - four-item order,
  hrefs `["/stories", "/candidates", "/", "/settings"]`, Stories active on `/stories` and
  `/stories/story-v1-abc`, Candidates active on `/candidates...`, no badge on Stories.

## Screenshot and accessibility checks

- Screenshot `/stories` at 360x800, 768x800 and 1280x800, light and dark: All with one row of each
  of the five statuses (including one long title, one long detail and one missing updated time),
  each filter, empty All, empty filtered, invalid token and unavailable. Include `/candidates` with
  the pending badge. Attach the images to the PR or handoff.
- At 360px confirm `document.documentElement.scrollWidth <= window.innerWidth`; filter pills wrap
  without clipping; row title wraps; detail ellipsizes; badges and time stay inside the row; the
  four-item nav wraps cleanly.
- Keyboard-only: skip link -> Stories -> Candidates -> New chart -> Your style -> each filter ->
  each row -> pager; the 3px `--color-focus` outline is visible at every stop and Enter opens a
  row. The filter group has an accessible name and the active filter is announced via
  `aria-current`.
- With JavaScript enabled, confirm the active-row page refreshes only `#stories-results` every 10
  seconds, keeps focus where it was, and a Ready/Closed-only page stops polling. With JavaScript
  disabled, repeat filters, paging and row navigation.
- With reduced motion enabled, confirm no new animation runs and the existing row press remains
  disabled. At 200% zoom recheck 360px wrapping.
- Confirm ink on the five status fills, muted detail/meta on surface and ground, and active-filter
  ink on marigold meet WCAG AA; `tests/test_design_tokens.py` stays green and the new CSS adds no
  raw hex.

## Out of scope

- `/stories/{id}`, the story detail page, final Approve/Reject, package rendering, closed-reason
  pages and decision notices (055).
- Changing workflows, `story_candidate.py`, `story_generation.py`, `story_status.py`,
  `story_visibility.py`, search-attribute registration, ingestion, worker, config or Postgres.
- Backfilling `StoryCreatedAt` on pre-053 executions, deleting histories, cleanup workflows or any
  database table.
- Publishing, Bluesky, chart generation, rendering, media, analytics dashboards, charts, a sidebar,
  a JavaScript framework/build step, global search, sorting, source filters, bulk actions, a real
  total-pending query or any per-row workflow query.
- Changing candidate decision semantics, copy, detail/redirect behaviour or chart routes/tests.
- New colours, fonts, external assets/CDNs, icons, or visual-regression tooling; committing
  screenshots.
- `docs/history.md` (the scribe handles the merged-task entry).

## Assumptions

- `StoryCreatedAt > now - 48h` is strict: exactly 48:00:00 is absent, 47:59:59 is visible. The web
  service computes the cutoff; 053 already caps review deadlines to the same instant.
- Pre-053 candidate executions without `StoryCreatedAt` are treated as outside the UI window on
  both pages and leave the pending counts; no backfill is added. New candidates get the attribute
  at start, and 053's patched terminal upsert adds it to old open candidates when decided.
- A generation-start failure is detected from visibility only: approved candidate, no generation-
  row for the same `StoryCandidateId`, candidate execution closed, and `StoryUpdatedAt` older than
  the 60-second grace. The grace absorbs visibility eventual consistency; no workflow query or
  result read is used.
- The Candidates nav badge stays the only pending badge and is derived from the already-fetched,
  cutoff-filtered first `/candidates` page. Stories shows a count for its current filter only; no
  extra `count_workflows` call is added.
- `?filter=` accepts only the six allow-listed values; anything else falls back to All. A page token
  whose filter differs from the requested filter is invalid and renders the friendly notice.
- Detail links point at `/stories/{id}`, which 055 adds; 054 does not serve that route.
- The fragment returns 200 with an error state on service failure so htmx swaps it and stops
  polling; the full page returns 503. Polling interval is 10 seconds.
- Four nav items may wrap at 360/768; wrapping is acceptable and no horizontal scroll is allowed.
- Status badge colours use the existing `.badge--*` fills; no new tokens or hex values are added.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: wrote the spec with the `frontend-design` skill loaded and applied inside the existing
graph-paper/ink token system. Next: human approval, then the builder (tests first). Assumptions are
recorded above: strict `>` cutoff, pre-053 rows without `StoryCreatedAt` are outside the window, a
60-second grace detects generation-start failure from visibility, the Candidates badge stays the
only pending badge, `/stories/{id}` is 055, and polling is 10 seconds.
**builder**: Built `services/stories.py`, the Temporal visibility scanner (cutoff, de-dup, opaque token), cutoff-aware candidate listing, `/stories` + `/stories/rows`, nav, CSS and docs. Next: verifier.
**builder**: Tests first: the two new files plus the cutoff/signature updates; `just lint` and `just test` pass (912 tests, `ANTHROPIC_BASE_URL` unset to avoid the harness LLM connection error).
**builder**: Also updated five existing test call sites outside the spec's file list because `list_pending` now requires `now`/`cutoff`; no production behaviour changed there.
**builder**: Screenshots at 360/768/1280 light and dark in `/tmp/stories-shots` (not committed); no horizontal overflow, 3px focus outline on every stop, reduced motion has no row transition.
**builder**: Unsure only about the count line sitting below the filter pills (required because the polling fragment must re-render it); everything else follows the spec.
