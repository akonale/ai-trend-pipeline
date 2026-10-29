# 045: Candidate inbox page

Status: built

## Goal

Add `GET /candidates`, the inbox view models and a Jinja template that list pending story
candidates in a compact, editorial queue using the existing app shell and neo-brutalist tokens.
Each row links forward to `/candidates/{id}` (served by 046); this task adds no detail or decision
route.

## Design decisions

The `frontend-design` skill was loaded and its plan → critique → build process applied, but the
brief wins: the existing "graph paper and ink" system in `web/static/app.css` is reused as-is. No
new palette, font, CDN asset, chart, analytics, sidebar, filter set or JavaScript.

**Subject, audience, job.** One reviewer opening a newsroom-style assignment queue: scan what is
waiting, then open a story. It is an editorial ledger, not a dashboard. Story first, provenance
last; the only numbers on the page are a plain waiting count.

**Palette (existing tokens only, light / dark).**

- Page ground: `--color-bg` `#E3ECDF` / `#191C30` (the existing graph paper).
- Row and state-card surface: `--color-surface` `#FFFFFF` / `#262A44`.
- Heading, row title, body: `--color-text` `#15172B` / `#E3ECDF`.
- Angle, relative time, count: `--color-muted` `#4A4F5E` / `#B4BACB`.
- Links (invalid-page notice only): `--color-accent` `#2448D8` / `#8DAEFF`.
- Borders and hard shadows: `--color-border` / `--color-shadow` `#15172B` / `#E3ECDF`.
- Source badge: existing `.badge .badge--muted` (`--color-neutral` fill, `--color-on-fill` text).
- `--color-highlight`, `--color-info`, `--color-success`, `--color-danger` keep their existing
  meanings (current nav step, progress, success, danger); the inbox does not repurpose them.
  The unavailable state stays neutral (no red): Temporal being down is not the reviewer's error.

**Type (existing families and scale).** `--font-display` (Arial Black stack) for the `<h1>` and row
titles; `--font-body` for angle and meta. Row title `--text-lg` (1.25rem), weight 900, line-height
1.15, letter-spacing -0.01em. Angle `--text-base`, muted, one line. Badge and time `--text-sm`.
No all-caps, no mono micro-labels, no numbered markers (the list is not a sequence), no "→".

**Layout.** One column inside the existing `main` (max 44rem), no new width. Same structure at
360px and 1280px; only wrapping changes.

```
main (max-width 44rem)
┌──────────────────────────────────────────────────────┐
│ Story candidates                                     │  h1, display
│ 3 stories waiting for review.                        │  .meta count line
│                                                      │
│ ┌──────────────────────────────────────────────────┐ │
│ │ Headline of the story, wraps when long           │ │  row title, display/lg
│ │ One line of the proposed angle that ellipsizes…  │ │  muted, nowrap + ellipsis
│ │ [Hacker News]  2 hours ago                       │ │  badge + <time>, text-sm
│ └──────────────────────────────────────────────────┘ │  whole row is one <a>
│ ┌──────────────────────────────────────────────────┐ │
│ │ …                                                │ │
│ └──────────────────────────────────────────────────┘ │
│ [Older stories]                 [Back to newest]     │  .pager, wraps at 360px
└──────────────────────────────────────────────────────┘
```

**Component notes (add to `app.css` after the run-page block).**

- `.candidate-inbox`: `list-style: none; margin: var(--space-5) 0 0; padding: 0; display: flex;
  flex-direction: column; gap: var(--space-3);`
- `.candidate-row`: `display: flex; flex-direction: column; gap: var(--space-2); padding:
  var(--space-4) var(--space-5); color: var(--color-text); background: var(--color-surface);
  border: var(--border-width) solid var(--color-border); border-radius: var(--radius-sm);
  box-shadow: var(--shadow-sm); text-decoration: none; min-width: 0; transition: transform
  var(--duration-press) ease-out, box-shadow var(--duration-press) ease-out;`
  Hover (inside the existing `@media (hover: hover)` block): `transform: translate(-2px, -2px);
  box-shadow: var(--shadow-lift);` and underline the title with the existing 2px
  `text-decoration-thickness`. Active: `transform: translate(4px, 4px); box-shadow:
  var(--shadow-none);` — the row presses like the app's buttons.
- `.candidate-row__title`: display font, `--text-lg`, 900, line-height 1.15, wrapping
  (`overflow-wrap: anywhere` is already on `body`).
- `.candidate-row__angle`: `color: var(--color-muted); font-size: var(--text-base); min-width: 0;
  overflow: hidden; white-space: nowrap; text-overflow: ellipsis;` (full text stays in the DOM for
  assistive tech).
- `.candidate-row__meta`: `display: flex; flex-wrap: wrap; align-items: center; gap:
  var(--space-2) var(--space-3); color: var(--color-muted); font-size: var(--text-sm);`
- `.pager`: `display: flex; flex-wrap: wrap; gap: var(--space-3); margin-top: var(--space-5);`;
  its links use the existing `.btn .btn--secondary` classes.
