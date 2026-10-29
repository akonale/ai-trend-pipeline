# 047: Candidate UI navigation and visual polish

Status: reviewed

## Goal

Make Candidates the primary destination in the shared header (first nav item, active on every
candidate page, with a pending-count badge only when the already-fetched inbox page supplies it),
and consolidate the 045/046 candidate styles into one reusable, token-only section of `app.css`.
Add a consistent Back to candidates link and page titles, without changing routes, decisions,
polling or the chart flow.

## Design decisions

The `frontend-design` skill was read and its plan → critique → build process applied. The existing
"graph paper and ink" tokens, the shared shell, and the 045/046 candidate pages win over the skill's
defaults. No new palette, typeface, asset, chart, analytics, sidebar, JavaScript or build step. The
task stays one PR: CSS consolidation, small view-model/shell additions, template class changes,
tests and docs.

**Subject, audience, job.** One reviewer working an editorial assignment desk. The product now opens
at a queue of story candidates; the chart flow is the secondary tool. The header must say that at a
glance: Candidates first, then New chart, then Your style. The visual language stays newsroom proof
sheet and ledger, not dashboard.

**Palette (existing tokens only, light / dark).**

- Page ground: `--color-bg` `#E3ECDF` / `#191C30`; grid `--color-grid` `#D2DFCC` / `#23273F`.
- Surfaces (rows, proof sheet cards, metric chips): `--color-surface` `#FFFFFF` / `#262A44`.
- Text and headings: `--color-text` `#15172B` / `#E3ECDF`; muted meta: `--color-muted` `#4A4F5E` /
  `#B4BACB`.
- Accent (links, Approve, nav count badge): `--color-accent` `#2448D8` / `#8DAEFF`; on accent:
  `--color-accent-contrast` `#FFFFFF` / `#15172B`.
- Active nav item: `--color-highlight` `#FFC93C` with `--color-on-fill` `#15172B`.
- Source badge: `--color-neutral` `#D3D6DE` with `--color-on-fill` `#15172B` (base `.badge`).
- Empty-state top edge: `--color-info` `#A8C8FF`; unavailable top edge: `--color-neutral`
  `#D3D6DE`. Reject keeps `.btn--danger-outline` (`--color-danger` `#FF9A9A`, `--color-on-fill`).
- Borders and hard shadows: `--color-border` / `--color-shadow` `#15172B` / `#E3ECDF`. Focus:
  `--color-focus` `#2448D8` / `#FFC93C`.

**Type (existing families and scale).** `--font-display` (Arial Black stack) for the `<h1>` and
candidate row titles/section headings; `--font-body` for prose and meta; `--font-mono` stays reserved
for workflow ids. Scale: `--text-sm` 0.875, `--text-base` 1, `--text-lg` 1.25, `--text-xl` 1.5625,
`--text-2xl` clamp(2rem–3.25rem). Nav 800; row title `--text-lg`/900; detail `<h1>` keeps the global
`--text-2xl` clamp. No new letter-spacing, all-caps labels, mono micro-labels or numbered markers.

**Spacing and borders.** `--space-1` 0.25 to `--space-7` 4rem; `--border-width` 3px,
`--border-thin` 2px, `--radius-sm` 4px, `--radius-md` 6px, `--radius-pill` 999px, `--shadow-sm`
4×4, `--shadow-md` 6×6, `--duration-press` 80ms. All new rules use these; no raw hex, no new shadow
or radius values.

**Layout.** Header: brand, then `<nav aria-label="Main">` with Candidates, New chart, Your style,
then the email chip pushed right; it wraps at 360px because `.site-nav` already wraps.

```
360px                                  1280px
┌───────────────────────────────┐      ┌──────────────────────────────────────────────────┐
│ ▮ Topic Studio                │      │ ▮ Topic Studio  Candidates [3]  New chart  Your   │
│ Candidates [3]  New chart     │      │                 style            (dev@localhost) │
│ Your style                    │      └──────────────────────────────────────────────────┘
│ (dev@localhost)               │
└───────────────────────────────┘
```

Inbox stays one ledger column inside the existing 44rem `main`; detail stays a proof column with
the 17rem decision rail at `min-width: 48rem`; not-available and empty/unavailable states stay
full-shell cards. Same structure at 768px and 1280px; only wrapping changes.

