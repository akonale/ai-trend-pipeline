# 038: Flow steps 1 and 2 plus the stepper

Status: pr-open

## Goal

Split today's single home form into a guided flow: step 1 at `/` picks the topic, step 2 at
`/new/style?topic=...` holds the per-chart options and a read-only summary of the saved style, and
Generate posts to the unchanged `POST /runs`. A shared stepper (1 Topic, 2 Style, 3 Review, 4 Done)
shows progress on step 1, step 2 and the run page.

## Decisions

### Flow wiring (no server state)

- All flow state travels in the query string or form fields. No cookies, no session.
- Step 1 is a **GET form**: `<form class="topic-form" method="get" action="/new/style">`, field
  `topic`. Next works without JS, the URL is shareable, and browser Back restores the box.
- **Chips only fill the box.** They keep linking to `/?topic=<quoted label>` (unchanged, so
  `EXAMPLE_TOPICS`, `TopicChip.href` and their tests stay as they are). Reason: the person can
  refine "Life expectancy" to "Life expectancy in Japan" before going on; one extra click is fine.
- `GET /` accepts `topic` plus the seven option fields as optional query strings (`chart_type`,
  `time_preset`, `from_year`, `to_year`, `countries`, `tone`, `instructions`, all `str = ""`).
  Non-empty option values are re-emitted on step 1 as `<input type="hidden">` ("carry" fields), so
  they reach step 2 again on Next. `GET /` never validates and never returns 400; the prefill is
  still stripped and cut to `MAX_TOPIC_LENGTH` (today's `build_home_view` behaviour).
- `GET /new/style` accepts the same eight query params (`topic: str | None = None`, options `""`):
  - `topic` missing, or blank after `strip()`: **303 to `/`** (exactly `/`, no query).
  - longer than 200 after strip: **400**, renders `home.html` (step 1) with the error
    `"Your topic is too long. Use 200 characters or fewer."` (built from `MAX_TOPIC_LENGTH`),
    `role="alert"`, the typed value (cut to 200 by `build_home_view`) and the carry fields.
  - otherwise 200, renders `new_style.html` (step 2) prefilled from the query. Option values are
    not validated here; `POST /runs` validates them as today.
- The step 1 input keeps `required maxlength="200"`, so browsers never send blank or too long.
- **Back from step 2** is a submit button in the step 2 form:
  `<button type="submit" class="btn btn--secondary" formaction="/" formmethod="get" formnovalidate>Back</button>`.
  It sends the hidden topic and every option field to `GET /`, so values typed on step 2 survive
  the trip to step 1 and back (via the carry fields). It sits **after** "Generate chart" in the DOM
  so pressing Enter in a field still generates (the first submit button is the default).
- The "Change topic" link at the top of step 2 is a plain link to `/?<flow query>` built at render
  time (topic plus non-empty options from the request).
- Flow query helper in `view_models.py`: `flow_query(topic: str, options: OptionsFormValues) -> str`
  using `urllib.parse.urlencode(..., quote_via=quote)`, topic first, then non-empty options in field
  order. So `topic="Life expectancy"` with no options gives `topic=Life%20expectancy`.

### Step 1 (`/`, `home.html`)

- Keeps: stepper (current 1), `<h1>What should today's chart be about?</h1>`, error alert, the card
  with label "Topic", `<input id="topic" name="topic" required maxlength="200" ...>`, the chips list
  (`aria-label="Example topics"`), and one button `btn btn--primary` "Next: choose a style".
- Removes: the `<details class="more-options">` block and all option fields (they move to step 2).
  The form no longer posts to `/runs`.
- Under the chips: `<p class="meta">Tap an example to put it in the box.</p>`.

### Step 2 (`/new/style`, new `new_style.html`)

One `<form class="style-step" method="post" action="/runs">`, containing in order:

1. Stepper (current 2; step 1 is a done link to the Change topic URL) above the `<h1>`.
2. `<h1>Set up this chart</h1>`, then the error alert if any (`<p class="error" role="alert">`).
3. Topic recap card `<section class="card topic-recap">`: `<p class="meta">Topic</p>`,
   `<p class="topic-recap__text">{topic}</p>`, `<a href="/?...">Change topic</a>`, and
   `<input type="hidden" name="topic" value="{topic}">`.
4. Options card `<section class="card chart-options" aria-labelledby="chart-options-h">` with
   `<h2 id="chart-options-h">This chart</h2>`, `<p class="meta">Leave a field on Default to let the
   chart decide.</p>`, then the exact fields from today's home form: same `id`/`name`/`for`, same
   selects built from `chart_types`, `time_presets`, `tones`, same `.year-row`, same help texts and
   `aria-describedby` ids, same `maxlength` values. Always visible: no `<details>`.
5. Saved-style card `<section class="card style-summary" aria-labelledby="style-summary-h">` with
   `<h2 id="style-summary-h">Your saved style</h2>`; either the summary `<dl class="options-list">`
   or the notice `<p class="meta">{notice}</p>`; then `<p class="meta">Changes there apply to every
   chart.</p>` and `<a href="/settings">Edit your style</a>`.
6. Actions `<div class="step-actions">`: `<button type="submit" class="btn btn--primary">Generate
   chart</button>` then the Back button above.

### Saved-style summary and the Postgres rule

**Decision: `GET /new/style` reads the saved style, read-only, and degrades gracefully.** The row
asks for a summary of the saved style, and 039 needs the same read to prefill its editable panel;
a plain link would show nothing useful and be redone next task.

- The route uses `optional_user` (never 401, per 037) and `get_style_service` (lazy store, no I/O
  until `get`). No signed-in user: no store call, notice `"Sign in to see your saved style."`
- `StyleService.get(email)` raising `StyleStoreUnavailable`: page is still **200**, notice
  `"Your saved style could not be loaded right now. Generate still works."` Catch only
  `StyleStoreUnavailable` (the Postgres store maps its errors to it). No 503 on this GET.
- Summary lines (`OptionLine`s): Palette = key capitalised (e.g. "Classic") plus swatches
  (`<span class="swatch-colors" aria-hidden="true"><i style="background: {c}"></i>...</span>`, as in
  settings), Font = key capitalised, Caption tone = key capitalised, Hashtags = `#a #b` or "None",
  Signature = the text or "None".
- `POST /runs` error re-renders do **not** read the style (the route keeps calling one service);
  they show the notice `"Your saved style is applied when you generate."`
- Architecture doc change: "Which routes touch Postgres: `POST /runs` (style lookup), `/settings`,
  and `GET /new/style` (read-only; if the store is down the page still renders 200 with a notice).
  Other routes work while Postgres is down."

### Error re-renders of `POST /runs`

Contract, status codes and messages are unchanged. Only the template changes:

| Case | Status | Template | Message |
| --- | --- | --- | --- |
| topic blank or over 200 | 400 | `home.html` (step 1), typed topic, options as carry fields | `Error: enter a topic of 1 to 200 characters.` (unchanged) |
| invalid options | 400 | `new_style.html` (step 2) | `options_error_message` (unchanged) |
| daily cap | 429 | `new_style.html` | `str(DailyCapReached)` (unchanged) |
| run exists | 409 | `new_style.html` | `Error: {exc}` (unchanged) |
| style store down | 503 | `new_style.html` | `STYLE_UNAVAILABLE_ERROR` (unchanged) |

Topic errors go to step 1 because step 2 cannot show an empty topic, and the error is about the
step 1 field (only reachable by non-browser clients, since step 2 always posts a valid hidden topic).
Step 2 re-renders keep the stepper at 2, the hidden topic and every typed option value.

### Stepper

- Models in `view_models.py`:
  `StepView(number: int, label: str, state: Literal["done", "current", "upcoming"], href: str | None = None)`
  with property-free field `aria_current: bool` (True only for the current step);
  `StepperView(steps: list[StepView])`. Labels: "Topic", "Style", "Review", "Done".
- `build_stepper(current: int, topic_href: str | None = None) -> StepperView`: steps below `current`
  are done, `current` is current, the rest upcoming. Only step 1 gets `href=topic_href`, and only
  when done and `topic_href` is given (step 2 page). Steps 2 to 4 never link: on the run page the run
  has already started, so going back would not change it ("New chart" in the nav starts over).
- Run page mapping (`RUN_STEP` dict): `drafting`, `awaiting_decision`, `posting` -> 3;
  `approved`, `rejected`, `expired` -> 4 (steps 1 to 3 done, step 4 current).
- Partial `partials/stepper.html` (receives `stepper`):
  ```
  <ol id="flow-stepper" class="stepper" aria-label="Progress"{% if oob %} hx-swap-oob="true"{% endif %}>
    <li class="step step--{state}"{% if aria_current %} aria-current="step"{% endif %}>
      [<a href="...">] <span class="step__num">{n}</span> <span class="step__label">{label}</span>
      {% if done %}<span class="step__check" aria-hidden="true">&#10003;</span><span class="visually-hidden">, done</span>{% endif %} [</a>]
    </li>
  </ol>
  ```
  `StepperView` has `oob: bool = False`, used only for the run fragment below.
- Shown on: step 1, step 2 (incl. all their error re-renders) and the run page. Not on
  `/settings`, `/health` or `run_not_found.html`.
- Placement: in each page's `content` block, first, before the `<h1>`.
- Run page and polling: the stepper lives **outside** `#run-status` in `run.html`, so swaps never
  re-render it in place. To move from 3 to 4 when polling ends, the `/runs/{id}/status` fragment
  also emits the stepper with `hx-swap-oob="true"` (htmx swaps it by id `flow-stepper`). Wiring:
  `build_run_view(..., fragment: bool = False)` sets `RunView.stepper` with `oob=fragment`;
  `_render_run` gets a `fragment` flag, true only for the status route; `run_status.html` ends with
  `{% if view.stepper.oob %}{% with stepper = view.stepper, oob = true %}{% include "partials/stepper.html" %}{% endwith %}{% endif %}`.
  `run.html` includes the stepper normally (no oob). The stepper has no focusable items on the run
  page, so the swap cannot steal focus; identical markup means no visible flicker.
- `build_shell`: "New chart" is also current for paths starting with `/new/`.

### Design (from the 036 "graph paper and ink" system; no new tokens)

- Plan: colour stays ink `#15172b` on paper `#e3ecdf`, cobalt `#2448d8` for done steps and the
  primary button, marigold `#ffc93c` for the current step (with `--shadow-sm` 4px hard shadow) as
  the one bold element per page; type stays `--font-display` (Arial Black stack) for numbers and
  headings, system body; layout single left-aligned 44rem column, stepper full width under the
  header. Checked against the plan: no cream/terracotta, no all-caps eyebrows, numbering is used
  because the content is a real sequence. Contrast pairs are the ones already tested in 036.
- Stepper: use the existing 036 rules unchanged. Add only `.step__label { white-space: nowrap; }`
  and `.step__check { margin-left: var(--space-1); }`; done-step links get
  `text-decoration-thickness: 2px` from `:where(a)` (already), colour `inherit` (already).
  At 360px the 4 columns are 1fr each with 0.8125rem labels; the longest label "Review" fits.
- `.topic-recap__text`: `font-family: var(--font-display); font-size: var(--text-xl);
  font-weight: 900; line-height: 1.1; margin: 0 0 var(--space-2); overflow-wrap: anywhere;`.
- Rename `.more-options label` and `.more-options .meta` selectors to `.chart-options label` and
  `.chart-options .meta`; delete `.more-options` and `.more-options summary` rules (no longer used).
- `.style-summary .swatch-colors { display: inline-flex; width: 4rem; vertical-align: middle;
  margin-right: var(--space-2); }`.
- `.step-actions { display: flex; flex-direction: column; gap: var(--space-3); margin-top: var(--space-5); }`;
  inside `@media (min-width: 40rem)`: `.step-actions { flex-direction: row; }`. Buttons full width
  on phones, natural width above 40rem. Primary first, Back second (also visual order, so focus
  order matches).
- Step 1 button stays in `.topic-row` (stacks under the box on phones, beside it above 40rem).
- No new JS, no external assets, `prefers-reduced-motion` already covers `.btn`.

### Copy (exact strings)

| Where | Text |
| --- | --- |
| Step 1 h1 | What should today's chart be about? (unchanged) |
| Step 1 button | Next: choose a style |
| Step 1 chips help | Tap an example to put it in the box. |
| Step 1 too long (from step 2 GET) | Your topic is too long. Use 200 characters or fewer. |
| Step 2 title tag | Set up this chart - Topic Studio |
| Step 2 h1 | Set up this chart |
| Topic recap label / link | Topic / Change topic |
| Options h2 / intro | This chart / Leave a field on Default to let the chart decide. |
| Style h2 | Your saved style |
| Style notices | Your saved style could not be loaded right now. Generate still works. / Sign in to see your saved style. / Your saved style is applied when you generate. |
| Style footer / link | Changes there apply to every chart. / Edit your style |
| Buttons | Generate chart / Back |
| Stepper | aria-label "Progress"; labels Topic, Style, Review, Done; hidden ", done" |

## Acceptance criteria

- [ ] `GET /` shows the stepper (step 1 current), topic box, chips linking to `/?topic=...`, and a
      GET form to `/new/style` with "Next: choose a style"; no option fields and no `<details>`.
- [ ] `GET /new/style` with missing or blank `topic` returns 303 to `/`; over 200 characters returns
      400 on step 1 with the friendly error and the typed value; otherwise 200 with the read-only
      topic, Change topic link, all seven option fields (same names), the saved-style summary,
      "Generate chart" posting to `/runs` with a hidden `topic`, and Back.
- [ ] `GET /new/style` renders 200 with the notice when the style store raises
      `StyleStoreUnavailable`, and with the sign-in notice (no store call, no 401) in cloudflare
      mode without the header.
- [ ] Typed values survive: query prefill on both steps, Back carries topic and options to step 1
      and Next carries them back; `POST /runs` 400 (options), 429, 409 and 503 re-render step 2 with
      stepper current 2 and all values; topic errors re-render step 1. Status codes and messages
      unchanged.
- [ ] The stepper shows 1 current on step 1; 1 done (linked) and 2 current on step 2; 3 current for
      drafting, awaiting_decision and posting runs; 4 current for approved, rejected, expired; the
      status fragment carries it with `hx-swap-oob="true"`; it is absent from settings, health and
      the not-found page.
- [ ] No horizontal scroll at 360px on step 1, step 2 and the run page; `just lint` and `just test`
      pass; existing tests pass with only the assertion edits listed below.

## Files to touch

- `src/ai_trend_pipeline/web/view_models.py` (`StepView`, `StepperView`, `build_stepper`,
  `RUN_STEP`, `HiddenField`, `flow_query`, `StyleSummaryView`, `StyleStepView`,
  `build_style_step_view`, slimmer `HomeView` with `carry` and `stepper`, `RunView.stepper`,
  `build_run_view(fragment=...)`, `build_shell` `/new/` rule, notice and error constants)
- `src/ai_trend_pipeline/web/routes/home.py` (`GET /` option params; new `GET /new/style`)
- `src/ai_trend_pipeline/web/routes/runs.py` (re-render templates per the table; `fragment` flag)
- `src/ai_trend_pipeline/web/templates/home.html`, `run.html`, `partials/run_status.html`
- `src/ai_trend_pipeline/web/templates/new_style.html` (new), `partials/stepper.html` (new)
- `src/ai_trend_pipeline/web/static/app.css`
- `tests/test_web_flow.py` (new); edits in `tests/test_web.py`, `tests/test_web_options.py`
- `docs/architecture/tech.md`, `docs/architecture/business.md`

## Tests to write

New `tests/test_web_flow.py` (reuse `RecGateway`, `client_for`, `auth_client` from `tests.test_web`,
`InMemoryStyleStore`, `BrokenStore`; override `get_style_service` for step 2):

- Step 1: GET form to `/new/style`, `name="topic"`, "Next: choose a style", chips, no `name="chart_type"`, stepper with step 1 `aria-current="step"`.
- Step 1 carries options: `/?topic=a&countries=France` has a hidden `countries` input with `France`; empty options emit no hidden inputs.
- Step 2 redirects: no `topic`, `topic=`, `topic=%20%20` each give 303 with location `/`.
- Step 2 too long: `topic=x*201` gives 400, step 1 page, friendly error with `role="alert"`, `value="xxx..."` (200 chars).
- Step 2 page: topic text, hidden topic input, all seven `name=` fields with labels, `action="/runs"`, `method="post"`, "Generate chart", Back button with `formaction="/"`, `href="/?topic=..."` Change topic link, no `<details`.
- Step 2 prefill from query: `countries=France&tone=punchy&from_year=1990` shows `value="France"`, `value="1990"`, the punchy option selected.
- Step 2 stepper: step 1 done with link and hidden ", done", step 2 `aria-current="step"`, steps 3 and 4 upcoming.
- Step 2 style summary from an `InMemoryStyleStore` profile (palette, font, tone, hashtags, signature shown; `/settings` link).
- Step 2 with `BrokenStore(StyleStoreUnavailable())` is 200 with "could not be loaded right now. Generate still works."
- Step 2 in cloudflare mode without the header is 200 with the sign-in notice and the store is never called (`BrokenStore()`).
- `POST /runs` 400 (bad options), 429, 409 and 503 each render step 2: stepper step 2 current, hidden topic, typed option values, same message and status as before.
- `POST /runs` blank topic renders step 1 with the unchanged error and carries typed options as hidden fields.
- `POST /runs` success still 303 to `/runs/{id}` with parsed options.
- Run page stepper per status (parametrised over the six statuses: 3 or 4 current, steps before it done, no links in the stepper).
- Status fragment contains `id="flow-stepper"` with `hx-swap-oob="true"`; the full run page stepper has no `hx-swap-oob`.
- No stepper (`class="stepper"`) on `/settings`, `/health`, `/runs/nope`.
- `build_stepper` unit test: states for current 1 to 4, single `aria_current`, href only on step 1 when given.
- `flow_query` unit test: encoding with `%20`, skips empty options, keeps field order.
- `build_shell("/new/style", ...)` marks "New chart" current.
- CSS contains `.step-actions`, `.topic-recap__text`, `.chart-options label`, and no `.more-options`.

### Existing test assertions that change (only these)

- `tests/test_web.py::test_home_page_has_form`: change `.get("/")` to `.get("/new/style?topic=abc")`. All asserts unchanged.
- `tests/test_web_options.py::test_home_has_closed_details_with_labelled_fields`: rename to
  `test_style_step_has_labelled_fields`; change `.get("/")` to `.get("/new/style?topic=Life%20expectancy")`;
  replace the two lines asserting `<details class="more-options">` and `<summary>More options</summary>`
  with `assert "<details" not in text`. The other asserts stay (the Change topic link satisfies
  `href="/?topic=Life%20expectancy"`).
- `tests/test_web_options.py::test_invalid_options_are_400_and_keep_values`: replace
  `'<details class="more-options" open>' in res.text` with `'aria-current="step"' in res.text and "Set up this chart" in res.text`.
- `tests/test_web_options.py::test_other_errors_keep_option_values`: replace `"open>" in res.text`
  with `"Set up this chart" in res.text`.

Everything else (including `test_daily_cap_returns_429_and_keeps_topic`, the 503 test, which match
the hidden `value="..."`, `test_blank_topic_error_keeps_chips`, `test_topic_prefill`,
`test_home_components`, `test_home_view_examples_encoded`, the shell tests and
`test_read_routes_never_touch_the_style_store`) must pass unchanged.

## Docs

- `docs/architecture/tech.md`: routes table (`GET /` = step 1, topic box and chips, `?topic=` and
  option params prefill; new row `GET /new/style` = step 2, 303 to `/` without topic, 400 over 200,
  uses `optional_user`, reads the style read-only and never fails on it; `POST /runs` note: topic
  errors re-render step 1, other errors step 2); the Postgres sentence above; templates list
  (`new_style.html`, `partials/stepper.html`); `StepperView`/`build_stepper`, run-page stepper with
  out-of-band swap; replace the "Reserved for task 038" sentence (stepper is now used; `.btn--secondary`
  used by Back; `.check` still reserved).
- `docs/architecture/business.md`: a short "Starting a run" paragraph before section 3: step 1
  topic (chips fill the box), step 2 per-chart options plus the saved style shown read-only, then
  Generate; daily cap and validation unchanged.

## Out of scope

- Review/Done restyle of the run page (040); only the stepper is added there.
- Editable style or per-chart style overrides (039); step 2 shows the saved style read-only.
- Any backend, service, workflow or `PostOptions` change; any change to `POST /runs` parsing,
  status codes or messages.
- JavaScript beyond the htmx already vendored; new tokens or colours; README (041).

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->
**planner**: Wrote spec. GET-form flow with query carry, step 2 reads style read-only (200 + notice on failure), topic errors on POST /runs re-render step 1, other errors step 2; stepper outside the polled div, updated by hx-swap-oob from the status fragment.
4 existing test assertions change (listed under "Existing test assertions that change"); docs: tech.md routes, Postgres rule, templates; business.md flow paragraph.
frontend-design skill was in context; design reuses 036 tokens, no new colours. One PR. Next: approval (orchestrator), then builder.
**builder**: Built per spec with the 4 listed test edits; just lint and test pass (388+). frontend-design skill was in context. Browser-checked 360/1280, light/dark, no overflow, no label wrap, OOB poll swap works; shots in scratchpad build038/shots.
Small CSS deviations: `.step__check` absolutely positioned top-right (stays on one line with link steps), `.step a` flex column, focus outline fix on the cobalt done link, `.topic-form > .meta` margin. Next: verifier.
**verifier**: just lint (ruff check, ruff format --check, pyright) and just test (388 passed) pass. Test diff has only the 4 listed assertion edits; new tests/test_web_flow.py. No leftover servers; ai-trend-prod containers untouched; no external assets in app.css or templates.
All checks pass.
**reviewer**: Approved. All criteria and listed tests present; the 4 edited assertions still pin the behaviour; POST /runs contract unchanged; escaping probed (quotes/angle brackets in topic and options are escaped in fields, hidden inputs and URL-encoded Change topic link); redirects are the constant `/`.
Notes: (1) carry/option values are not length-limited on GET (escaped, harmless). (2) builder added one more CSS rule (`.step a .step__label` underline) beyond the four reported; it uses no new colours. (3) No dedicated escaping test for step 2 echoes; consider adding later.
**scribe**: Updated README Web app (step 1 picks topic, step 2 /new/style holds options and Generate; options no longer in a collapsed home form). Pushed branch.
PR: https://github.com/akonale/ai-trend-pipeline/pull/42. Next: orchestrator visual check at 360px and 1280px, then the human merges (not merged by scribe).
