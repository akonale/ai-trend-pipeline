# 046: Candidate detail and decision page

Status: pr-open

## Goal

Add `GET /candidates/{id}` and `POST /candidates/{id}/decide` plus a dedicated detail template that
presents one story candidate as an editorial proof sheet (title and safe source link first, then
summary, proposed angle, selection reason and source metrics), with explicit Approve/Reject
buttons that post, redirect and return to the inbox with an allow-listed success notice. Missing
and already-closed candidates get a clear not-available page.

## Design decisions

The `frontend-design` skill was loaded and its plan → critique → build process applied. The brief
and the existing system win: the "graph paper and ink" tokens and components in
`web/static/app.css`, the shared shell, and 045's inbox patterns are reused as-is. No new palette,
font, CDN asset, chart, analytics, media preview, publishing control, sidebar or JavaScript.

**Subject, audience, job.** One reviewer opening a single story from a newsroom assignment queue,
reading enough to decide, then deciding. It is a proof sheet, not a dashboard. The story is the
page; provenance and engagement are supporting evidence, shown last.

**Palette (existing tokens only, light / dark).**

- Page ground: `--color-bg` `#E3ECDF` / `#191C30`.
- Decision card surface, metric chips: `--color-surface` `#FFFFFF` / `#262A44`.
- Headline, section headings, prose: `--color-text` `#15172B` / `#E3ECDF`.
- Source line, meta, chip labels, note: `--color-muted` `#4A4F5E` / `#B4BACB`.
- Source link, Approve button: `--color-accent` `#2448D8` / `#8DAEFF`; on accent:
  `--color-accent-contrast` `#FFFFFF` / `#15172B`.
- Reject button: existing `.btn--danger-outline` (`--color-danger` `#FF9A9A`,
  `--color-on-fill` `#15172B`) in both themes.
- Borders and hard shadows: `--color-border` / `--color-shadow` `#15172B` / `#E3ECDF`.
- Focus: `--color-focus` `#2448D8` / `#FFC93C`.
- Source badge: existing `.badge .badge--muted`. The unavailable state stays neutral; it is not
  the reviewer's error.

**Type (existing families and scale).** `--font-display` (Arial Black stack) for the `<h1>` and
section headings; `--font-body` for prose and meta. Title `--text-2xl` (existing `h1` clamp,
`2rem`–`3.25rem`), 900, line-height 1.05, `max-width: 30ch`. Section headings `--text-lg`
(1.25rem), 800, line-height 1.2. Prose `--text-base` (1rem), line-height 1.55, under 68ch. Source
line, link, chips and note `--text-sm` (0.875rem). Metric values 800 with `f"{value:,}"`. No
all-caps, no mono micro-labels, no numbered markers, no "→".

**Spacing and structure.** `--space-1` 0.25, `--space-2` 0.5, `--space-3` 0.75, `--space-4` 1,
`--space-5` 1.5, `--space-6` 2.5, `--space-7` 4rem. `--border-width` 3px, `--border-thin` 2px,
`--radius-sm` 4px, `--radius-pill` 999px, `--shadow-sm` and `--shadow-md` are the existing hard
offsets. Sections stack with `--space-5` between them.

**Layout.** One stacked column inside the existing `main` (max 44rem) at 360px. At
`min-width: 48rem` the reading column and a 17rem decision rail sit side by side; the reading
column is not boxed, only the decision is a `.card`.

```
360px                                 1280px (main still 44rem)
┌──────────────────────────┐          ┌──────────────────────────────────────────────┐
│ Headline of the story,   │          │ Headline of the story, wrapping              │
│ wrapping                 │          │ [Hacker News]  Detected 2 hours ago          │
│ [Hacker News] Detected…  │          │ Read the original story                      │
│ Read the original story  │          │ ┌ reading (1fr) ──────────┐ ┌ decide 17rem ┐ │
│                          │          │ │ Summary                 │ │ Decide this  │ │
│ Summary                  │          │ │ prose                   │ │ story        │ │
│ prose                    │          │ │ Proposed angle          │ │ note         │ │
│ Proposed angle           │          │ │ prose                   │ │ [Approve]    │ │
│ prose                    │          │ │ Why this story          │ │ [Reject]     │ │
│ Why this story           │          │ │ prose                   │ │ Back to …    │ │
│ prose                    │          │ │ [Points 412][Comments…] │ └─────────────┘ │
│ Source metrics           │          │ └─────────────────────────┘                 │
│ [Points 412] [Comments]  │          └──────────────────────────────────────────────┘
│ ┌ Decide this story ───┐ │
│ │ note                 │ │
│ │ [Approve story]      │ │
│ │ [Reject story]       │ │
│ │ Back to candidates   │ │
│ └──────────────────────┘ │
└──────────────────────────┘
```

