# 035: UI refresh

Status: verified

## Goal

Give the home and run pages a distinctive, cohesive look. The style is an "editorial chart desk":
warm paper background, ink-coloured text, one burnt-orange accent, serif display headings over a
system sans body, with light and dark themes. Use only plain CSS in `web/static/app.css` and the
htmx already in the app. There is no build step, no new JS library, and no external font or CDN.

## Design (concrete; the builder follows this, exact values may be tuned for contrast)

**Tokens.** `:root` in `app.css` defines these custom properties. They are the only place colours,
font sizes, spaces, radii and shadows are written. Component rules use `var(--…)`.

| Group | Names (required) | Light starting value | Dark starting value |
| --- | --- | --- | --- |
| Colour | `--color-bg` | `#f7f4ee` (paper) | `#15140f` |
| | `--color-surface` | `#ffffff` | `#1f1d18` |
| | `--color-text` | `#1d1b18` | `#f3efe6` |
| | `--color-muted` | `#5c574f` | `#b3ab9c` |
| | `--color-border` | `#e2dccf` | `#3a362e` |
| | `--color-accent` / `--color-accent-contrast` | `#c2410c` / `#ffffff` | `#fb923c` / `#1a1206` |
| | `--color-success` / `--color-danger` / `--color-warning` / `--color-info` | `#166534` / `#b91c1c` / `#92400e` / `#1d4ed8` | `#4ade80` / `#f87171` / `#fbbf24` / `#93c5fd` |
| | `--color-focus` | same as accent | same as accent |
| Type | `--font-display`, `--font-body`, `--font-mono` | see below | |
| | `--text-sm`, `--text-base`, `--text-lg`, `--text-xl`, `--text-2xl` | ratio about 1.25 from 1rem; `--text-2xl` uses `clamp()` | |
| Space | `--space-1` … `--space-6` | 0.25, 0.5, 0.75, 1, 1.5, 2.5 rem | |
| Radius | `--radius-sm`, `--radius-md`, `--radius-pill` | 6px, 12px, 999px | |
| Shadow | `--shadow-sm`, `--shadow-md` | soft warm shadows | darker and subtler in dark |

The dark values override the colour and shadow tokens in `@media (prefers-color-scheme: dark)`.
`base.html` sets `<meta name="color-scheme" content="light dark">`.

**Typefaces.** Use system fonts only. Do not commit font files and do not use `@import` or `url()`
to fetch fonts.
- `--font-display: ui-serif, "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif`
  (h1, h2, brand).
- `--font-body: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif`.
- `--font-mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace` (workflow id).

**Layout.**
- Single column, `max-width: 44rem`, centred, with side padding `--space-4`.
- At 360px nothing scrolls sideways. Long ids and emails use `overflow-wrap: anywhere`, images use
  `max-width: 100%`, and the topic form stacks (input full width, button below).
- At 40rem and up the input and button sit on one row.

**Components (class names are fixed so tests and later tasks can rely on them):**
- `header.site-header`: the brand link "Topic Studio" to `/` (display font plus a small accent mark
  made with CSS) and a one-line muted tagline. It has no `<button>` elements.
- Topic form, `form.topic-form`:
  - A visible `<label for="topic">`.
  - `input#topic` and a primary button `button.btn.btn--primary` labelled "Generate".
  - Below them, `ul.chips`, where each `a.chip` links to `/?topic=<urlencoded>`. Clicking a chip
    reloads home with the box prefilled. This needs no JS.
- `.card`: surface background, border, `--radius-md`, `--shadow-sm`, padding `--space-5`.
- Run page cards, in order:
  - Status card: the `span.badge` with the status label, the workflow id in mono, and the
    requester and decider lines.
  - Preview card: `figure.chart-frame` holding the `img.preview` and a `figcaption` with the
    caption. The frame has a neutral inner background, so the white PNG reads well in dark mode.
  - Critique card.
  - Decision card: Approve is `.btn--primary`, Reject is `.btn--danger-outline`.
  - A muted footer link "View in Temporal".
