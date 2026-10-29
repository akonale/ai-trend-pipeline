# 039: Per-chart style overrides

Status: reviewed

## Goal

Let a person change palette, font, caption tone, hashtags and signature for one chart on step 2
(`/new/style`), with each blank field falling back to their saved style. An optional "Save as my
default" checkbox also saves the result as their profile, with the same validation as `/settings`.

## Decisions

### 1. Data model (`post_options.py`, `style.py`)

- **Validation is reused, not copied.** In `style.py`, move the body of each `ChartStyle` field
  validator into a public pure function: `check_palette(str) -> str`, `check_font`, `check_tone`,
  `check_hashtags(list[str]) -> list[str]`, `check_signature(str) -> str` (strips). `ChartStyle`'s
  validators call them. Messages and behaviour stay the same, so `tests/test_style.py` passes as is.
- New optional fields on `PostOptions`, all defaulting to `None` (old payloads still validate):

  | Field | Type | `None` | Other values |
  | --- | --- | --- | --- |
  | `palette` | `str \| None` | use the profile | a `PALETTES` key (`check_palette`) |
  | `font` | `str \| None` | use the profile | a `FONTS` key (`check_font`) |
  | `hashtags` | `list[str] \| None` | use the profile | `[]` = no hashtags on this chart; else `check_hashtags` |
  | `signature` | `str \| None` | use the profile | `""` = no signature on this chart; else `check_signature` |

  `tone` stays as it is, but its validator now calls `check_tone` (same message). Validators skip
  `None`.
- `STYLE_OVERRIDE_FIELDS = ("palette", "font", "tone", "hashtags", "signature")` in
  `post_options.py`.
- **`merge_style(profile, options)`** (pure), field by field: per-chart value (not `None`) > profile
  value > `ChartStyle()` default.
  - If `options` is `None` or sets none of the five fields, return `profile` itself (same object,
    can be `None`). So no-options and options-without-style runs are byte-for-byte unchanged.
  - Otherwise return `ChartStyle.model_validate({**(profile or ChartStyle()).model_dump(),
    **overrides})`, where `overrides` holds only the non-`None` fields. Build `overrides` with
    explicit `if options.x is not None` lines, not `getattr` (Pyright strict). `model_validate`
    (not `model_copy`) runs the same validators as `/settings`.