**Principles.** The headline and source line carry the identity; the prose reads like a printed
proof, not a card grid. The single bold element is the decision rail: one blue approve button and
one pink reject button, full width, pressed state inherited from `.btn`. Metrics are small stamped
chips (quiet, non-interactive). Provenance (source link) is a text link, not a button.

**Component notes (add to `app.css` after the candidate inbox block).**

- `.candidate-detail`: `margin-top: var(--space-5);`
- `.candidate-detail__title`: `max-width: 30ch; margin-top: var(--space-3);` (rest from global `h1`).
- `.candidate-source`: `display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-2)
  var(--space-3); margin-bottom: var(--space-2); color: var(--color-muted); font-size:
  var(--text-sm);`
- `.candidate-source__link`: `margin-bottom: var(--space-4); font-size: var(--text-sm);`
- `.candidate-detail__layout`: `margin-top: var(--space-5);`
- `.candidate-detail__reading`: `min-width: 0;`
- `.candidate-section`: `margin-bottom: var(--space-5);` and
  `.candidate-section h2`: `margin: 0 0 var(--space-2); font-family: var(--font-display);
  font-size: var(--text-lg); font-weight: 800; line-height: 1.2; letter-spacing: -0.01em;`
- `.candidate-metrics`: `list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap;
  gap: var(--space-2);` and `.candidate-metrics > li { max-width: none; }` (counter the global
  prose `li` rule as 045 did).
- `.metric-chip`: `display: inline-flex; align-items: baseline; gap: var(--space-2); padding:
  var(--space-1) var(--space-3); color: var(--color-text); background: var(--color-surface);
  border: var(--border-thin) solid var(--color-border); border-radius: var(--radius-pill);
  font-size: var(--text-sm);` with `.metric-chip__label { color: var(--color-muted); font-weight:
  700; }` and `.metric-chip__value { font-weight: 800; }`. Non-interactive: no hover, no shadow.
- `.candidate-actions`: `margin-bottom: 0;` and `.candidate-actions h2 { font-size:
  var(--text-lg); margin-bottom: var(--space-2); }`;
  `.candidate-actions .decisions { flex-direction: column; gap: var(--space-3); }`,
  `.candidate-actions .decision .btn { width: 100%; }`, `.candidate-actions .back { margin-top:
  var(--space-4); }`.
- `@media (min-width: 48rem)`: `.candidate-detail__layout { display: grid; grid-template-columns:
  minmax(0, 1fr) 17rem; gap: var(--space-5); align-items: start; }`. No sticky rail.
- Focus target for the inbox notice: `.notice[tabindex="-1"]:focus { outline: var(--border-width)
  solid var(--color-focus); outline-offset: 4px; }` (keyboard focus after redirect stays visible).
- Add no transitions, so `prefers-reduced-motion` needs no change; the global `:focus-visible`
  outline is kept. No raw hex values in the new CSS.

**Design critique (checked before building).** Rejected the skill's default cream/terracotta and
acid-accent palettes (the app's green graph paper and ink blue stay), the SaaS card kit (only the
decision is boxed), broadsheet hairlines (3px ink), all-caps eyebrows, mono micro-labels, numbered
markers and arrow glyphs. The one memorable move is the two full-width stamped decisions beside a
flat proof column; everything else stays quiet.

**Copy (exact strings live in `view_models.py`, never in the template).**

- Title: `Untitled story` when blank. Source line: `<time>` + `Detected 2 hours ago`.
- Source link: `Read the original story`, plus visually hidden `(opens in a new tab)`; opens with
  `target="_blank" rel="noopener noreferrer"`. No URL: `No source link for this story.`
- Sections: `Summary` / `No summary available.`; `Proposed angle` / `No angle proposed.`;
  `Why this story` / `No reason recorded.`; `Source metrics` / `No source metrics for this story.`
- Metric labels: `score` → `Points`, `comments` → `Comments`, anything else → the key with `_`
  replaced by spaces and sentence-cased. Values `f"{value:,}"`.