- `span.badge`: pill shape, small caps, with a dot drawn by CSS `::before`. The classes come from the
  view model (below). Tones: `badge--progress` (info), `badge--attention` (accent),
  `badge--success`, `badge--danger`, `badge--muted`.
- `div.run-progress`: rendered only when `view.polling`. It has `role="status"`,
  `aria-live="polite"`, an indeterminate animated bar made in CSS, and the text "Working on it,
  checking every 2 seconds". It sits inside `#run-status`, so it survives the htmx `outerHTML` swap
  and disappears when polling stops. Under `prefers-reduced-motion: reduce` it does not animate.
- `.error`: alert box using the danger tone, with `role="alert"`.
- Focus: `:focus-visible` on `a`, `button`, `input` and `.chip` draws a 3px `--color-focus`
  outline with a 2px offset. Never use `outline: none` without a replacement.
- Contrast: body text is at least 4.5:1 and badges, borders on inputs and buttons are at least
  3:1, in both themes.

## Acceptance criteria

- [ ] `app.css` defines every token named above in `:root` and overrides the colour and shadow
      tokens in a `prefers-color-scheme: dark` block. It has no `@import`, no `url(http…)` and no
      external font. `base.html` has `<meta name="viewport" content="width=device-width,
      initial-scale=1">` and the `color-scheme` meta, and references only `/static/` assets.
- [ ] `HomeView` supplies `examples: list[TopicChip]` (`label`, `href`) with a default, so
      `runs.py`'s error renders show chips too. `GET /?topic=…` prefills the box (stripped and cut
      to `MAX_TOPIC_LENGTH`) through `build_home_view(topic)` in `view_models.py`. The home route
      only parses the query param and calls it.
- [ ] `RunView` supplies `badge_label` and `badge_class` from one mapping in `view_models.py`.
      Templates only print them:

      | State | `badge_label` | `badge_class` |
      | --- | --- | --- |
      | drafting | Drafting | `badge--progress` |
      | awaiting_decision, not sent | Needs review | `badge--attention` |
      | awaiting_decision, sent | Decision sent | `badge--progress` |
      | posting | Posting | `badge--progress` |
      | approved | Posted | `badge--success` |
      | rejected | Rejected | `badge--danger` |
      | expired | Expired | `badge--muted` |

- [ ] Pages render the components above with the fixed class names. Templates contain only
      `{% if %}`, `{% for %}`, `{% extends/block/include %}` and `{{ }}` output: no filters
      that compute, and no `{% set %}`.
- [ ] Every test in the existing `tests/test_web.py` passes with that file unchanged. Watch for
      these:
      - No `<button` anywhere on run pages that are not decidable.
      - The text "Requested by: x" and "Approved by: x" / "Rejected by: x" stays contiguous.
      - `hx-trigger="every 2s"` appears only when polling.
      - The status fragment has no `<html`.
- [ ] Manual check, by the orchestrator or human: home and run pages (drafting, awaiting, approved)
      at 360px and 1280px in browser devtools, in light and dark. There is no horizontal scroll,
      focus rings show when tabbing, and the progress bar shows while polling. `just lint` and
      `just test` pass.

## Files to touch

- `src/ai_trend_pipeline/web/static/app.css`: rewrite with tokens, themes and components.
- `src/ai_trend_pipeline/web/templates/base.html`: viewport and color-scheme meta, site header.
- `src/ai_trend_pipeline/web/templates/home.html`: label, topic form, chips.
- `src/ai_trend_pipeline/web/templates/run.html`: cards layout and mono workflow id.
- `src/ai_trend_pipeline/web/templates/partials/run_status.html`: badge, progress, cards, buttons.
- `src/ai_trend_pipeline/web/templates/run_not_found.html`: wrap in a card. No other change.
- `src/ai_trend_pipeline/web/view_models.py`: add `TopicChip`, `HomeView.examples`, `MAX_TOPIC_LENGTH`,
  `build_home_view`, `RunView.badge_label` and `badge_class`, and the badge mapping.
- `src/ai_trend_pipeline/web/routes/home.py`: optional `topic` query param to `build_home_view`.
- `src/ai_trend_pipeline/web/routes/runs.py`: import only. Use `MAX_TOPIC_LENGTH` from
  `view_models.py` instead of the local constant. No behaviour change.
