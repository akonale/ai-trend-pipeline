# 065b: Illustration picker (pick the picture that goes in the post)

Status: pr-open

## Goal

In review, show every candidate picture from the story's media manifest (today: the illustrated
card and the story card) as a no-JavaScript radio picker. Send the human's pick with Approve,
freeze it in the approved content hash, and make Bluesky upload exactly the picked still. This is
the part of task 065 that 065a (merged in PR #74) deferred. It adds no new illustration source.

## Context (already merged in 065a, do not redo)

- `MediaManifest` has `illustration_artifact`, `illustration_status` and `selected`. The
  `MediaSelection` enum has the values `illustration` and `card`. A wrap serializer drops the three keys when they are `None`,
  and the two pinned `story_package_hash` literals in `tests/test_media_models.py` guard this.
- `MediaManifest.artifact()` already returns the illustration, so the existing media route serves
  it with no route change.
- In 065a `selected` is always `None`, so publishing always uses the story card. The review page
  shows only the card, plus the failed note when the illustration failed.
- `story_content_hash` excludes `media`, but `story_package_hash` includes it. So setting
  `selected` changes the approved hash and leaves the manifest's `story_content_hash` link valid.

## Decisions

### Candidates come from the manifest, not a hard-coded pair (forward compatibility)

- Add `MediaCandidate` (frozen Pydantic model) to `media/models.py` with these fields:
  - `selection: MediaSelection`
  - `artifact: MediaArtifact`, the still that is posted
  - `animated: MediaArtifact | None`, the review-only GIF, set only for the card when the outcome
    is `gif`
  - `preselected: bool`
- Add `MediaManifest.candidates() -> tuple[MediaCandidate, ...]`. It is the **only** place that
  knows which manifest fields are candidates. The order is fixed: the illustration (when
  `illustration_status` is `matched` or `generic`), then the story card last, because the card is
  the fallback that always exists. A manifest with no `static_artifact` returns `()`.
- **Default rule.** Exactly one candidate has `preselected=True`:
  - `matched`: the illustration is preselected.
  - `generic` (a weak match or no match): the card is preselected.
  - `failed`, `disabled`, `None`, or no illustration: only the card is a candidate, and it is
    preselected.
- Add a pure helper, `with_media_choice(manifest, choice: MediaSelection | None) -> MediaManifest`.
  - If the manifest has fewer than 2 candidates, it returns the manifest **unchanged** (`selected`
    stays `None` and the hash stays the same).
  - Otherwise it sets `selected = choice` when `choice` is one of the offered selections, and
    `card` in every other case. So a missing choice (from the CLI or an old form) or a forged
    value means `card`.
- **What a future source needs.** A later source, such as an Unsplash-style photo provider, needs
  only these changes:
  - a new `MediaSelection` value
  - a new optional manifest field, also dropped when `None`
  - one entry in `candidates()`
  - its tile copy in `view_models.py`

  The route allow-list, the template loop, the CSS grid, the workflow helper and the formatter
  read the candidate list and stay unchanged. **Do not build any new source, field or enum value
  in this task.**

### Workflow (`story_generation.py`)

- `FinalStoryDecision` gains `media_choice: MediaSelection | None = None`. Give it a wrap
  serializer (the same pattern as `MediaManifest`) that drops `media_choice` when it is `None`.
  Then signal payloads from the CLI, from old forms and from rejects stay byte-identical to the
  ones sent before 065b.
- Add `STORY_MEDIA_CHOICE_PATCH = "story-media-choice-v1"`. In `_review`, after the decision and
  only when it is an approval **and** `package.media` offers 2 or more candidates, run this:
  ```
  if workflow.patched(STORY_MEDIA_CHOICE_PATCH):
      package = package.model_copy(update={"media": with_media_choice(package.media, decision.media_choice)})
      self._content_hash = story_package_hash(package)
  ```
  Then pass that `package` to `_finish`. Rejections, text-only stories, stories without an
  illustration and stories from before 065 never call `patched`, so they keep their hash and their
  command sequence.
- **Stale protection does not change.** `_record` still compares `decision.content_hash` with the
  review-time `self._content_hash`, so the human approves what they saw. The new hash exists only
  after approval and is the one that Ready, the publish button and the publication key use. A
  later change to the media still needs re-review, as before.
- This code stays pure: no I/O, clock or randomness.

### Service and route

- `DecideStoryRequest` gains `media_choice: MediaSelection | None = None`. `StoryDetailService.decide`
  puts it into `FinalStoryDecision` only for an approval. A reject always sends `None`. The stale
  check does not change.
- `POST /stories/{detail_id}/decide` gains the form field
  `media_choice: Annotated[str, Form()] = ""`.
  - The allowed values are `""` (meaning `None`) and every `MediaSelection` value. Derive the
    allow-list from the enum, not from string literals.
  - Any other value re-renders with HTTP 400 and the existing `INVALID_DECISION_ERROR`. It never
    reaches the service.
  - The route stays thin: parse, call one service, redirect.
- The CLI gets no new flag. A CLI approval sends no choice, which means `card`.

### Publishing (`publishing/formatter.py`)

`_choose_artifact` works like this:

- If `manifest.selected` is set and is not `card`, look the selection up in
  `manifest.candidates()` and return that candidate's still `artifact`, even when the destination
  supports GIF.
- Otherwise, including `selected` `None` (stories approved before 065b) and `card`, keep today's
  behaviour exactly.

The capability, size and alt-text checks and the activity's hash re-check do not change. Bluesky
takes PNG up to 1,000,000 bytes, and the alt text comes from the artifact.

## UI design (frontend-design skill applied)

**Design plan.**

- **Colour:** only the shipped tokens, with no new colours:
  - light: ground `#e3ecdf`, surface `#ffffff`, ink/border `#15172b`, muted `#4a4f5e`, focus
    `#2448d8`, highlight `#ffc93c`
  - dark: surface `#262a44`, border `#e3ecdf`, muted `#b4bacb`, focus `#ffc93c`
- **Type:** the existing `--font-display` for the legend and tile names, and `--font-body` for
  hints. There is no new face, no all-caps and no eyebrow labels.
- **Layout:** one large lead tile (the first candidate) and smaller tiles stacked beside it, with
  unequal weight in a 2fr/1fr grid. It stacks to one column when the reading column is narrow.
- **The one bold move:** the yellow hard shadow means "this goes in the post". It moves to
  whichever tile is checked, through `:has(input:checked)`. The app already uses the yellow
  `--shadow-chart` to mean "this is the artefact", so the pick reuses that meaning.

**Check against the brief and the defaults.** The generic version would be two identical rounded
cards with the same soft grey shadow, or a segmented toggle above one image. We rejected both:

- The tiles differ in size.
- Only the picked tile has a shadow.
- The shadow is a hard offset in the brand yellow, not a blur.
- The control is a real radio inside a real label, so the state is visible with no CSS `:has`,
  with no colour and with no JavaScript.

One deliberate change from the 065 sketch: the 2fr/1fr switch keys off the **width of the
reading column** (a CSS container query), not the viewport. At 48rem and wider the rail takes
17rem, and a viewport breakpoint would squeeze the card tile to about 140px.

**Layout.** In review, when `candidates()` has 2 or more entries, the picker replaces the single
`figure.story-media` at the top of `.story-detail__reading`:

```
reading column >= 36rem                         reading column < 36rem (and at 360px)
+--------------------------------+---------+    +----------------------+
| (o) Illustrated card           | ( ) Sto-|    | (o) Illustrated card |
| [ 1200x675 scene ............ ]| ry card |    | [ scene ........... ]|
| hint                           | [card]  |    | hint                 |
| Alt text: ...                  | hint    |    | Alt text: ...        |
+--------------------------------+ alt     |    +----------------------+
  checked tile: 3px border,      +---------+    | ( ) Story card       |
  6px 6px 0 0 #ffc93c shadow                    | [ card ]  hint  alt  |
                                                +----------------------+
```

**Markup** (`partials/story_detail_body.html`, loops and ifs only):

```html
<div class="pick">
  <fieldset class="pick__set">
    <legend class="pick__legend">{{ view.pick.legend }}</legend>
    <div class="pick__tiles">
      {% for tile in view.pick.tiles %}
      <label class="pick__tile pick__tile--{{ tile.value }}">
        <span class="pick__head">
          <input type="radio" name="media_choice" value="{{ tile.value }}" form="{{ view.pick.form_id }}"{% if tile.checked %} checked{% endif %}>
          <span class="pick__name">{{ tile.name }}</span>
        </span>
        <picture>{% if tile.animated_src %}<source media="{{ view.pick.motion_media }}" srcset="{{ tile.animated_src }}" type="image/gif">{% endif %}<img src="{{ tile.src }}" width="{{ tile.width }}" height="{{ tile.height }}" alt="{{ tile.alt_text }}" decoding="async"></picture>
        <span class="pick__hint">{{ tile.hint }}</span>
        <span class="pick__alt">{{ view.pick.alt_label }} {{ tile.alt_text }}</span>
      </label>
      {% endfor %}
    </div>
  </fieldset>
</div>
```

- The approve `<form>` in the rail gets `id="story-approve"`, and the reject form gets no id.
  The radios use the HTML `form` attribute, so they are submitted with Approve and never with
  Reject, with no JavaScript. Nothing in this template uses `hx-boost`, and the review state does
  not poll, so the checked radio is never swapped out.
- Under the Approve button the rail shows one more line,
  `<p class="meta">{{ view.pick_rail_note }}</p>`, and only when the picker is shown.
- The card tile keeps the GIF `<source>` and the reduced-motion poster, exactly like today's
  figure.
- With fewer than 2 candidates, `view.pick` is `None` and the existing figure renders byte for
  byte as today.

**CSS (`app.css`, plain CSS, no new colours):**

- Add the token `--shadow-picked: 6px 6px 0 0 var(--color-highlight);` in `:root`. Both themes
  use it.
- `.pick` sets `container: pick / inline-size; margin: 0 0 var(--space-5);`.
- `.pick__set` sets `border: 0; margin: 0; padding: 0; min-width: 0;`.
- `.pick__legend` sets `font-family: var(--font-display); font-size: var(--text-lg); font-weight:
  800; line-height: 1.2; padding: 0; margin-bottom: var(--space-3);`.
- `.pick__tiles` sets `display: grid; grid-template-columns: minmax(0, 1fr); gap: var(--space-4);
  align-items: start;`.
- `@container pick (min-width: 36rem)` does two things:
  - `.pick__tiles` sets `grid-template-columns: minmax(0, 2fr) minmax(0, 1fr);`.
  - `.pick__tile:first-child` sets `grid-column: 1; grid-row: span 2;`, and
    `.pick__tile:not(:first-child)` sets `grid-column: 2;`. A third candidate later stacks in
    column 2 with no rewrite.
- `.pick__tile` sets:
  - `display: flex; flex-direction: column; gap: var(--space-2);`
  - `background: var(--color-surface); color: var(--color-text);`
  - `border: var(--border-thin) solid var(--color-border); border-radius: var(--radius-sm);`
  - `box-shadow: var(--shadow-none); padding: 0 0 var(--space-3); cursor: pointer; min-width: 0;`
- `.pick__tile picture, .pick__tile img` set `display: block; width: 100%; height: auto;`.
  `.pick__tile picture` also sets `border-top` and `border-bottom` to `var(--border-thin) solid
  var(--color-border)`.
- `.pick__tile:has(input:checked)` sets `border-width: var(--border-width); box-shadow:
  var(--shadow-picked);`.
- `.pick__tile:has(input:focus-visible)` sets `outline: var(--border-width) solid
  var(--color-focus); outline-offset: 3px;`.
- `.pick__head` sets `display: flex; align-items: center; gap: var(--space-2); padding:
  var(--space-3) var(--space-3) 0;`.
- `.pick__head input` sets `width: 1.25rem; height: 1.25rem; margin: 0; flex: none; accent-color:
  var(--color-focus);`.
- `.pick__name` sets `font-family: var(--font-display); font-size: var(--text-base); font-weight:
  800;`.
- `.pick__hint` and `.pick__alt` set `display: block; padding: 0 var(--space-3); font-size:
  var(--text-sm); line-height: 1.5; max-width: 62ch;`.
- `.pick__alt` also sets `color: var(--color-muted); overflow-wrap: anywhere;`.
- Add no transitions or animation. The shadow jump is the feedback.
- Contrast holds with the existing tokens:
  - ink on white is about 17:1, muted on white about 8:1, and dark muted on `#262a44` about 7:1
  - the radio and the border are at least 3:1 against the surface in both themes
  - the shadow is decoration only, because the radio carries the state

**Copy (constants in `view_models.py`, sentence case, as approved in 065):**

| Constant | Text |
| --- | --- |
| `PICK_LEGEND` | `Picture for the post` |
| Illustration tile name | `Illustrated card` |
| Illustration hint, matched | `Headline with a drawing from our free illustration set, picked to match the story.` |
| Illustration hint, generic | `Headline on a pattern made from this story. No drawing matched the topic.` |
| Card tile name | `Story card` |
| Card hint | `Headline and key fact drawn by this app.` If the card is animated, append the existing animated kind text. |
| `PICK_RAIL_NOTE` | `Approve sends the picture you picked with the story.` |
| Ready kind text, illustration | `Illustrated card. This picture goes in the post.` |
| Ready kind text, card with `selected` set | the existing card kind text plus ` This picture goes in the post.` |

Tile copy is a dict keyed by `MediaSelection`, so a new candidate kind adds one entry.

**View models (`web/view_models.py`):**

- `PickTileView`: `value`, `name`, `hint`, `src`, `animated_src`, `width`, `height`, `alt_text`,
  `checked`.
- `StoryPickView`: `legend`, `form_id` (`"story-approve"`), `alt_label`, `motion_media`,
  `tiles: list[PickTileView]`.
- `build_story_pick_view(detail_id, manifest) -> StoryPickView | None` builds one tile per
  `manifest.candidates()` entry, with `checked = candidate.preselected`. It returns `None` when
  there are fewer than 2 candidates.
- `StoryDetailView` gains `pick: StoryPickView | None` and `pick_rail_note: str`. They are set
  only in the review state. When `pick` is set, `media` is `None` there.
- In the Ready state, `build_story_media_view` shows only the selected picture:
  - `selected == illustration`: the illustration still, its alt text and the illustration
    kind text, with no GIF source.
  - `selected == card`: today's card figure plus the "goes in the post" kind text.
  - `selected is None`: exactly today's figure.

**Other states, unchanged from 065a:**

- `failed`: the card figure plus the failed note, with no picker.
- `disabled` or `None`, and old stories: render exactly as today.

## Acceptance criteria

- [ ] In review, a story with an illustration shows a picker with one tile per
      `manifest.candidates()` entry, inside a `<fieldset>`. Every radio has `name="media_choice"`
      and `form="story-approve"`. `matched` preselects the illustrated card. `generic` preselects
      the story card. `failed`, `disabled` and pre-065 stories show today's single figure (plus
      the failed note for `failed`), with no picker.
