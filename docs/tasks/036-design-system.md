# 036: Neo-brutalist design system

Status: pr-open

## Goal

Rewrite `web/static/app.css` into a neo-brutalist design system ("graph paper and ink": thick
ink borders, hard offset shadows, flat colour blocks, heavy headings, light and dark) so every
existing page takes the new look with no layout, route or copy changes. Add a kept test that
checks every declared colour pair against WCAG AA.

## Acceptance criteria

- [ ] `app.css` is rewritten to the tokens and component rules below; home, run (every status),
      settings, health and not-found pages render with the new look. No inline styles added.
- [ ] `tests/test_web*.py` pass unchanged (class names `badge--progress|attention|success|danger|muted`,
      `btn--primary`, `btn--danger-outline`, `chip`, `chart-frame`, `run-progress`, `site-header`,
      `more-options`, `options-list` kept; all tokens in `tests/test_web_ui.py::TOKENS` still declared;
      the string `http` never appears in `app.css`, so no SVG data URIs and no URLs in comments).
- [ ] Every `--color-*` token is a literal 6-digit hex in both the light `:root` block and the dark
      `@media (prefers-color-scheme: dark) { :root { ... } }` block, and every pair in the contrast
      table below passes in both themes (checked by `tests/test_design_tokens.py`).
- [ ] Stepper, shell and future-component classes exist now, unused: `.stepper`, `.step`,
      `.step__num`, `.step--done`, `.step--current`, `.step--upcoming`, `.btn--secondary`,
      `.user-chip`, `.site-footer`, `.dev-tag`, `.check`, `.card--chart`, `.visually-hidden`.
- [ ] Keyboard focus shows a 3px outline on every link, button, input, select, textarea, chip,
      summary and swatch; `prefers-reduced-motion` turns off transitions and the progress animation.
- [ ] Step labels never break inside a word at 360px ("Review" stays whole): `.step` sets
      `overflow-wrap: normal; word-break: keep-all; hyphens: none`, and below 30rem the step label
      size and padding shrink as specified under "Stepper". The builder re-checks this in a 360px
      screenshot of a page (or scratch HTML) that contains the stepper, since no app page uses it yet.
- [ ] Orchestrator screenshots at 360px and 1280px, light and dark, show no horizontal overflow on
      home, run (awaiting decision with chart), settings, health and not-found.
- [ ] `docs/architecture/tech.md` ("Templates, view models, design") describes the new tokens,
      the contrast test and the stepper classes reserved for 038.

## Design

The look was reviewed from screenshots of the planner's preview page (light and dark, 1280px and
360px, no horizontal overflow); the preview uses exactly these tokens and rules. Builder: copy the
values below verbatim. Decided: system font stacks only (no vendored font), and dark mode keeps the
hard shadows in `#e3ecdf` as in the preview.

### Concept and the one bold element

Topic Studio makes charts, so the page is **graph paper** (pale green ground with a faint 24px grid)
and every component is drawn in **ink** (3px blue-black borders, hard ink shadows). The single
memorable element is the **chart frame**: the chart sits on a white "plot sheet" with ruler ticks
along its left and bottom edges (an x/y axis) and a 10px marigold hard shadow. Nothing else uses the
marigold shadow or the ticks; everything else stays disciplined (ink shadows, 4px/6px offsets).

Review against generic defaults, and what changed: (1) the stock neo-brutalist kit (yellow ground,
hot pink, pure black) was dropped for graph-paper green, cobalt ink and marigold highlighter, which
come from plotting; (2) the current cream + terracotta and serif-italic captions are gone; (3) dark
is not near-black + acid accent: it is ink-navy with a periwinkle accent and marigold focus;
(4) badges lose the uppercase, letter-spaced labels (sentence case, weight 800); (5) mono is kept
only for the workflow id, which really is an identifier; (6) one radius per role, not one for
everything: badges 0, buttons/inputs 4px, cards 6px, chips and user chip pill.

### Colour tokens