- Decision card: `Decide this story`; note `Approving only records the story for later. Nothing is
  published.`; buttons `Approve story`, `Reject story`; link `Back to candidates`.
- Invalid decision (400, detail re-rendered): `Choose Approve or Reject.`
- Not available, missing (404): heading `This story is not available`, body `It may have been
  removed, or the link may be wrong. The candidate inbox is the best place to start.`
- Not available, approved (410): heading `This story was already approved`, body `It is no longer
  waiting for review. Approving only records a story; nothing was published.`
- Not available, rejected (410): heading `This story was already rejected`, body `It is no longer
  waiting for review. Rejecting only removes a story; nothing was published.`
- Not available, expired (410): heading `This story expired without a decision`, body `The review
  window closed before anyone decided. Nothing was published.`
- Unknown closed status (410): heading `This story is no longer available`, body `It is no longer
  waiting for review.`
- Back link on every not-available page: `Back to candidates` → `/candidates`.
- Inbox notices (allow-listed token → text; unknown token renders nothing): `approved` → `Approved:
  the story is recorded. Nothing was published.`; `rejected` → `Rejected: the story is out of the
  queue. Nothing was published.`; `closed` → `That story was already decided or has expired.
  Nothing changed.`

**View model contract (pure, no I/O).**

- `MetricChip`: `label`, `value`.
- `CandidateDetailView`: `page_title`, `title`, `source_label`, `observed_iso: str | None`,
  `observed_text`, `source_url: str | None`, `source_link_label`, `source_link_hint`,
  `source_missing_label`, `error`, `summary_label`, `summary`, `angle_label`, `angle`,
  `reason_label`, `selection_reason`, `metrics_label`, `metrics`, `metrics_note`,
  `decision_heading`, `decision_note`, `decision_action`, `approve_label`, `reject_label`,
  `back_href`, `back_label`.
- `CandidateNotAvailableView`: `page_title`, `heading`, `body`, `back_href`, `back_label`.
- `build_candidate_detail_view(state: CandidateReviewState, *, now: datetime, error: str = "") ->
  CandidateDetailView`.
- `build_candidate_not_available_view(status: str | None) -> CandidateNotAvailableView` (`None` is
  missing; otherwise the closed copy for that status).
- `metric_chips(metrics: dict[str, int]) -> list[MetricChip]`, `safe_source_url(url: object) ->
  str | None` (only `http`/`https` with a host; anything else, including a `model_construct`ed
  non-`HttpUrl`, gives `None`), `decision_notice(token: str) -> str` (allow-list above).
- `decision_action` is `/candidates/{quote(candidate_id, safe="")}/decide`.

**Route and service wiring.** 044 already supplies `CandidateService.show` (raising
`CandidateNotFound` / `CandidateClosed`) and `CandidateService.decide`; this task adds no service
or gateway method. The detail route catches `CandidateNotFound` → 404 and `CandidateClosed` →
410 with the status-specific not-available view. The decision route uses `current_user` for
`decided_by`, catches repeated/closed decisions → 303 to `/candidates?notice=closed#candidate-notice`,
unknown candidates → 404, and maps the returned `StoryCandidateResult.status` (`approved`,
`rejected`, `expired`) to the notice token. The 045 `CandidateUnavailable` handler keeps covering
service outages at 503. The inbox route gains an allow-listed `?notice=` param; the notice element
gets `id="candidate-notice" tabindex="-1" role="status"` so the redirect fragment moves focus
there without a script.

## Acceptance criteria

- [ ] `GET /candidates/{id}` renders the shared shell and one candidate: `<h1>` title first, then
      source badge, detection time and a safe source link (`http`/`https` only,
      `target="_blank" rel="noopener noreferrer"`, or the missing-link note), then Summary,
      Proposed angle, Why this story and a chip group of metric labels/values. The page has no
      htmx attributes and works with JavaScript disabled.
- [ ] The decision card has two separate POST forms (`decision=approve` / `decision=reject`) with
      visually distinct `Approve story` (accent) and `Reject story` (danger) buttons and the
      "nothing is published" note; both use `current_user` and 303 to
      `/candidates?notice=approved|rejected#candidate-notice`.
- [ ] Missing candidates render the 404 not-available page; already-approved/rejected/expired
      candidates render the 410 page with status-specific copy; repeated or raced decisions 303 to
      the inbox closed notice; an invalid `decision` value re-renders the detail at 400 with the
      error and sends no signal; an unknown `notice` token renders no notice.