- [ ] Approving with `illustration`, `card` or no choice gives a Ready story with
      `selected = illustration`, `card`, `card` respectively. Its `content_hash` is
      `story_package_hash` of the package with `selected` set. A stale `content_hash` is still
      refused, and a story without an illustration keeps its review-time hash.
- [ ] The Bluesky formatter uploads the illustrated still with its alt text when
      `selected = illustration`, even when the destination supports GIF. `card` and `None`
      behave exactly as before 065b.
- [ ] The route accepts `media_choice` only from the `MediaSelection` values or empty. Any other
      value gives 400 with `INVALID_DECISION_ERROR` and sends no signal.
- [ ] All existing histories replay, including a pre-065b history of an illustrated story
      approved on 065a code, which is recorded before any code change. A new
      `story-media-choice-v1` history also replays. The two pinned `story_package_hash`
      literals are unchanged, and a `FinalStoryDecision` with no choice serialises without a
      `media_choice` key.
- [ ] The picker works with no JavaScript, has no horizontal scroll at 360px, and stacks in one
      column when the reading column is narrower than 36rem. The README, `tech.md` and
      `docs/runbooks/publishing.md` (including the manual visual checklist) describe the picker.
      `just lint` and `just test` pass with no network.

## Files to touch

- `src/ai_trend_pipeline/media/models.py` (`MediaCandidate`, `MediaManifest.candidates()`,
  `with_media_choice`)