**Principles.** Candidates is primary by position and active state, not by a new colour or a bigger
type size. The pending count is the only new decorative-looking element and it carries real
information. Everything else stays quiet: rows are slips, the detail is a printed proof, the
decision rail is the one bold element. Reuse `.btn`, `.card`, `.badge`, `.notice`, `.error`, `.pager`
before adding a candidate class.

**Critique of 045/046 against each other (fix this drift).**

- Candidate navigation: neither inbox nor detail marked anything current in the header. Add the
  Candidates branch in `build_shell`; it is the main missing piece.
- Source badges: both pages repeat `badge badge--muted`, borrowing the workflow-status stamp for
  provenance. Introduce one `.source-badge` used by both: `.badge` base, pill radius, same neutral
  fill and ink text. Status badges stay square stamps; provenance becomes a pill.
- Hover/active/reduced motion: candidate-row hover lives inside the generic button/chip hover block,
  and the reduced-motion block lists both `.candidate-row` and `.candidate-inbox .candidate-row`.
  The duplicate exists only to beat the later `.candidate-row` transition by specificity. Consolidate
  into one candidate section at the end of the file with its own `prefers-reduced-motion` block after
  the base rules; delete the duplicate selector and leave `.btn`/`.chip` entries alone.
- Decision layout: 046 overrides generic `.decisions` and `.decision .btn` from the run page. Give
  the detail its own `.candidate-decisions` so run-page decisions are untouched.
- Flash messages: the inbox notice gets an id-specific focus rule while the detail error has none.
  One `.candidate-flash` class on both; move the focus rule there.
- Empty/unavailable: both are plain `.card`s and look identical. Add `.candidate-state` with the
  run-page outcome convention (0.75rem top edge): info blue for empty, neutral for unavailable.
- Back link: 046 puts it inside the decision rail on detail and below the body on not-available.
  One `.candidate-back` link at the top of both candidate subpages; same text, one destination.
- Intentional hierarchy, not drift: 045 row titles are `--text-lg` scans and the detail `<h1>` is
  the `--text-2xl` clamp; 045 angles stay one-line ellipsis while detail prose wraps. Keep both.

**Intentional visible-text changes.** (1) The header gains a `Candidates` item in first position;
existing labels do not change. (2) The Candidates item shows a count badge `N` or `N+` on a
successful first inbox page. (3) The source badge becomes a pill (shape only, same text). (4) The
"Back to candidates" text is unchanged but moves above the heading on detail and not-available pages.
(5) Empty/unavailable cards gain a coloured top edge (visual only). Inbox `<title>` text does not
change; it is now sourced from `page_title` in the view model.

**Pending badge rule (the important constraint).** A true total would need a new visibility count
call, which the plan forbids here. The badge therefore comes only from the `CandidatePage` the inbox
route already fetched: exact `N` when `next_token is None`; `N+` when there is another page; no badge
at all on empty, unavailable, invalid-token, non-first (`?page=`) or non-inbox pages, because those
either have no count or would present a fragment as a total. `CandidateService.list_pending` stays
unchanged; no per-row workflow query is added. The no-I/O shell context processor never queries.

**Page titles and back link.** Add `page_title` to `CandidateInboxView` (copy lives in
`view_models.py` like detail/not-available) and use `{{ view.page_title }} - {{ shell.brand }}` in
`candidates.html`. Detail and not-available already do this. Every candidate subpage gets one
`.candidate-back` link (`Back to candidates` → `/candidates`) before its `<h1>`; remove the one in
the decision rail and the one below the not-available body so there is exactly one per page.

**Component notes (move all candidate rules into one labelled section at the end of `app.css`;
add no raw hex).**

- `.nav-badge`: `display: inline-flex; align-items: center; justify-content: center; min-width:
  1.5rem; height: 1.5rem; margin-left: var(--space-2); padding: 0 var(--space-1); background:
  var(--color-accent); color: var(--color-accent-contrast); border: var(--border-thin) solid
  var(--color-border); border-radius: var(--radius-pill); font-size: var(--text-sm); font-weight:
  800; line-height: 1;`. Rendered after the label, `aria-hidden="true"`, with a `.visually-hidden`
  `, N stories waiting for review` (or `, N or more stories waiting for review`).