| Token | Light | Dark | Role |
| --- | --- | --- | --- |
| `--color-bg` | `#e3ecdf` graph paper | `#191c30` night ink | page ground |
| `--color-grid` | `#d2dfcc` | `#23273f` | 1px grid lines on the ground (24px) |
| `--color-surface` | `#ffffff` sheet | `#262a44` | cards, header, footer, secondary button |
| `--color-field-bg` | `#ffffff` | `#191c30` | inputs, selects, textarea, swatch, progress track |
| `--color-text` | `#15172b` ink | `#e3ecdf` | body text |
| `--color-muted` | `#4a4f5e` pencil | `#b4bacb` | meta, help text, placeholder, upcoming step |
| `--color-border` | `#15172b` | `#e3ecdf` | every 2px/3px border |
| `--color-field-border` | `#15172b` | `#e3ecdf` | control borders |
| `--color-shadow` | `#15172b` | `#e3ecdf` | hard shadows |
| `--color-accent` | `#2448d8` cobalt | `#8daeff` periwinkle | links, primary button, done step |
| `--color-accent-contrast` | `#ffffff` | `#15172b` | text on accent |
| `--color-focus` | `#2448d8` | `#ffc93c` | focus outline |
| `--color-highlight` | `#ffc93c` marigold | same | current step, current nav, link hover, chip hover, checked swatch, chart shadow, dev tag |
| `--color-info` | `#a8c8ff` sky | same | badge--progress fill |
| `--color-success` | `#7ddba3` mint | same | badge--success, `.notice` fill |
| `--color-warning` | `#ffc93c` | same | badge--attention fill |
| `--color-danger` | `#ff9a9a` coral | same | badge--danger, `.error`, `.btn--danger-outline` fill |
| `--color-neutral` | `#d3d6de` fog | same | badge--muted, disabled button, table header, user chip |
| `--color-on-fill` | `#15172b` | same | text on any fill token, chart ticks |
| `--color-on-fill-muted` | `#4a4f5e` | same | disabled button text |
| `--color-chart-bg` | `#ffffff` | same | plot sheet behind the chart PNG |

Rule: any element whose background is a fill token (highlight, info, success, warning, danger,
neutral) sets `color: var(--color-on-fill)`; never inherit `--color-text` onto a fill (it is
light in dark mode and would fail).

### Contrast pairs (relative luminance, WCAG 2.x; computed and double-checked)

| Foreground / background | Min | Light | Dark |
| --- | --- | --- | --- |
| text / bg | 4.5 | 14.57 | 13.86 |
| text / grid | 4.5 | 12.75 | 12.09 |
| text / surface | 4.5 | 17.66 | 11.58 |
| text / field-bg | 4.5 | 17.66 | 13.86 |
| muted / bg | 4.5 | 6.74 | 8.66 |
| muted / grid | 4.5 | 5.90 | 7.55 |
| muted / surface | 4.5 | 8.17 | 7.23 |
| muted / field-bg | 4.5 | 8.17 | 8.66 |
| accent / bg | 4.5 | 5.79 | 7.70 |
| accent / grid | 4.5 | 5.07 | 6.72 |
| accent / surface | 4.5 | 7.02 | 6.43 |
| accent-contrast / accent | 4.5 | 7.02 | 8.10 |
| on-fill / highlight (= warning) | 4.5 | 11.50 | 11.50 |
| on-fill / info | 4.5 | 10.40 | 10.40 |
| on-fill / success | 4.5 | 10.54 | 10.54 |
| on-fill / danger | 4.5 | 8.69 | 8.69 |
| on-fill / neutral | 4.5 | 12.14 | 12.14 |
| on-fill-muted / neutral | 4.5 | 5.62 | 5.62 |
| on-fill / chart-bg | 4.5 | 17.66 | 17.66 |
| border / bg | 3 | 14.57 | 13.86 |
| border / grid | 3 | 12.75 | 12.09 |
| border / surface | 3 | 17.66 | 11.58 |
| field-border / field-bg | 3 | 17.66 | 13.86 |
| field-border / surface | 3 | 17.66 | 11.58 |
| focus / bg | 3 | 5.79 | 10.94 |
| focus / grid | 3 | 5.07 | 9.54 |
| focus / surface | 3 | 7.02 | 9.13 |