- `src/ai_trend_pipeline/story_generation.py` (`FinalStoryDecision.media_choice` plus its
  serializer, `STORY_MEDIA_CHOICE_PATCH`, apply the choice in `_review`)
- `src/ai_trend_pipeline/services/stories.py` (`DecideStoryRequest.media_choice`, pass it on
  approval)
- `src/ai_trend_pipeline/web/routes/stories.py` (the `media_choice` form field and allow-list)
- `src/ai_trend_pipeline/publishing/formatter.py` (`_choose_artifact`)
- `src/ai_trend_pipeline/web/view_models.py` (pick views, copy, the Ready selected picture)
- `src/ai_trend_pipeline/web/templates/partials/story_detail_body.html`
- `src/ai_trend_pipeline/web/static/app.css`
- `tests/fixtures/histories/story_generation_v2_illustrated_ready.json` (new). Record it from
  the `main` code at `036140d` **before any source change**, using the same recording approach
  as `story_generation_v2_media_ready.json`: an illustrated (matched) manifest, approved with
  no choice. Commit it first, on its own.
- `tests/fixtures/histories/story_generation_v3_media_choice_ready.json` (new, recorded with
  the new code, approved with `illustration`)
- Tests listed below (existing files extended, no new test modules needed)
- `README.md`, `docs/architecture/tech.md`, `docs/runbooks/publishing.md`
- `docs/mvp-plan.md` (row 065b, already added by the planner)