- `.source-badge`: `border-radius: var(--radius-pill);` only; `.badge` supplies neutral fill, ink
  text, 2px border and the square marker. Markup becomes `<span class="badge source-badge">` in both
  candidate templates.
- Keep `.candidate-inbox`, `.candidate-row`, `__title`, `__angle`, `__meta`, `.pager` as they are,
  but move the hover rules (`translate(-2px,-2px)`, `--shadow-lift`, underline the title) and the
  active rule into this section inside one `@media (hover: hover)` block.
- Candidate section ends with `@media (prefers-reduced-motion: reduce) { .candidate-row {
  transition: none; } }` after the base rule; remove `.candidate-inbox .candidate-row` from the
  earlier generic block.
- `.candidate-state`: `border-top-width: 0.75rem;`; `.candidate-state--empty { border-top-color:
  var(--color-info); }`; `.candidate-state--unavailable { border-top-color: var(--color-neutral); }`.
  Templates add both classes alongside `.card`.
- `.candidate-flash`: `margin: 0 0 var(--space-5);` and `.candidate-flash:focus { outline:
  var(--border-width) solid var(--color-focus); outline-offset: 4px; }`; templates add it alongside
  `.notice`/`.error`. Delete `.notice[tabindex="-1"]:focus`.
- `.candidate-back`: `margin: 0 0 var(--space-3); font-size: var(--text-sm); font-weight: 700;` and
  `.candidate-back a { color: var(--color-muted); }` (underline stays from the global link rule).
- `.candidate-decisions`: same wrap/gap as `.decisions`; `.candidate-actions .candidate-decisions {
  flex-direction: column; gap: var(--space-3); }` and `.candidate-actions .candidate-decisions .btn
  { width: 100%; }`. Delete the `.candidate-actions .decisions`, `.candidate-actions .decision .btn`
  and `.candidate-actions .back` overrides. Leave the generic `.decisions`/`.decision` run-page rules
  untouched.
- Keep `.candidate-detail`, `__layout`, `__reading`, `.candidate-section`, `.candidate-metrics`,
  `.metric-chip`, `.candidate-source`, `.candidate-source__link` as designed in 046; no new dividers
  or section borders. `.candidate-section h2` stays `--text-lg`, 800.
- Do not add transitions to badges, chips or back links, so the only reduced-motion entry remains the
  row. The global `:focus-visible` outline stays.

**Shell/view-model contract (pure, no I/O).**

- `class NavBadge(BaseModel): text: str; label: str`; `NavItem` gains `badge: NavBadge | None = None`.
- `build_shell(path, user_email, auth_mode, *, pending: NavBadge | None = None) -> ShellView`; nav
  order is `Candidates` (`/candidates`, current for `/candidates` and `/candidates/...`, badge
  `pending`), `New chart` (`/`, current for `/`, `/runs`, `/runs/...`, `/new/...`), `Your style`
  (`/settings`, current for `/settings`). Brand still links to `/`.
- `pending_nav_badge(page: CandidatePage, *, first_page: bool) -> NavBadge | None`: `None` for no
  rows or `not first_page`; otherwise `text=str(n)` / `f"{n}+"` when `page.next_token` is set, and
  `label=f"{n} {'story' if n == 1 else 'stories'} waiting for review"` or
  `f"{n} or more stories waiting for review"`. Empty rows still return `None`.
- `CandidateInboxView` gains `page_title: str = INBOX_HEADING`; both builders set it.

**Wiring.** `dependencies.py` gains `build_request_shell(request, *, pending: NavBadge | None =
None) -> ShellView` (settings + optional user + `build_shell`); `shell_context` delegates to it. The
successful inbox branch of `candidates.py` passes `{"view": view, "shell":
build_request_shell(request, pending=pending_nav_badge(page, first_page=token is None))}` (Starlette
merges context-processor values first, so the route value wins). Detail, not-available, invalid-token
and the 503 handler keep the processor shell and therefore no badge. No route/service/gateway
signature changes.

## Acceptance criteria

- [ ] The shared header shows `Candidates`, `New chart`, `Your style` in that order with the email
      chip; `/candidates` and `/candidates/{id}` (including the not-available pages) mark exactly
      `Candidates` with `aria-current="page"`, `/settings` marks `Your style`, the chart pages mark
      `New chart`, and `/health` marks nothing. Existing chart route tests pass unchanged.