### Typography

- `--font-display: "Arial Black", "Segoe UI", -apple-system, system-ui, Roboto, sans-serif;`
  weight 900 (Arial Black on Mac/Windows, Segoe UI Black, SF Black, Roboto Black elsewhere).
- `--font-body: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;`
- `--font-mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;` (workflow id only).
- No web fonts, no `@font-face`, no vendored font file (decided: system stacks).
- Scale (major third): `--text-sm: 0.875rem`, `--text-base: 1rem`, `--text-lg: 1.25rem`,
  `--text-xl: 1.5625rem`, `--text-2xl: clamp(2rem, 1.4rem + 3vw, 3.25rem)`.
- Headings (`h1, h2`): display font, 900, line-height 1.05, letter-spacing -0.02em,
  `text-wrap: balance`; `h1` at `--text-2xl`, `max-width: 18ch`, margin `--space-6 0 --space-4`;
  `h2` at `--text-xl`, margin `--space-5 0 --space-3`. No accent colour or italic inside headings.
- Body line-height 1.55; `.meta` 1.45; `p, li, dd, figcaption` `max-width: 68ch` (under 80 chars).
- Labels: body font weight 800 (`.topic-form > label`, `.settings-form > label`, `legend` at
  `--text-lg`; `.more-options label` weight 700 at base). Sentence case everywhere, no uppercase.

### Spacing, borders, radius, shadows, motion tokens

- `--space-1..6` keep today's values (0.25, 0.5, 0.75, 1, 1.5, 2.5rem); add `--space-7: 4rem`
  (bottom padding of `main`).
- `--border-width: 3px` (cards, buttons, inputs, header, footer, frames, alerts, focus);
  `--border-thin: 2px` (chips, badges, user chip, nav, table cells, chart ticks and caption rule).
- `--radius-sm: 4px`, `--radius-md: 6px`, `--radius-pill: 999px`.
- `--shadow-sm: 4px 4px 0 0 var(--color-shadow)` (buttons, alerts, current step, checked swatch);
  `--shadow-md: 6px 6px 0 0 var(--color-shadow)` (cards); `--shadow-lift: 6px 6px 0 0 var(--color-shadow)`
  (button hover); `--shadow-chart: 10px 10px 0 0 var(--color-highlight)` (chart frame only);
  `--shadow-none: 0 0 0 0 var(--color-shadow)`. No blur anywhere, no rgba shadows.
- `--duration-press: 80ms`.
- Pressed rule: `:active` translates the element by exactly its shadow offset and sets
  `--shadow-none` (buttons 4px, chips 2px), so it lands where its shadow was. Hover (inside
  `@media (hover: hover)` only): buttons `translate(-2px, -2px)` + `--shadow-lift`.
- Motion: no page-load or entrance animation. The only non-user motion is the existing progress
  bar (now `steps(12, end)`, hard edges). Transitions only on button/chip transform and shadow.
  `prefers-reduced-motion: reduce`: `transition: none` on `.btn, .chip`; the bar stops and shows
  static 45deg stripes (`repeating-linear-gradient(45deg, accent 0 8px, transparent 8px 16px)`)
  at full width. Press and focus states still change instantly.

### Selector discipline (migration rules)

- Element defaults (`h1`, `h2`, `p`, `a`, inputs, `table`, `th`, `td`) are wrapped in `:where(...)`
  so any component class wins without fights. Components are single classes plus at most one
  state (`.btn:hover`, `.chip:active`) or one child level (`.site-nav a`, `.run-progress .bar`).
- Remove the old duplicate field rules (`.settings-form select`, `.topic-form select`): one
  `:where(input[type="text"], input[type="number"], input[type="email"], select, textarea)` rule.
- Label rules target direct children (`.topic-form > label`, `.settings-form > label`) so they never
  hit `.swatch` labels or `.more-options` labels. `.swatch` replaces `.settings-form .swatch`.