- [ ] Every label, fallback, metric label/value, URL and notice is computed in
      `web/view_models.py`; templates contain only loops and ifs; untrusted title/summary/angle/
      reason/URL text is escaped.
- [ ] At 360px the page is one stacked column with no horizontal overflow; at `min-width: 48rem`
      the reading column and 17rem decision rail sit side by side. Only existing colour, type,
      spacing, border and shadow tokens are used (no raw hex), contrast and 3px focus outlines
      hold, and reduced motion needs no new rule.
- [ ] A decision reaches only `CandidateService.decide`; no route, view model, template or test
      path calls chart generation, Bluesky publishing, workflows or the gateway directly.
      `just lint` and `just test` pass.

## Files to touch

- `src/ai_trend_pipeline/web/routes/candidates.py` (add detail GET, decision POST, `notice` query
  param on the inbox route)
- `src/ai_trend_pipeline/web/view_models.py` (detail and not-available views, metric chips, safe
  source URL, notice allow-list, copy constants)
- `src/ai_trend_pipeline/web/templates/candidate_detail.html` (new)
- `src/ai_trend_pipeline/web/templates/candidate_not_available.html` (new)
- `src/ai_trend_pipeline/web/templates/candidates.html` (notice focus target: `id`, `tabindex="-1"`)
- `src/ai_trend_pipeline/web/static/app.css` (detail layout, sections, metric chips, decision rail,
  notice focus rule)
- `tests/test_web_candidate_detail.py` (new)
- `docs/architecture/tech.md` (route table, templates list, detail view models)

## Tests to write

- `tests/test_web_candidate_detail.py::test_detail_page_shows_story_and_source` — title first,
  source badge, `Detected …`, safe link with target/rel, summary, angle, reason, metric chips,
  decision form action and both buttons, `Back to candidates`, shell.
- `tests/test_web_candidate_detail.py::test_detail_long_title_and_missing_optional_fields` — long
  title, no URL, no metrics, blank prose fallbacks; page still renders without error.
- `tests/test_web_candidate_detail.py::test_detail_escapes_untrusted_html` — `<script>`/`<img
  onerror>` in title, summary, angle and selection reason come back escaped with no raw tags.
- `tests/test_web_candidate_detail.py::test_source_link_is_limited_to_http_and_https` — valid
  `https` URL (with `&` escaped in `href`); `javascript:`, `data:`, `ftp:` and protocol-relative
  URLs built with `model_construct` render no link and the missing-link note.
- `tests/test_web_candidate_detail.py::test_view_metric_chips` — `score`/`comments`/unknown key
  labels, thousands separators, input order preserved, empty dict gives the metrics note.
- `tests/test_web_candidate_detail.py::test_approve_redirects_to_inbox_notice` — POST
  `decision=approve` returns 303 to `…?notice=approved#candidate-notice`; fake handle recorded
  approve with `decided_by=dev@localhost`.
- `tests/test_web_candidate_detail.py::test_reject_redirects_to_inbox_notice` — same for reject and
  `notice=rejected`.
- `tests/test_web_candidate_detail.py::test_success_notice_and_focus_target` — following the
  redirect renders the approved notice with `id="candidate-notice" tabindex="-1" role="status"`;
  the response has no htmx attributes.
- `tests/test_web_candidate_detail.py::test_unknown_notice_token_is_ignored` — `?notice=<script>`
  renders no notice text and no reflected markup.
- `tests/test_web_candidate_detail.py::test_missing_candidate_is_404` — unknown id renders the
  not-available page, 404, with `Back to candidates` and the shared shell.
- `tests/test_web_candidate_detail.py::test_already_decided_candidates_are_410` — approved,
  rejected and expired handles each render 410 with their own heading/body and no decision form.
- `tests/test_web_candidate_detail.py::test_repeated_decision_redirects_with_closed_notice` —
  approve then reject against the same closed candidate gives 303 with `notice=closed`, and the
  fake records only the first decision.
- `tests/test_web_candidate_detail.py::test_invalid_decision_renders_error_without_signal` — POST
  `decision=maybe` returns 400 with `Choose Approve or Reject.`, the detail markup, and no signal.
- `tests/test_web_candidate_detail.py::test_decision_never_calls_the_chart_service` — override
  `get_chart_service` with a fake whose every attribute raises; approve still returns 303 and the
  candidate handle is the only thing called.