- [ ] A pending badge appears on the Candidates nav item only on a successful first inbox page: `N`
      for a complete page, `N+` when `next_token` is set, no badge for empty, unavailable, invalid
      token, older (`?page=`) or non-inbox pages. The badge has an `aria-hidden` visible number and
      a visually hidden label; it adds no Temporal, visibility or per-row workflow query.
- [ ] Candidate CSS lives in one labelled section and reuses tokens only: rows/slips, `.source-badge`
      (pill), metric chips, detail sections, `.candidate-decisions`, `.candidate-state--empty`,
      `.candidate-state--unavailable`, `.candidate-flash` and `.candidate-back`. The duplicate
      `.candidate-inbox .candidate-row` reduced-motion selector and the replaced descendant
      overrides are gone, the hover rules move with the section, and there are no raw hex values
      below the token blocks.
- [ ] Every candidate subpage (detail, invalid-decision re-render, not-available 404/410) has exactly
      one `Back to candidates` link to `/candidates` above its heading, and a non-empty,
      page-specific `<title>` produced by the view model (`Story candidates - Topic Studio`,
      `<story title> - Topic Studio`, `<heading> - Topic Studio`).
- [ ] Candidate pages stay plain server-rendered GET + POST/303: no `hx-` attributes, no new script,
      no auto-refresh, no framework or build step; decisions still reach only `CandidateService.decide`
      and pagination/copy/decision behaviour is unchanged.
- [ ] At 360/768/1280px there is no horizontal overflow; nav and badge wrap without clipping; every
      focus stop has the 3px `--color-focus` outline; reduced motion disables the row press; light
      and dark text/border pairs still meet WCAG AA and `tests/test_design_tokens.py` is green.

## Files to touch

- `src/ai_trend_pipeline/web/view_models.py` (`NavBadge`, `NavItem.badge`, `build_shell` order and
  Candidates branch, `pending_nav_badge`, `CandidateInboxView.page_title`)
- `src/ai_trend_pipeline/web/dependencies.py` (`build_request_shell`; `shell_context` delegates)
- `src/ai_trend_pipeline/web/routes/candidates.py` (pass the shell override with the pending badge on
  the successful inbox response)
- `src/ai_trend_pipeline/web/templates/base.html` (nav badge markup)
- `src/ai_trend_pipeline/web/templates/candidates.html` (`page_title`, `candidate-state`, flash and
  source-badge classes)
- `src/ai_trend_pipeline/web/templates/candidate_detail.html` (top back link, source badge, flash and
  `candidate-decisions` classes)
- `src/ai_trend_pipeline/web/templates/candidate_not_available.html` (top back link)
- `src/ai_trend_pipeline/web/static/app.css` (consolidated candidate section, nav badge, states,
  flash, back link; remove duplicated selectors)
- `tests/test_web_shell.py` (nav order/current expectations; no new badge on non-inbox pages)
- `tests/test_web_candidates.py` (source-badge class, page title, active nav and badge cases)
- `tests/test_web_candidate_detail.py` (source-badge class, one top back link, active nav, titles)
- `tests/test_web_candidate_polish.py` (new: badge helper, cross-page nav, CSS consolidation)
- `docs/architecture/tech.md` (routes, dependencies, templates/design sections)
- `README.md` (Web app and Story candidates route descriptions)

## Tests to write

- `tests/test_web_candidate_polish.py::test_build_shell_nav_order_and_current_flags` — labels/hrefs
  are `Candidates`, `New chart`, `Your style`; current flags for `/`, `/new/style`, `/runs/x`,
  `/settings`, `/health`, `/candidates` and `/candidates/candidate-v1-abc`.
- `tests/test_web_candidate_polish.py::test_pending_nav_badge` — zero/None, one, many, complete vs
  `next_token`, and `first_page=False` returns `None`; label singular/plural and `or more`.
- `tests/test_web_candidate_polish.py::test_badge_only_on_first_inbox_page` — a fake gateway with
  three candidates renders the nav badge with `aria-hidden="true"` and the hidden count label on
  `/candidates`; a next-token page shows `3+`; a `?page=` page, empty page, unavailable 503, home,
  settings and health render no badge.
- `tests/test_web_candidate_polish.py::test_candidate_pages_mark_candidates_current` — inbox, detail,
  404 and 410 pages each have exactly one `aria-current="page"` on `Candidates`; inbox title is
  `Story candidates - Topic Studio`.