- Modifiers come after their base in source order (`.btn` then `.btn--*` then hover media then
  `:active` then `:disabled`; `.card` then `.card--chart`).
- Link hover (`:where(a:not([class])):hover`: marigold background, on-fill text) must not apply
  where text colour is forced: `.site-nav a` and `.step a` set `background: transparent`. Drop
  the muted colour on `.back a` / `.footer-link a` (links stay accent).
- Keep the dark block as `@media (prefers-color-scheme: dark) { :root { ... } }`; it redefines
  only the tokens whose dark value differs (fills, on-fill and chart-bg are shared).

### Component notes

- **Page:** `body` background `--color-bg` with the grid as two 1px `linear-gradient`s in
  `--color-grid`, `background-size: 24px 24px`. `main` max-width 44rem (unchanged), padding
  `0 --space-4 --space-7`.
- **Header / footer** (`.site-header`, `.site-footer`): surface band, `display: flex; flex-wrap: wrap;
  align-items: center; gap: --space-2 --space-4`, padding-block `--space-4`, the existing
  `max(...)` padding-inline formula; header has a 3px bottom border, footer a 3px top border,
  `--text-sm`, muted. `.tagline` `flex-basis: 100%`, muted, `--text-sm`, not italic.
- **Brand:** display font 900, `--text-lg`, ink colour, no underline; `::before` is a 1.4rem x 1.25rem
  three-bar mini chart (three `linear-gradient` layers in accent, 0.35rem wide, heights 55%,
  100%, 35%, at x = 0, 0.5rem, 1rem) sitting on a 3px ink baseline.
- **Nav:** `.site-nav` flex, wrap, gap `--space-2`. `.site-nav a`: weight 800, text colour, no
  underline, transparent background, 2px transparent border, radius-sm; hover shows the border;
  `[aria-current="page"]` highlight fill + border.
- **User chip / dev tag:** `.user-chip` neutral fill, on-fill text, 2px border, pill, `--text-sm`
  700, `margin-left: auto`. `.dev-tag` highlight fill, on-fill, 2px border, radius 0, weight 800.
- **Buttons:** `.btn` min-height 3rem, padding `--space-3 --space-5`, weight 800, 3px border,
  radius-sm, `--shadow-sm`, surface/text by default. `--primary` accent/accent-contrast;
  `--secondary` surface/text; `--danger-outline` danger fill/on-fill (name kept for tests).
  `:disabled, [aria-disabled="true"]`: neutral fill, on-fill-muted text, dashed border, no shadow,
  no transform, `cursor: not-allowed`.
- **Focus:** one global `:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 4px; }`
  (offset clears the 4px shadow); `.swatch:has(input:focus-visible)` gets the same outline.