- **Form parser:** `options_from_form` keeps its seven positional parameters and gains keyword-only
  ones with defaults, so today's callers and tests are unchanged:
  `*, palette: str = "", font: str = "", hashtags: str = "", signature: str = "",
  hashtags_none: bool = False, signature_none: bool = False`.
  What "blank" means for each field:
  - `palette`, `font`: `strip()` is `""` (the "Saved" choice) → field omitted (`None`).
  - `hashtags`: if `hashtags_none` is ticked → `[]`. Otherwise `parse_hashtags(text)`. An empty
    list (blank or only `#`, commas, spaces) → omitted.
  - `signature`: if `signature_none` is ticked → `""`. Otherwise `strip()`; `""` → omitted.
  - **A ticked "No … on this chart" box wins over typed text.** The text is ignored, and the run
    page shows "None" for that field, so the result is visible.
  - Still returns `None` when nothing at all is set (today's rule).

### 2. Form field names and how they flow

- New step 2 field names: `palette` (radio), `font` (select), `hashtags` (text), `signature`
  (text), `hashtags_none` (checkbox, value `1`), `signature_none` (checkbox, value `1`) and
  `save_default` (checkbox, value `1`). None of them clash with `topic`, `chart_type`,
  `time_preset`, `from_year`, `to_year`, `countries`, `tone` or `instructions`. (`/settings` uses
  the same names, but it is a separate form and page.)
- `OptionsFormValues` gets these seven `str = ""` fields **after** `instructions`, in the order above.
  `_carry`/`flow_query` pick them up as they are: only non-empty values are carried, so the
  existing exact-URL assertions do not change.
- `GET /` and `GET /new/style` take the seven as query params (`str = ""`), like the existing
  seven. Back (submit to `GET /`) and step 1's hidden carry fields bring them round the step 1
  trip. "Change topic" links include them through `flow_query`. Unticked checkboxes are not sent,
  so they stay unticked.
- `POST /runs` takes them as `Form()` params (`str = ""`). The route puts them all in `typed`
  (re-renders keep everything), then calls
  `options_from_form(..., palette=..., font=..., hashtags=..., signature=...,
  hashtags_none=hashtags_none == "1", signature_none=signature_none == "1")` and passes
  `save_as_default=save_default == "1"` on the request.

### 3. Step 2 panel UX (`new_style.html`, `view_models.py`)

The read-only "Your saved style" card becomes an editable card,
`<section class="card style-panel" aria-labelledby="style-panel-h">` with
`<h2 id="style-panel-h">Style for this chart</h2>`, placed after the "This chart" card. The
**Caption tone** select (`id/name="tone"`, the same markup) moves from "This chart" into this panel.

"Prefilled" here means that every control starts on a **Saved** choice that shows the saved value
and submits blank, so blank always means "use my saved style". This avoids turning every chart
into a full set of overrides and keeps the run page's "Options used" list down to what was
actually changed. Order and markup:

1. Intro `<p class="meta">`. If the profile was loaded: `Your saved style is filled in. Change any
   field to use something else on this chart only.` If not: the existing `view.notice` text
   (`STYLE_NOTICE_UNAVAILABLE`, `STYLE_NOTICE_SIGN_IN` or `STYLE_NOTICE_APPLIED`, unchanged).
2. **Palette**: `<fieldset><legend>Palette</legend><div class="swatches">` with radio swatches,
   reusing the `/settings` `.swatch` markup: a first radio `value=""` labelled `Saved: Ocean` with
   the saved palette's `swatch-colors` (unknown profile: label `Saved style`, no colour strip), then
   one radio per `PALETTES` key, labelled with `key.capitalize()` ("Okabe-ito") and its colour strip.
   A radio is checked when its key equals the typed `palette`, so `""` (Saved) is the default.
3. **Font**: `<label for="font">Font</label>`, a select with first option `value=""` `Saved: Serif`
   (unknown: `Saved style`), then `Sans`, `Serif`, `Mono`.
4. **Caption tone**: the same select, but the blank option's label changes from `Your style` to
   `Saved: Punchy` (unknown: `Saved style`). The help text becomes `Sets how the caption reads.`
5. **Hashtags**: text input `id/name="hashtags"`, value from the typed text, help
   (`aria-describedby="hashtags-help"`): `Saved: #ai #data. Leave blank to use it. Up to 3,
   separated by spaces or commas.` If there are no saved hashtags: `Saved: None. …`. Unknown
   profile: `Leave blank to use your saved hashtags. Up to 3, separated by spaces or commas.` Then a
   `.check-row` holding the `hashtags_none` checkbox and `<label for="hashtags_none">No hashtags on
   this chart</label>`.
6. **Signature**: text input `id/name="signature"`, `maxlength="40"` (`MAX_SIGNATURE_CHARS`), help:
   `Saved: By Ash. Leave blank to use it.` / `Saved: None. Leave blank to use it.` / unknown:
   `Leave blank to use your saved signature.` Then a `.check-row` with `signature_none` and `No
   signature on this chart`.
7. **Save as my default**: a `.check-row.save-default` holding the checkbox `save_default` and
   `<label for="save_default">Save as my default</label>`, plus help
   `<p class="meta" id="save_default-help">Also saves these choices as your style for future charts.
   Blank fields keep your saved value.</p>`. When it cannot be used, the checkbox is **left out of
   the page** (not disabled: disabled controls are skipped by some screen readers and look faint)
   and replaced by `<p class="meta">` with the reason:
   - store down on GET: `Saving a default is off right now because your saved style could not be
     loaded.` (`SAVE_DEFAULT_UNAVAILABLE`)
   - no user on GET (cloudflare, no header): `Sign in to save a default style.`
     (`SAVE_DEFAULT_SIGN_IN`)
   - `POST /runs` re-renders always show the checkbox, ticked if it was ticked.
8. The existing `<a href="/settings">Edit your style</a>` and `<p class="meta">Changes there apply
   to every chart.</p>`.

When the store is down, `GET /new/style` still returns 200. The panel shows the "Saved style"
labels with no values, the unavailable notice and the save note, and Generate still works (blank
fields are merged when the run starts, if the store is back by then).

View model changes (`StyleStepView`/`build_style_step_view`):
- Remove `summary` and `style_summary_lines` (no longer used).
- Add `palettes: list[PaletteChoice]` (new model: `key`, `label`, `colors`, `checked`),
  `fonts: list[ChoiceOption]`, `profile_known: bool`, `hashtags_help: str`, `signature_help: str`,
  `max_signature: int = MAX_SIGNATURE_CHARS`, `can_save_default: bool`, `save_note: str`.
- New keyword param `save_note: str = ""`, where `can_save_default = not save_note`.
  `GET /new/style` passes `SAVE_DEFAULT_SIGN_IN` or `SAVE_DEFAULT_UNAVAILABLE` alongside the
  matching notice. `POST /runs` re-renders pass nothing, as today: `style=None`,
  `notice=STYLE_NOTICE_APPLIED`.
- All labels and help strings are built in the view model. The template only loops and branches.

### 4. "Save as my default" semantics (`services/charts.py`)

- `StartChartRequest` gets `save_as_default: bool = False`.
- `ChartService.start` order: **(1)** daily-cap check (unchanged, first); **(2)** pick `wf_id`;
  **(3)** read the profile (`StyleStore.get`, unchanged); **(4)** `style = merge_style(profile,
  options)`; **(5)** if `save_as_default` and `requested_by` and a store is set:
  `await self._styles.save(requested_by, style or ChartStyle())`; **(6)** `gateway.start`.
- **Save before start, so a failure leaves nothing half-done.** A started Temporal run cannot be
  rolled back, but a saved profile is harmless and idempotent (saving the same style twice is
  safe). If the save raises `StyleStoreUnavailable`, the error propagates, **no run starts**, and
  the route's existing `except StyleStoreUnavailable` re-renders step 2 with **503** and
  `STYLE_UNAVAILABLE_ERROR`, keeping every typed value and the ticked box. If `gateway.start` fails
  after a successful save (409/Temporal down), the profile stays saved. That is acceptable and
  documented.
- **What gets saved: the whole effective style.** Because blank fields inherit from the profile,
  the effective style equals the old profile with only the fields the person changed replaced. So
  "save effective" and "save only what was set" give the same row, and saving a full `ChartStyle`
  means no new store method. With no stored profile, the defaults fill the gaps.
- The save goes through the same `StyleStore` that `StyleService` wraps (`StyleService.save` only
  passes through), so `ChartService` keeps its existing `styles: StyleStore | None` parameter and
  no new dependency is wired. Validation is `ChartStyle`'s, the same as `/settings`, because
  `merge_style` uses `model_validate`.
- **Capped person: 429, nothing saved, no store call** (the cap check comes first).
  `save_as_default` without a requester or store is ignored (CLI, tests).

### 5. Validation errors (400, step 2, values kept)

- `PostOptions` errors on the new fields come back as `ValidationError`s with `loc` set to the
  field name. `view_models.py` gets one `STYLE_ERRORS` dict (palette, font, tone, hashtags,
  signature → the exact current `style_error_message` strings), and `style_error_message` iterates
  it, so its output is unchanged. `OPTIONS_ERRORS` keeps its five entries in their current order and
  appends `palette`, `font`, `hashtags` and `signature` taken from `STYLE_ERRORS`. Tone in step 2
  keeps `Caption tone: pick one of the listed tones.`
- The exact new step 2 messages:
  - `Palette: pick one of the listed palettes.`
  - `Font: pick one of the listed fonts.`
  - `Hashtags: use at most 3, each 1 to 24 letters, digits or underscores.`
  - `Signature: use at most 40 printable characters on a single line.`
- Re-render: `role="alert"`, the stepper on step 2, the typed `hashtags`/`signature` text exactly as
  typed, ticked boxes still ticked, no store read and no store write.

### 6. Replay and compatibility

- Every new `PostOptions` field defaults to `None`, so `DailyChartInput`/`ReviewState` payloads
  from before 039 deserialize unchanged. No workflow code changes.
- `option_lines` (run page "Options used") adds, after "Caption tone" and before "Instructions":
  `Palette` (`key.capitalize()`), `Font` (`key.capitalize()`), `Hashtags` (`#a #b`, or `None` for
  `[]`) and `Signature` (text, or `None` for `""`). Lines appear only for non-`None` fields.
- `options=None`: `merge_style` returns the profile itself, and `data.style`/`data.options` stay as
  today.

### 7. Architecture

- Routes stay thin: parse the form, call `ChartService.start` once, render. Only `POST /runs`,
  `GET /new/style` and `/settings` touch Postgres, as today. `GET /new/style` still makes exactly one
  `StyleService.get` call. `GET /` never touches the store.
- `style.py` and `post_options.py` stay pure (no I/O). No workflow or activity code changes:
  `write_chart_spec` already applies `data.style` with `apply_style_to_spec`/`caption_suffix`, and
  the prompts are unchanged (the caption budget already follows the suffix).

### Design notes (UI)

Plan: no new colours, fonts or tokens. This panel reuses the 036 system as it is. One accent: the
checked swatch already gets `--color-highlight` fill and `--shadow-sm`, and that is the only bold
element in the panel. Everything else uses quiet form chrome so the chart choices stand out. Left
aligned, single column, same rhythm as `.chart-options`. Reviewed against the brief: neo-brutalist
(3px borders, hard shadows), htmx/plain CSS, no JS, 360px, AA. Nothing in it is new decoration.

CSS (`app.css`, replace the now-unused `.style-summary .swatch-colors` rule):
- `.style-panel > label, .style-panel legend { display: block; margin: var(--space-4) 0
  var(--space-2); padding: 0; font-weight: 700; }`. Use the **`>` child selector**: a plain
  `.style-panel label` (0,1,1) would beat `.swatch` (0,1,0) and break the swatch labels.
- `.style-panel fieldset { min-width: 0; margin: 0; padding: 0; border: 0; }`
- `.style-panel .meta { margin-top: var(--space-1); }`
- `.style-panel .swatches { grid-template-columns: repeat(auto-fit, minmax(7.5rem, 1fr)); }`
  (two columns at 360px, where the card's inner width is about 274px).
- `.check-row { display: flex; align-items: center; gap: var(--space-3); margin: var(--space-2) 0
  var(--space-3); }` and `.check-row label { margin: 0; font-weight: 700; }`. The checkbox keeps
  the base 1.25rem size and global focus ring. Make the label the click target, about 44px tall:
  `.check-row label { padding-block: var(--space-2); }`.
- `.save-default { margin-top: var(--space-5); padding-top: var(--space-4); border-top:
  var(--border-thin) solid var(--color-border); }`. This divider separates "this chart" choices
  from the action that changes every chart.
- Contrast: only existing token pairs (text, muted and on-fill on surface/highlight) are used, so
  `tests/test_design_tokens.py` passes unchanged.

## Acceptance criteria

- [ ] Choosing a palette or font on step 2 sets that value in the started run's `DailyChartInput.style`
      (and `options`) while the stored profile is unchanged; the next run without overrides uses the
      profile again.
- [ ] Blank fields fall back to the profile field by field; "No hashtags/signature on this chart"
      clears that field for this run only; `options=None` behaves exactly as before.
- [ ] With "Save as my default" ticked the effective style is saved (before the run starts); a save
      failure returns 503 with the friendly message, values kept and no run started; a capped
      person gets 429 with nothing read or saved.
- [ ] Invalid palette, font, hashtags or signature return 400 on step 2 with the exact messages in
      Decision 5, the typed text kept, and no run started.
- [ ] `GET /new/style` shows the editable panel prefilled from the profile (one store read), and
      still returns 200 with the notice and the save note (no checkbox) when the store raises.
- [ ] A time-skipping workflow test replays a pre-039 payload (no new fields) to `awaiting_decision`;
      `just lint` and `just test` pass with **no existing test assertions changed**.

## Files to touch

- `src/ai_trend_pipeline/style.py` (extract `check_*` functions; `ChartStyle` validators call them)
- `src/ai_trend_pipeline/post_options.py` (new fields, `STYLE_OVERRIDE_FIELDS`, parser, `merge_style`)
- `src/ai_trend_pipeline/services/charts.py` (`save_as_default`, save before start)
- `src/ai_trend_pipeline/web/routes/runs.py`, `web/routes/home.py` (new form/query params)
- `src/ai_trend_pipeline/web/view_models.py` (form values, step 2 view, `PaletteChoice`,
  `STYLE_ERRORS`, `OPTIONS_ERRORS`, `option_lines`, save-note constants)
- `src/ai_trend_pipeline/web/templates/new_style.html`
- `src/ai_trend_pipeline/web/static/app.css`
- `tests/test_web_style_overrides.py` (new)
- `docs/architecture/tech.md` (route table rows for `/`, `/new/style`, `POST /runs`; "Save as my
  default" writes to Postgres from `POST /runs`)
- `docs/architecture/business.md` ("Merge precedence": all five fields can be overridden per
  chart; blank/None rules; how Save as my default works and its order)
- `docs/architecture/README.md` (only the `merge_style` line, if its wording becomes wrong)

## Tests to write

All in `tests/test_web_style_overrides.py`. Use fakes from `tests/style_fakes.py`,
`tests/test_web.py` (`RecGateway`), `tests/test_web_settings.py` (`BrokenStore`) and
`tests/test_daily_chart.py`/`tests/test_chart_spec.py` helpers. For route tests, build
`ChartService(gateway, settings, styles=store)` and `StyleService(store)` on the **same** store.
`flow_client` uses no store on the chart service, so add a local client helper.

- `check_*` functions return the same values/errors as `ChartStyle` (one valid and one invalid per field).
- `PostOptions` accepts `None`, `[]`, `""` for the new fields and rejects a bad palette, font,
  hashtag and signature (with `loc` set to the field).
- `merge_style` precedence, one parametrized case per field: override beats profile, `None`
  inherits the profile, with no profile the default fills in, and `merge_style(p, PostOptions(chart_type="bar")) is p`.
- `merge_style` clear semantics: `hashtags=[]` and `signature=""` clear the profile values.
- `options_from_form` with the new keywords: blank → `None`; `"#ai, data"` → `["ai","data"]`; a
  ticked none-box gives `[]`/`""` even with text typed; `"#  ,"` → omitted.
- `ChartService.start` passes the merged style plus the options to the gateway and does not save
  when unticked (store unchanged).
- `ChartService.start` with `save_as_default` saves the effective style (changed fields replaced,
  others kept) before `gateway.start` (record the call order).
- Save raising `StyleStoreUnavailable` → exception propagates and `gateway.started == []`.
- Cap reached with `save_as_default` → `DailyCapReached` and no store get or save.
- `POST /runs` with `palette=sunset&font=mono` → 303, and the run input has `style.palette ==
  "sunset"`, `style.font == "mono"`, the other fields from the profile, and the profile unchanged.
- `POST /runs` with `save_default=1` → the profile in the store equals the effective style.
- `POST /runs` with `save_default=1` and a store whose `save` raises → 503,
  `STYLE_UNAVAILABLE_ERROR`, typed hashtags and the ticked box kept, no run started (fake store:
  `get` works, `save` raises).
- `POST /runs` at the cap with `save_default=1` → 429 and the store unchanged.
- `POST /runs` invalid values (`palette=neon`, `font=comic`, `hashtags=a b c d`,
  `hashtags=bad-tag`, `signature=` 41 chars) → 400, the exact message, typed text kept, no run.
- `POST /runs` with `hashtags_none=1` → `options.hashtags == []` and `style.hashtags == []`.
- `GET /new/style` with a stored profile: `Saved: Ocean` radio checked with its swatch, `Saved:
  Serif`, `Saved: Punchy`, the hashtag/signature help strings, the `save_default` checkbox, and one
  store `get` call.
- `GET /new/style` prefills typed overrides from the query (`palette=ink` checked, `hashtags` value,
  ticked boxes) and Back/Change-topic URLs carry them (`flow_query`).
- `GET /new/style` with the store raising → 200, notice, `SAVE_DEFAULT_UNAVAILABLE` text, no
  `name="save_default"`; cloudflare with no header → 200, `Sign in to save a default style.`
- `GET /?topic=a&palette=ink&save_default=1` re-emits both as hidden carry fields.
- `option_lines` shows Palette/Font/Hashtags/Signature lines, with `None` for `[]`/`""`.
- The chart output differs per override: run `write_chart_spec` (the `tests/test_chart_spec.py`
  recorded fixture client) with the effective styles of two overrides on the same profile, and
  assert that `config.range.category` and `config.font` differ.
- Old payload replay: in `WorkflowEnvironment.start_time_skipping()` with the pydantic converter,
  start `DailyChartWorkflow` with a raw dict payload whose `options` has only pre-039 keys
  (`{"chart_type": "bar", "tone": "punchy", "countries": [], "instructions": ""}`) and a pre-039
  `style`. Reach `awaiting_decision` and assert that the new fields are `None` in the
  activity inputs and the query state.
- CSS: `.style-panel > label`, `.check-row` and `.save-default` exist and `.style-summary` is gone.

Existing tests: **none change**. The copy above was chosen so that 038's assertions ("Your saved
style", "Ocean", "Serif", "Punchy", "#ai #data", "By Ash", "Classic"/"None", `swatch-colors`,
`href="/settings"`, "Changes there apply to every chart.", "Your saved style is applied when you
generate.", `<option value="punchy" selected>`, exact `flow_query` URLs, labelled `tone`) still
hold. If one has to change, the builder stops and reports it.

## Out of scope

- The review/done restyle (040), any change to `/settings` (route, template or messages), the
  prompts, `cli.py`.
- New routes, new store methods, per-person stored overrides beyond the existing profile, cookies
  or sessions.
- JavaScript beyond the htmx already present; new colours, fonts or tokens.
- Workflow or activity code changes.

**Split if it grows:** one PR is expected (about 8 source files plus one test file). If the diff
gets out of hand, split it into 039a (model, `merge_style`, parser, service save, unit tests) and
039b (routes, view model, template, CSS, web tests).

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->
**planner**: Wrote the spec: PostOptions palette/font/hashtags/signature (None = inherit, []/"" = clear via "No … on this chart" boxes), field-by-field merge_style, save-before-start in ChartService, editable step 2 panel with "Saved: X" choices.
Copy is chosen so that no existing test assertion changes. frontend-design skill was loaded; no new tokens or colours.
Next: approval (orchestrator, row is `no`), then the builder on `task/039-style-overrides`.
**builder**: Built per spec (check_* in style.py, new PostOptions fields, field-by-field merge_style, save-before-start in ChartService, editable step 2 panel, tests/test_web_style_overrides.py incl. pre-039 replay); no existing assertion changed; tech.md and business.md updated, architecture/README.md wording still right.
Visual check done at 360/1280 light+dark (all states, click-through, focus ring, two palette renders): no overflow, no mid-word breaks. frontend-design skill was loaded. Next: verifier.
**verifier**: `just lint` passed (ruff check, ruff format --check, pyright 0 errors). `just test` passed (435 passed).
No existing test line removed (`git diff origin/main -- tests/` shows only additions plus new tests/test_web_style_overrides.py).
No leftover throwaway servers or listeners on 876x, no scratch containers or images. ai-trend-prod containers untouched (listed, all up).
No external URLs or @import in app.css or templates. git status clean apart from this file and the untracked .claude/settings.json. Next: reviewer.
**reviewer**: Approved. Checked diff vs spec and checklist: cap check, read, merge, save, start order; replay test uses a raw pre-039 payload; no existing assertions changed; lint, pyright, 435 tests pass.
Note: a 400 re-render with an invalid palette leaves no radio checked (browser then sends blank = saved); harmless.