- `tests/test_web_candidate_polish.py::test_candidate_page_titles_and_back_link` — detail and
  not-available titles use the view-model copy; each page has exactly one `class="candidate-back"`
  link to `/candidates` before the `<h1>`.
- `tests/test_web_candidate_polish.py::test_candidate_css_is_consolidated_and_token_only` — new
  classes are present; `.candidate-inbox .candidate-row` is gone from the reduced-motion list;
  `.candidate-actions .decisions`, `.candidate-actions .decision .btn`, `.candidate-actions .back`
  and `.notice[tabindex` are gone; no hex colours below the token blocks; `.decisions` still exists
  for the run page.
- `tests/test_web_candidates.py::test_inbox_rows` (update) — source markup is `class="badge
  source-badge"`; active Candidates nav and badge still render around the rows.
- `tests/test_web_candidate_detail.py::test_detail_page_shows_story_and_source` (update) — source
  markup is `class="badge source-badge"`; the single back link sits before the heading and the
  decision rail no longer contains it.
- `tests/test_web_shell.py::test_build_shell_fields` and `::test_aria_current_on_the_right_link`
  (update) — assert the new nav order and the Candidates active state; chart-page expectations stay
  the same.

## Screenshot and accessibility checks

- Screenshot at 360×800, 768×800 and 1280×800, light and dark, with the nav visible: `/`,
  `/new/style?topic=…`, `/runs/{id}` (generating, review, approved, rejected, expired), `/settings`,
  `/health`, `/candidates` (rows, long title/angle, `3` badge, `3+` badge), `/candidates` empty,
  invalid token, unavailable, `/candidates/{id}` (normal and invalid decision), and the 404/410
  not-available pages. Attach to the PR or handoff.
- On every candidate page confirm exactly one active nav item and that the badge (when present) sits
  inside the Candidates link without overlapping the label at 360px.
- Confirm `document.documentElement.scrollWidth <= window.innerWidth` at 360px for the header with
  three items plus chip, the badge, row titles/angles, decision buttons and back links.
- Keyboard-only: skip link → brand → Candidates → New chart → Your style → main; on the inbox each
  row is one Enter target; on detail Back to candidates comes before the source link and decisions;
  the 3px `--color-focus` outline is visible at every stop and the hidden badge label is announced.
- Disable JavaScript and repeat the inbox, detail, not-available and decision flows; there are no
  `hx-` attributes on candidate pages. At 200% zoom recheck 360px wrapping.
- With reduced motion enabled confirm the row no longer translates/shadows on press; the only
  remaining animation on candidate pages is none.
- Confirm new pairs: `--color-accent-contrast` on `--color-accent` (badge), `--color-on-fill` on
  `--color-neutral` (source badge), `--color-muted` on `--color-bg`/`--color-surface` (back link and
  meta). `tests/test_design_tokens.py` stays green and the candidate section adds no hex.
- Check that no unused duplicate candidate rule remains (hover/reduced-motion/decision/back/flash)
  and that the generic `.btn`, `.chip`, `.decisions` and run-page rules still behave as before.

## Out of scope

- Any service, gateway, workflow, worker, search-attribute, config, Postgres or `CandidateService`
  change; in particular no `count`/total-pending method and no extra Temporal or visibility call.
- Changing routes, redirect targets, pagination semantics, decision semantics, copy, or the
  `?notice=` allow-list; no new feedback message text.
- Chart route/template redesign, including `run.html`, `home.html`, `new_style.html`,
  `settings.html` and their tests; `base.html` changes are limited to the nav badge markup.
- New colours, fonts, icons, external assets, CDN, charts, analytics, sidebar, JavaScript framework,
  htmx auto-refresh or build step.
- Authentication, CSRF, session state, per-source filters/sorting, bulk decisions, or media preview.
- Committing screenshots or adding visual-regression tooling.
- Updating `docs/history.md` (the scribe handles the merged-task history entry).

## Assumptions

- "Primary destination" means first nav position plus the active state on all candidate routes; the
  brand keeps linking to `/` and the chart flow stays reachable as `New chart`.
- A real total pending count is unavailable without a new visibility call, which the task forbids.
  The badge is therefore the already-fetched page's count (`N+` when `next_token` is set) and is
  suppressed on older and non-inbox pages rather than presenting a page fragment as a total.