- Add `.candidate-row` to the existing `@media (prefers-reduced-motion: reduce)` selector list
  (no new media query). The global `:focus-visible` outline is the focus treatment; do not remove
  it.

**Design critique (checked before building).** Rejected the skill's default cream/terracotta
palette (the app's green graph paper and ink blue stay), the SaaS card grid (this is one ledger
list of links, not equal rounded cards), broadsheet hairlines (the app uses 3px ink), all-caps
eyebrows, mono micro-labels and numbered markers. The single memorable move is the whole-row slip
that presses under the finger; everything else stays quiet.

**Copy (exact strings live in `view_models.py`, never in the template).**

- `<h1>`: `Story candidates`. Count: `3 stories waiting for review.` / `1 story waiting for review.`
- Empty: heading `Nothing waiting for review`, body `New candidates show up here when a source
  finds a story. Check back soon.`, action `Check again` → `/candidates`.
- Temporal unavailable: heading `The inbox is unavailable`, body `We couldn't reach the candidate
  service. Stories that were already waiting are not lost.`, action `Try again` → `/candidates`.
- Invalid page token: `That page link is out of date. Start from the newest stories.` with
  `Back to newest` → `/candidates`.
- Missing fields: title `Untitled story`, angle `No angle proposed.` When `observed_at` is absent
  the `<time>` element is omitted and only the source badge shows.
- Source labels: `hn` → `Hacker News`, `fake` → `Test source`, unknown → its raw value. Badge
  markup: `<span class="badge badge--muted"><span class="visually-hidden">Source: </span>…</span>`.
- Pager links: `Older stories`, `Back to newest`.

**View model contract (pure, no I/O).**

- `CandidateRow`: `href`, `title`, `angle`, `source_label`, `observed_iso: str | None`,
  `observed_label: str`.
- `CandidateInboxView`: `heading`, `count_text`, `rows`, `empty`, `empty_heading`, `empty_body`,
  `empty_action_label`, `empty_href`, `unavailable`, `unavailable_heading`,
  `unavailable_body`, `retry_href`, `notice`, `older_href`, `newest_href`.
- `build_candidate_inbox_view(page: CandidatePage, *, now: datetime, page_token: str | None = None,
  notice: str = "") -> CandidateInboxView` and `build_candidate_unavailable_view(now: datetime)
  -> CandidateInboxView`.
- `relative_time(observed_at, now)` rules: `< 1 min` or future → `just now`; `< 60 min` →
  `1 minute ago` / `N minutes ago`; `< 24 h` → `1 hour ago` / `N hours ago`; `< 7 d` →
  `1 day ago` / `N days ago`; older → `on 12 Oct 2026` (`%d %b %Y`). Naive timestamps are treated
  as UTC.
- Detail href: `/candidates/{quote(candidate_id, safe="")}`. Older href: `/candidates?page=
  {quote(next_token, safe="")}`. Newest href: `/candidates`, only when a page token was supplied.

**Service/gateway.** `CandidateService.list_pending` already exists from 044. Add
`CandidateUnavailable(CandidateServiceError)` and map `RPCError` from
`TemporalCandidateGateway.list_pending` to it. `get_candidate_service` caches the Temporal client
on `app.state.temporal_client` (shared with the chart service) and maps connect-time
`RPCError`/`OSError`/`RuntimeError` to `CandidateUnavailable`. Register one app exception handler
for `CandidateUnavailable` that renders `candidates.html` with the unavailable view at status 503,
so both a failed connection and a failed visibility query stay recoverable and inside the shell.

## Acceptance criteria

- [ ] `GET /candidates` renders the shared shell (skip link, header, footer) and a page headed
      `Story candidates`; each pending row shows title, source badge, relative detection time, a
      one-line proposed angle and one whole-row link to `/candidates/{id}`. The page uses no htmx
      attributes and works with JavaScript disabled.
- [ ] `build_candidate_inbox_view` owns all labels, copy, fallbacks, relative times and URLs; the
      template contains only loops and ifs.
- [ ] Pagination is server-side through the service `next_token` exposed as `?page=<token>`
      (`Older stories` when a next token exists, `Back to newest` when a cursor is present); an
      invalid token renders a friendly 400 inbox. No previous-token back-stack is invented.
- [ ] Empty and Temporal-unavailable states are recoverable full-shell pages using existing card,
      badge and button tokens; unavailable returns 503 and offers `Try again`.
- [ ] Untrusted titles/angles are escaped, long titles wrap, long angles ellipsize to one line,
      and at 360px and 1280px there is no horizontal overflow and no clipped row or pager action.
      Focus-visible outlines remain visible on rows and pager links; light and dark modes use only
      existing tokens.
- [ ] Existing routes and shell navigation are unchanged; `just lint` and `just test` pass.

## Files to touch