## Tests to write

- `tests/test_media_models.py`: these cases cover `candidates()` and `with_media_choice`.
  - Ordering: the illustration comes first and the card last. `matched` preselects the
    illustration, and `generic` preselects the card.
  - `failed`, `disabled` and `None` give only the card, and text-only gives `()`. Exactly one
    candidate is preselected each time.
  - The card candidate carries the GIF as `animated` for the `gif` outcome.
  - `with_media_choice`: a choice of `illustration` sets `selected` to `illustration`. A choice of
    `card`, or `None`, sets it to `card`.
  - `with_media_choice` with a single candidate returns the manifest unchanged. The two pinned
    hash literals still match.
- `tests/test_story_generation_media.py` (time-skipping): approving with `illustration`, `card`
  or `None` sets `selected` to `illustration`, `card` and `card`.
  - The final `content_hash` equals `story_package_hash` of the final package and differs from
    the review hash.
  - A story without an illustration keeps its review hash, and a reject leaves `selected`
    `None`.
  - A stale `content_hash` is still ignored.
- `tests/test_story_generation_media.py`: `FinalStoryDecision()` dumps with no `media_choice`
  key, and `FinalStoryDecision(media_choice="illustration")` round-trips.
- `tests/test_story_generation_replay.py`: `story_generation_v1_ready.json`,
  `story_generation_v2_media_ready.json`, `story_generation_v2_illustrated_ready.json` and
  `story_generation_v3_media_choice_ready.json` all replay with the new code.