- The nav count badge uses accent/on-accent rather than highlight so it stays legible when the
  Candidates item itself has the marigold active background.
- Starlette applies context processors first and then merges the route context, so passing
  `{"shell": build_request_shell(...)}` from the inbox route overrides the processor's shell for
  that response; a route test proves it.
- "Back to candidates" text is unchanged; only its placement and class change, so 046's visible copy
  and redirect focus behaviour stay valid.
- Existing candidate tests may be edited for the new source-badge class and back-link placement;
  chart route tests must pass untouched. The source badge changes shape only (square → pill).
- The candidate CSS section moves below the earlier generic blocks, so its reduced-motion rule must
  sit after the row's transition declaration; this is why the duplicate selector is removed rather
  than simply reordered.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: wrote the spec with the `frontend-design` skill applied inside the existing token
system. Next: human approval, then builder (tests first). Assumptions: badge comes only from the
already-fetched first inbox page (`N`/`N+`, none elsewhere), Candidates is first in nav while the
brand still opens `/`, and the "Back to candidates" text is unchanged but moves above the heading.

**builder**: added Candidates-first nav with a first-page `N`/`N+` badge (Starlette 1.10 runs context processors after the route context, so the badge is also stored on `request.state` for `build_request_shell`), one token-only candidate CSS section, and one Back link above each candidate heading with view-model titles.
**builder**: new `tests/test_web_candidate_polish.py` plus three updated route test files; `just lint` and `just test` green (614 passed).
**builder**: screenshots in `/tmp/candidate-047-shots` (Chromium 1248 headless shell; the full `chrome-linux64/chrome` binary is missing `libcups.so.2`). Unsure: the shared header's 44rem padding wraps the email chip to a second line at 768/1280, unlike the 1280 wireframe; left unchanged as out of scope.

**verifier**: `just lint` green (ruff, format, pyright strict: 0 errors) and `just test` green, 614 passed on `task/047-candidate-ui-polish`. First run showed only pre-existing LLM tests failing with `anthropic.APIConnectionError`; rerun with harness `ANTHROPIC_*` vars unset, all green. Next: reviewer.

**reviewer**: needs-fix.
1. `app.css:161` — 44rem header measure + three nav items needs ~789px, so `.user-chip` wraps to a lone second row at 768/1280 on every page; 046 had one row at 1280 and the spec's 1280 wireframe shows one row. Widen the shared `.site-header`/`.site-footer` inline measure at `min-width: 60rem` (e.g. `padding-inline: max(var(--space-4), calc((100% - 50rem) / 2 + var(--space-4)))`), tighten the nav at 768 if it should also fit there, and re-shoot 768/1280 light+dark.
2. `app.css:635` — `.candidate-back a { color: var(--color-muted); }` outranks the global `a:hover` ink, so dark hover is #B4BACB on #FFC93C (1.26:1, AA fail; light is 5.3:1). Use `:where(.candidate-back a)` so the hover ink wins, or add `.candidate-back a:hover { color: var(--color-on-fill); }`, and recheck both themes.

**builder** (fix loop 1): widened the shared header/footer measure to 50rem from 48rem and tightened the tablet nav so brand, nav and chip share one row at 768 and 1280; changed the candidate back link colour to zero-specificity `:where(.candidate-back a)` so the global hover ink wins (11.50:1 in both themes).
**builder** (fix loop 1): re-shot every state at 360/768/1280 light+dark in `/tmp/candidate-047-shots`; the harness asserts one header row at 768/1280 and the hover contrast. `just lint` and `just test` green, 614 passed.

**verifier** (re-verify after fix loop 1): `just lint` green (ruff, format, pyright strict: 0 errors) and `just test` green, 614 passed on `task/047-candidate-ui-polish`. First run showed only pre-existing LLM tests failing with `anthropic.APIConnectionError`; rerun with harness `ANTHROPIC_*` vars unset, all green. Next: reviewer.

**reviewer** (re-review after fix loop 1): Approved. Header is one row at 768/1280 light+dark on inbox, detail, not-available, home, settings and run states; back-link hover is ink on marigold at 11.5:1 (rest state muted is 6.7:1 light / 8.7:1 dark). The fixes are CSS-only, add no duplicate selectors, and regress no other state.