- `src/ai_trend_pipeline/services/candidates.py` (add `CandidateUnavailable`)
- `src/ai_trend_pipeline/services/temporal_gateway.py` (map `RPCError` in `list_pending`)
- `src/ai_trend_pipeline/web/dependencies.py` (add `get_candidate_service`)
- `src/ai_trend_pipeline/web/routes/candidates.py` (new: route + unavailable handler)
- `src/ai_trend_pipeline/web/app.py` (include router, register the unavailable handler)
- `src/ai_trend_pipeline/web/view_models.py` (inbox view models and `relative_time`)
- `src/ai_trend_pipeline/web/templates/candidates.html` (new)
- `src/ai_trend_pipeline/web/static/app.css` (inbox and pager classes, reduced-motion entry)
- `tests/test_web_candidates.py` (new)
- `tests/test_candidate_gateway.py` (add the `RPCError` mapping case)
- `docs/architecture/tech.md` (`GET /candidates` row, `CandidateUnavailable`, `candidates.html`)

## Tests to write

- `tests/test_web_candidates.py::test_view_relative_time` — every boundary, `None`, and a future
  timestamp.
- `tests/test_web_candidates.py::test_view_source_labels_and_count` — `hn`/`fake` labels and
  singular/plural count.
- `tests/test_web_candidates.py::test_inbox_rows` — multiple rows render title, escaped detail
  href, angle, badge and `<time datetime>`.
- `tests/test_web_candidates.py::test_untrusted_text_is_escaped` — `<script>`/`<img onerror>` in
  title and angle come back escaped, with no raw tags.
- `tests/test_web_candidates.py::test_missing_fields_and_long_text` — empty title/angle and absent
  `observed_at` use fallbacks; long title/angle render without error.
- `tests/test_web_candidates.py::test_pagination_links` — next-token page shows `Older stories`
  with an encoded `?page=` href; cursor page shows `Back to newest`; first page shows neither.
- `tests/test_web_candidates.py::test_invalid_page_token` — fake gateway raises
  `InvalidPageToken`; response is 400 with the notice and newest link, shell intact.
- `tests/test_web_candidates.py::test_empty_state` — fake returns an empty page; empty copy and
  `Check again` appear and no list is rendered.
- `tests/test_web_candidates.py::test_unavailable_state` — fake raises `CandidateUnavailable`;
  response is 503 with the unavailable copy, `Try again`, and the shared shell.
- `tests/test_web_candidates.py::test_page_needs_no_javascript` — page has no `hx-` attributes and
  all navigation is plain `<a href>`.
- `tests/test_candidate_gateway.py::test_list_pending_maps_rpc_error_to_unavailable` — a stub
  client (cast to `Client`) raising `RPCError` produces `CandidateUnavailable`.

## Screenshot and accessibility checks

- Screenshot `GET /candidates` at 360×800 and 1280×800, light and dark, for: three pending rows
  including one long title and one long angle, the empty state, the invalid-token notice, and the
  unavailable state. Attach the images to the PR or handoff.
- At 360px confirm `document.documentElement.scrollWidth <= window.innerWidth` and that no row
  title, badge, time, button or pager link is clipped or hidden behind an overflow.
- Keyboard-only: skip link → header nav → each row → pager; every stop shows the 3px
  `--color-focus` outline; rows are reachable and openable with Enter.
- Disable JavaScript and repeat the list, paging and empty-state checks.
- At 200% zoom, recheck wrapping and overflow at 360px.
- Confirm contrast of muted angle/time on `--color-surface` and badge text on `--color-neutral`
  (these are existing token pairs covered by `tests/test_design_tokens.py`; the new CSS must add no
  raw hex values).

## Out of scope

- `GET /candidates/{id}`, Approve/Reject routes, the detail template, source links, metrics chips,
  summary/selection reason, and decision feedback (task 046).
- The header `Candidates` nav item, active state, pending-count badge, and consolidating shared
  candidate CSS (task 047).
- Auto-refresh, htmx polling, filters, search, sorting, or any JavaScript.
- Changes to workflows, gateway start/detail/decision methods, search attributes, ingestion,
  Postgres, or the existing chart routes/shell nav.
- New colours, fonts, external assets, charts, analytics, or a sidebar.
- A previous-page token stack for pagination.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: wrote the spec; `frontend-design` skill loaded and applied within the existing token
system. Next: human approval, then builder (tests first). Assumptions are recorded in the final
planner note: `CandidateUnavailable` is new in 045, row links target the 046 route, and pagination
is forward-cursor plus "Back to newest".

**builder**: Tests first, then inbox route/view models/template/CSS, `CandidateUnavailable` and
gateway `RPCError` mapping, app 503 handler; tech.md updated. `just lint` and full `just test` pass
(unset harness `ANTHROPIC_BASE_URL`). View model adds `older_label`, `newest_label`, `retry_label`
beyond the listed contract so no copy sits in the template. Next: verifier.
Screenshots (not committed): `/tmp/candidate-045-shots` covers 360/1280, light/dark, rows (long
title/angle), empty, invalid token and unavailable; 0 horizontal overflow, 3px focus outlines,
no-JS and 200% zoom checks pass. A `li` max-width reset was needed to counter the global prose rule.