- `tests/test_web_ui.py` (new).

## Tests to write (all in `tests/test_web_ui.py`; FastAPI test client and fake gateway, no Temporal)

- `build_run_view` gives the exact `badge_label` and `badge_class` for each of the 7 rows in the
  table.
- The run page renders `class="badge badge--attention"` and "Needs review" for awaiting_decision.
- The run page renders `class="run-progress"` with `role="status"` when polling (drafting, posting,
  awaiting with sent), and leaves it out for awaiting not sent, approved, rejected and expired.
- The awaiting run page has `figure` with `class="chart-frame"`, a `figcaption` holding the caption,
  `btn--primary` (Approve) and `btn--danger-outline` (Reject).
- The home page has `class="site-header"`, `<label for="topic"`, `id="topic"`, `btn--primary`, and
  at least 3 `class="chip"` links whose hrefs start with `/?topic=`.
- `HomeView()` has default non-empty `examples`, and each `href` URL-encodes its label.
- `GET /?topic=%20Life%20expectancy%20` prefills `value="Life expectancy"`. A 300-character topic
  prefills 200 characters. A plain `GET /` prefills nothing.
- A POST to `/runs` with a blank topic (400) still renders the chips and a `role="alert"` error.
- Every page (home, run, not found, health) has the viewport meta and `color-scheme` meta, and every
  `<link href` / `<script src` starts with `/static/`.
- The CSS served at `/static/app.css` contains each required token name and
  `prefers-color-scheme: dark`, `prefers-reduced-motion` and `:focus-visible`. It contains no
  `@import` and no `http`. These are presence checks only, not a CSS snapshot.

## Out of scope

- Do not change workflows, activities, `services/`, `config.py`/`Settings`, `daily_chart.py` or the
  CLI.
- No new dependencies, JS files, JS frameworks, inline `<script>`, font files or CDN links. Leave
  `htmx.min.js` as it is.
- No theme toggle button: the theme follows `prefers-color-scheme` only.
- No new pages and no runs list (024), refine box (031) or timeline (033).
- No restyle of the health page beyond what it inherits from `base.html` and `app.css`. Do not
  change `health.html` or `partials/health_status.html`.
- Do not edit `tests/test_web.py`. Use no CSS or HTML snapshot or golden-file tests.
- README and plan updates are the scribe's job.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: Wrote the spec for an editorial look: tokens, light/dark themes, system fonts, cards, badge, progress bar, chips as `/?topic=` links (no JS).
Badge label/class and chips come from `view_models.py`; the home route gains only an optional `topic` query param; `runs.py` changes one import.
New tests go in `tests/test_web_ui.py`; `tests/test_web.py` must pass untouched (note the `<button`, "Requested by: x" and poll-string constraints).
Phone/desktop and both-theme looks need a manual devtools check, since tests cannot see visuals.
Next: orchestrator approves (approval: no), then builder on `task/035-ui-refresh`.

**builder**: Implemented tokens/themes CSS, templates, `TopicChip`/`build_home_view`/badge mapping, `GET /?topic=`; added `tests/test_web_ui.py`; `test_web.py` untouched, lint and 224 tests pass.
Visual check NOT done: no Chromium/Playwright/node exists in this environment (none found on disk). Did a computed contrast check of token pairs instead (all >= 4.5:1 text, >= 3:1 borders/buttons, both themes). Orchestrator/human must eyeball 360/1280px, light/dark.
Added unlisted tokens `--color-field-border` (input/chip border for 3:1) and `--color-chart-bg`. Next: verifier.

**verifier**: `just lint` passed (ruff check, ruff format, pyright 0 errors). `just test` passed (224 tests).
`git diff origin/main -- tests/test_web.py` is empty, so test_web.py is unchanged.
All checks pass. Visual (360/1280px, light/dark) check is still open for the human.
Next: reviewer.

**builder** (fix 1): chips were hit by `.card ul` (padding-left, margin 0); scoped that rule to `ul:not(.chips)` and set chips margin-top to --space-3. Lint and tests pass; not re-screenshotted.