- `tests/test_publishing_formatter.py`: `selected=illustration` uploads the illustration
  artifact and alt text even with a GIF-capable destination. `card` and `None` keep the current
  choice, GIF included.
- `tests/test_story_detail_service.py` (or the existing story-decide service test): a
  `media_choice` on approve reaches the gateway's `FinalStoryDecision`, and a reject sends
  `None`.
- `tests/test_web_story_media.py`, route tests:
  - posting `media_choice=illustration`, `card` or empty passes the right value to the fake
    service
  - `media_choice=photo` or `<script>` returns 400 with `INVALID_DECISION_ERROR`, and the
    service is not called
- `tests/test_story_media_view.py`: these are view-model and markup tests.
  - The picker renders a `fieldset` with a `legend`, and one `label.pick__tile` per candidate.
    Every radio has `form="story-approve"`, and the approve form has `id="story-approve"`.
  - `matched` checks the illustration, and `generic` checks the card.
  - A 3-candidate manifest stub renders 3 tiles with no template change. Build it by
    monkeypatching `candidates()`, which proves the loop is not a hard-coded pair.
  - `failed` renders the figure plus the note, and `disabled`/old stories render the old markup
    unchanged.
  - Ready with `illustration` shows only the illustration and its kind text. Ready with `None`
    renders today's markup.
  - The rail note shows only with the picker.