- `tests/test_web_candidate_detail.py::test_detail_and_not_available_need_no_javascript` — neither
  template emits `hx-` attributes; the decision is a plain `method="post"` form.
- `tests/test_web_candidate_detail.py::test_not_available_view_copy` — `None`, approved, rejected,
  expired and unknown statuses map to the specified headings/bodies and the shared back link.

## Screenshot and accessibility checks

- Screenshot at 360×800 and 1280×800, light and dark: detail with metrics and a long title, detail
  with no URL/metrics, missing 404, closed approved/rejected/expired, and the inbox approved
  notice. Attach the images to the PR or handoff.
- At 360px confirm `document.documentElement.scrollWidth <= window.innerWidth`, no clipped title,
  chip or button, and the decision card below the reading column.
- Keyboard-only: skip link → header nav → source link → section content → Approve → Reject →
  Back to candidates; the 3px `--color-focus` outline is visible at every stop, and after a
  decision the fragment focus lands on the notice.
- Disable JavaScript and repeat the detail, decision and not-available flows.
- At 200% zoom, recheck wrapping and overflow at 360px.
- Confirm muted meta/chip labels on `--color-surface` and page ground, accent on ground, and
  `.btn--danger-outline` text on danger meet WCAG AA; the new CSS adds no raw hex and
  `tests/test_design_tokens.py` stays green.

## Out of scope

- Task 047: header `Candidates` nav item/active state/pending-count badge, consolidated candidate
  CSS, shared Back link polish, README screenshots.
- Any change to `StoryCandidateWorkflow`, `story_candidate.py`, the gateway, search attributes,
  `CandidateService` methods, ingestion, worker or Postgres.
- Chart generation, media, analytics, Bluesky/publishing controls, auto-refresh, htmx polling,
  filters, sorting, comments/notes fields, bulk decisions or a JavaScript build step.
- New colours, fonts, external assets, charts, analytics or a sidebar.
- CSRF tokens (the app has none today), authentication changes, or new settings.
- Changing inbox ordering/pagination beyond the `?notice=` param.

## Assumptions

- 044 already provides `CandidateService.show` and `decide`; 046 wires them and needs no service or
  gateway change. `CandidateClosed.status` selects the closed-page copy.
- The plan allows a not-available page or an inbox notice: this spec uses 404/410 pages for reads
  and the inbox closed notice for repeated/raced decisions.
- Post-redirect focus uses a URL fragment (`#candidate-notice`), which browsers focus because the
  notice has `tabindex="-1"`; no script is allowed.
- A non-NOT_FOUND Temporal RPC failure on a single-candidate query/signal still surfaces as 500;
  only connect-time `CandidateUnavailable` reaches the existing 503 inbox page. Mapping every RPC
  error belongs with the 049 hardening work.
- No CSRF token is added; the app currently relies on Cloudflare Access and same-origin forms.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: wrote the spec; `frontend-design` skill loaded and applied inside the existing token
system. Next: human approval, then builder (tests first). Assumptions are recorded above: 044's
`show`/`decide` are reused unchanged, missing is 404 and closed is 410, and post-redirect focus
uses the `#candidate-notice` fragment with no script.

**builder**: added detail/decision routes, allow-listed inbox `?notice=`, detail and not-available
views/templates and CSS; tests first (19 in `tests/test_web_candidate_detail.py`). Decisions reach
only `CandidateService.decide`; nothing charts or publishes. `just lint`/`just test` pass with the
harness `ANTHROPIC_*` base-url vars unset. Screenshots (360/1280, light/dark) in
`/tmp/candidate-046-shots`: no overflow/clipping, focus and notice focus verified. Next: verifier.

**verifier**: `just lint` passes; `just test` passes (608) with harness `ANTHROPIC_*` base-url vars unset. With them set, 54 pre-existing LLM tests fail on `anthropic.APIConnectionError` only. Next: reviewer.

**reviewer**: Approved. Checked every criterion, the 360/1280 light and dark screenshots, escaping, http/https-only source link, 404/410 and the PRG closed notice; routes stay thin and decisions reach only `CandidateService.decide`. No service, gateway or workflow changes; no live calls; `just lint` and the candidate/web tests pass. Next: scribe.

**scribe**: README now documents the detail page, decision redirect and 404/410 pages; tech.md route and template tables were updated by the builder. Pushed `task/046-candidate-detail` and opened PR #52: https://github.com/akonale/ai-trend-pipeline/pull/52. Next: human review and merge (never merged by agents).