- **Inputs, selects, textareas:** width 100%, min-height 3rem, padding `--space-3 --space-4`,
  field-bg, text colour, 3px field-border, radius-sm, no shadow. Textarea min-height 6rem,
  `resize: vertical`. Placeholder muted, opacity 1. Native select arrow (no custom SVG).
  Checkbox/radio: 1.25rem, `accent-color: var(--color-accent)`. `.check`: flex row, gap
  `--space-3`, weight 700 (for 039's "Save as my default").
- **More options:** `summary` `--text-lg` 800, `border-top: 2px dashed` border colour, padding-top
  `--space-3`; native marker kept.
- **Cards:** surface, 3px border, radius-md, `--shadow-md`, padding `--space-5`, margin-bottom
  `--space-5`; `.card > :first-child` margin-top 0, `> :last-child` margin-bottom 0.
  `.card--chart` removes padding, background, border and shadow (the chart frame is the card).
- **Chips:** pill, 2px border, surface, text colour, `--text-sm` 700, `2px 2px 0 0` shadow,
  gap `--space-3`; hover highlight fill + on-fill; active pressed 2px.
- **Badges** (status stamps): radius 0, 2px border, `--text-sm` 800, sentence case, on-fill text;
  `::before` a 0.625rem square in `currentcolor`. Fills: `--progress` info (drafting, posting,
  decision sent), `--attention` warning (needs review), `--success` success (posted), `--danger`
  danger (rejected), `--muted` neutral (expired). Class names and `badge_for` unchanged.
- **Alerts:** `.error` danger fill, `.notice` success fill; both on-fill text, 3px border,
  radius-sm, `--shadow-sm`, weight 600, padding `--space-3 --space-4`. No left stripe.
- **Progress:** `.run-progress .bar` 1.25rem tall, field-bg, 3px border, radius-sm; `::after` 35%
  accent block with a 3px right border, `run-slide 1.4s steps(12, end) infinite`.
- **Chart frame (bold element):** `.chart-frame` margin `0 10px 10px 0`, padding
  `--space-4 --space-4 calc(--space-4 + 0.75rem) calc(--space-4 + 0.75rem)`, chart-bg, on-fill
  text, 3px border, radius-sm, `--shadow-chart`, `position: relative`. `::before` (y ruler):
  absolute left 0, top 0, bottom 0, width 0.75rem, 2px right border on-fill, background
  `repeating-linear-gradient(to top, on-fill 0 2px, transparent 2px 12px) right / 0.4rem 100% no-repeat`.
  `::after` (x ruler): absolute left 0, right 0, bottom 0, height 0.75rem, 2px top border, same
  gradient `to right`, `top / 100% 0.4rem no-repeat`. `img` block, max-width 100%, height auto.
  `figcaption` margin-top `--space-4`, padding-top `--space-3`, 2px top rule on-fill, base size,
  line-height 1.55, on-fill, not italic.
- **Lists / tables:** `.options-list dt` muted `--text-sm` 700, `dd` margin `0 0 --space-2`;
  `.health dt` 800, `dd` margin-bottom `--space-3`. `:where(table)` full width, collapse, surface,
  3px border; `:where(th, td)` padding `--space-2 --space-3`, 2px border, left, top; `:where(th)`
  neutral fill, on-fill, 800.
- **Swatches:** `.swatch` field-bg, text colour, 3px field-border, radius-sm, `--text-sm` 700;
  checked: highlight fill, on-fill text, `--shadow-sm`. `.swatch-colors` 2px on-fill border.
- **Stepper (038 uses it):** `.stepper` `ol`, no list style, grid of 4 `minmax(0, 1fr)` columns,
  margin-top `--space-5`. `.step` flex column, padding `--space-2 --space-3`, 3px border, borders
  collapse via `margin-left: -3px` (first child 0), outer corners radius-sm, `--text-sm` 700,
  line-height 1.2. `.step__num` display font 900 `--text-xl`. `--done` accent/accent-contrast;
  `--current` highlight/on-fill, `--shadow-sm`, `position: relative; z-index: 1`; `--upcoming`
  surface/muted. `.step a { color: inherit; background: transparent; }`. State words for screen
  readers go in `.visually-hidden` (standard clip-path utility).
  No mid-word breaks: `.step { overflow-wrap: normal; word-break: keep-all; hyphens: none; }`
  (overrides the `overflow-wrap: anywhere` inherited from `body`). Narrow screens:
  `@media (max-width: 30rem) { .step { padding: var(--space-2) var(--space-1); font-size: 0.8125rem; } }`
  so a column is about 74px of content at 360px and "Review" fits whole. All labels stay visible;
  colours are unchanged, so contrast is unchanged. Each step stays at least 44px tall.
- **Kept as is (restyled only through tokens):** `.topic-row` (column, row from 40rem),
  `.year-row`, `.decisions` (gap now `--space-4`), `.wf-id` (mono, muted), `.meta`,
  `.footer-link`, `.back`, `.status-line`.

## Files to touch

- `src/ai_trend_pipeline/web/static/app.css` (rewrite)
- `src/ai_trend_pipeline/web/templates/partials/run_status.html` (only: the chart section's class
  becomes `card card--chart`)
- `tests/test_design_tokens.py` (new)
- `docs/architecture/tech.md` ("Templates, view models, design" paragraph)

## Tests to write

- `tests/test_design_tokens.py::test_contrast_math`: the helper gives 21.0 for `#000000`/`#ffffff`
  and about 4.48 for `#777777`/`#ffffff` (guards the formula).
- `test_color_tokens_are_hex`: every `--color-*` in the light `:root` and the dark media `:root`
  is a 6-digit hex literal (no `var()`), parsed from `app.css` with `re` only.
- `test_pairs_meet_wcag` (parametrised by theme): dark = light tokens updated by the dark block;
  every pair in the PAIRS constant (the contrast table above, as `(fg, bg, min)`) meets its min.
- `test_component_classes_present`: the new classes listed in acceptance criterion 4 and
  `.btn:active`, `:focus-visible`, `prefers-reduced-motion` appear in `app.css`.
- `tests/test_web_ui.py::test_awaiting_page_has_badge` still passes; add one assertion in the new
  file (not in old tests) that the run page with a chart renders `class="card card--chart"`.

## Out of scope

- No layout or page-structure changes, no new pages or routes, no copy changes, no header/footer
  markup (037), no stepper markup (038), no view model changes.
- No JavaScript, no new dependencies, no web fonts or `@font-face`, no CDN, no images.
- No backend, workflow, service or config changes. Do not edit existing tests.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->
**planner**: Spec written with full tokens, contrast table (hand-computed, both themes) and component rules; frontend-design skill was in context.
Preview (throwaway, not in repo): /tmp/claude-1000/-home-akonale-ai-trend-pipeline/5b5d6b35-c69e-5443-8442-dfc45e86630a/scratchpad/preview036/design-preview.html (add `#dark` or `data-theme="dark"` on html for dark).
Bold element: the chart frame (plot sheet with axis ruler ticks, marigold 10px shadow). Grid ground kept faint because the brief asks for a visible grid.
Coordinator review: kept system fonts and dark hard shadows; fixed the 360px stepper break ("Revie/w") with keep-all plus smaller labels and padding under 30rem.
Next: builder (re-check the stepper at 360px in a screenshot).
**builder**: Rewrote app.css from the approved mock (dark block in the media query, `:where` element defaults, `.card ul` rule now `:where(.card ul)` so `.chips` wins), `card card--chart` in run_status.html, new tests/test_design_tokens.py, tech.md updated.
Screenshots at 360/1280, light/dark, of home, await, draft, rejected (long id/email), settings, health, not-found, stepper scratch page: no horizontal overflow; "Review" stays whole at 360px; focus rings checked.
frontend-design skill was in context. Shots: /tmp/claude-1000/-home-akonale-ai-trend-pipeline/5b5d6b35-c69e-5443-8442-dfc45e86630a/scratchpad/build036/shots/. Next: verifier.
**verifier**: just lint (ruff check, ruff format --check, pyright strict) and just test (345 passed) both pass.
Existing web tests unchanged vs origin/main, no leftover serve.py processes, app.css has no external URLs or @import/url(). git status clean apart from the untracked .claude/settings.json.
ai-trend-prod containers all present (worker, web, cloudflared, temporal-ui, temporal, postgres); not touched.
Next: scribe.
**reviewer**: Approved. All criteria and listed tests present; contrast math is correct WCAG and would fail on a dropped token; lowest ratios (5.90, 5.62, 5.07, 6.43) recomputed independently and match.
No url()/@import/http/!important; only outline is the focus replacement; web tests untouched; only the class edit in run_status.html; tech.md accurate.
Note: min 44px step height is not set explicitly (met by padding + content); spec Status line was already modified in the worktree.
**scribe**: tech.md already current; README needs no change (no CSS/design section). Branch pushed; PR https://github.com/akonale/ai-trend-pipeline/pull/40 opened against main (title "036: Neo-brutalist design system").
Local main is stale (behind origin/main); PR diff checked against origin/main: 5 in-scope files only.
Not merged; human merges. After merge, prod needs `just deploy` to show the new look.