- `tests/test_design_tokens.py`: `app.css` defines `--shadow-picked` from `--color-highlight`.
  `.pick__tile:has(input:checked)` uses it, and `.pick` declares `container` with
  `inline-size`. The 2fr/1fr grid sits only inside `@container pick (min-width: 36rem)`.
  `.pick__tile img` has `width: 100%` and `height: auto`, and `.pick__tile` has `min-width: 0`.
  These stand in for screenshots (see below).

## Visual check (no screenshots possible)

Headless Chromium cannot start in the build environment (the system library `libnspr4` is
missing), so the builder cannot take screenshots. The honest fallback has two parts:

1. The markup and CSS assertions above check the structure that drives the layout.
2. The builder adds these items to the existing "Visual check (manual)" list in
   `docs/runbooks/publishing.md`. The human runs them in a real browser, in light and dark
   themes, before merge.
   - The picker shows side by side on a wide window and stacks, illustration first, at 360px
     with no horizontal scroll.
   - The yellow shadow moves to the tile you click, with no page reload.
   - Keyboard: Tab reaches the radios, the arrow keys switch the pick, and the focus ring wraps
     the whole tile.
   - With CSS `:has` unsupported (or ignored), the radio still shows the pick.
   - Reduced motion shows the card poster in its tile.
   - Approve with each pick, then Ready shows only the picked picture.

The builder and the PR must say plainly that no screenshot was taken.

## Out of scope

- Any new illustration source (Unsplash-style photos, hosted models or anything else), any new
  `MediaSelection` value or manifest field. 065b only makes sure such a source would need no UI
  rewrite.
- Weak-glyph fixes (`glyph-bolt-panel`, `glyph-fruit-bowl`), the doodle/glyph line-weight and
  style mismatch, new or redrawn assets, matching-table or threshold changes, scene changes and
  regenerated goldens. All of these are later tasks.
- **The CC0 dedication of the 12 project-authored glyphs.** It is still unconfirmed by the human.
  `LICENSES.md` records no confirmation, and 065b must not edit `LICENSES.md` or claim one in
  code, docs, the PR or the handoff. It stays an open item for the human.
- A CLI `--media` flag, a re-roll or "pick another drawing" control, and editing the pick after
  approval (that needs re-review, as today).
- Changes to the Bluesky activity, the capabilities, the chart publisher, the media workflow
  (`story_media.py`) or the illustration activity.
- JavaScript, htmx behaviour on the picker, new fonts, new colours, motion.

## Open items for the human (not resolved by this task)

- Confirm or decline the CC0 dedication of the 12 project-original glyphs (carried over from
  065a).
- Run the manual visual checklist, because no screenshots exist.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: Wrote the 065b spec: a manifest-driven `candidates()` list feeds a no-JS radio picker (`form="story-approve"`, `--shadow-picked`, 2fr/1fr via a container query so it stacks in narrow columns and at 360px), `media_choice` goes route -> service -> workflow under `story-media-choice-v1` with the hash recomputed after approval, and the formatter uploads the picked still. Frontend-design skill was in context.
Forward-compatible: a new source adds an enum value, a field, a `candidates()` entry and copy, with no UI rewrite; no new source is built. The glyph CC0 dedication stays an open human item; no screenshots are possible (libnspr4), so the runbook gets a manual checklist.
No shell: branch `task/065b-illustration-picker` was NOT created, and edits are uncommitted. Record `story_generation_v2_illustrated_ready.json` on `036140d` before any code change.
Next: the human approves the spec.

**builder**: Recorded `story_generation_v2_illustrated_ready.json` on 065a code first (own commit), then built manifest-driven `candidates()`/`with_media_choice`, `media_choice` through route/service/workflow (`story-media-choice-v1`), formatter, picker view models/template/CSS (container query), docs, v3 history. `just lint` and `just test` pass (1738 tests).
No screenshot was taken: headless Chromium still fails (libnspr4 missing), so the spec's fallback applies (markup/CSS assertions plus the manual checklist in `docs/runbooks/publishing.md`). Layout and both themes are unverified visually.
Glyph CC0 dedication still unconfirmed by the human; `LICENSES.md` untouched. Note the Approve-rail note sits between the Approve and Reject forms; the v2/v3 histories were recorded with a test double child, not the real renderer.
Next: verifier.

**verifier**: `just lint` passed (ruff check, ruff format --check, pyright 0 errors).
`just test` passed: 1738 passed, 2 warnings (Pydantic serializer warnings in test_web_story_detail.py, not failures).
All checks pass.

### Reviewer handoff
Needs fix (1 real problem; the rest of the diff is approved, 1738 tests pass):
1. `src/ai_trend_pipeline/web/view_models.py:1348` and `:1768-1773`: `PACKAGE_NOTE_WITH_MEDIA` ("You approve the text and the story card together.") is also shown when the picker is on screen, and on Ready when the illustration was picked. That is wrong: the human approves the text and the picture they pick. Add a separate note for the picker and for a picked illustration, e.g. "You approve the text and the picture you pick together." (Ready: "...and the picture shown"). Keep the old note for the single-card case, and update `tests/test_web_story_detail.py:803` and the picker tests to match.

**builder (fix loop 1)**: Fixed the misleading package note. Review with the picker now says "You approve the text and the picture you pick together."; Ready with a picked illustration says "You approved the text and the illustrated card together." (the rail does not render `package_note` on Ready, so the Ready kind text "Illustrated card. This picture goes in the post." is what users see). The card-only note is unchanged.
Added one test covering card only, picker shown, illustration picked. `just lint` and full tests pass (1739).

**verifier (fix loop 1 re-check)**: `just lint` passed (ruff check, ruff format --check, pyright 0 errors). `just test` passed: 1739 passed, 2 warnings (Pydantic serializer warnings, not failures), run with ANTHROPIC_* unset.
All checks pass.

**reviewer (re-review)**: Approved. Package note fix is accurate (card only, picker, picked); the Ready page shows the kind text, which is correct.
Checked: 3-candidate picker test renders 3 tiles through the template; pinned hashes 108f57c1/855c28a8 pass and are unchanged from main; all replay histories pass (v1, v2 media, v2 illustrated, v3 media choice, story_media v1/v2). `just lint` clean.

**scribe**: README picker section and tech.md and the runbook checklist match the code. Reworded the README glyph line so it no longer reads as a confirmed CC0 dedication; no other doc edits. Open items for the human: CC0 glyph dedication (unconfirmed), no screenshots, deferred style/weak-glyph work, and `just deploy`.
Branch pushed; PR opened against main (link below).
