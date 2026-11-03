# 070: Reviewer gallery for AI-drawn images, and docs

Status: approved

## Goal

In review, `/stories/{id}` shows the story's AI-drawn pictures as a view-only gallery with visible
alt text and a "Drawn by an AI model" note. A failed or capped drawing shows a one-line note. Then
document the feature: how to enable it, what it costs, the cap and the kill switch.

## Context

- This task builds on 069: `MediaManifest.review_gallery()`, `drawn_status`, and `artifact()`
  serving drawn ids through the existing manifest-checked media route at
  `/stories/{id}/media/{artifact_id}`.
- **View-only, decided.** Drawings are not radio tiles, are not in `candidates()`, and do not
  change `selected`, `media_choice`, the Approve form, the approved hash logic, the Ready view or
  publishing.
- **Temporal workflow code:** none.
- The frontend-design skill was applied. The design plan is below.

## UI design

### Design plan

- **Colour:** shipped tokens only, with no new hex values.
  - light: tray `--color-grid` `#d2dfcc`, tile `--color-surface` `#ffffff`, ink/border
    `#15172b`, muted `#4a4f5e`
  - dark: tray `#23273f`, tile `#262a44`, border `#e3ecdf`, muted `#b4bacb`
- **Type:** `--font-display` at weight 800 for the heading and the role labels, and
  `--font-body` for the note and the alt text. Use sentence case, with no all-caps and no eyebrow
  label.
- **Layout:** one tray at the end of the story text, after caveats and before the post preview.
  Inside the tray:
  - the heading
  - one note line
  - a list of tiles, where the main-scene tile spans the full tray and the others sit two across
  - one column when the tray is narrower than 30rem, which covers 360px
- **The one bold move:** the drawings sit on a grid-green tray, the colour of the app's own
  graph-paper ground. They are clearly a separate sketch board for looking at. Nothing in the tray
  has a shadow, because the yellow hard shadow means "this goes in the post", and drawings never
  do.

### Check against the brief and the generic defaults

The default answer would be identical rounded cards with soft shadows, or a carousel. We rejected
both:

- The tiles have unequal weight (hero first, full width).
- There are no shadows and no motion.
- There are no controls.

We also rejected a dashed frame, because `.story-media--none` already uses dashed to mean "no
picture", and reusing it would muddle that meaning. The note says in plain words that the drawings
are not posted, so the reviewer does not hunt for a picker.

### Layout

```
tray >= 30rem                                  tray < 30rem (360px)
+------------------------------------------+   +----------------------+
| Drawings for this story                  |   | Drawings for this... |
| Drawn by an AI model. For review only... |   | Drawn by an AI ...   |
| +--------------------------------------+ |   | +------------------+ |
| | [ hero 4:3 ......................... ]| |   | | [ hero ]         | |
| | Main scene                           | |   | | Main scene       | |
| | Alt text: AI drawing (main scene)... | |   | | Alt text: ...    | |
| +--------------------------------------+ |   | +------------------+ |
| +-----------------+ +------------------+ |   | | [ concept ]      | |
| | [ concept ]     | | [ detail ]       | |   | | ...              | |
| | Idea as picture | | Detail           | |   | +------------------+ |
| | Alt text: ...   | | Alt text: ...    | |   +----------------------+
| +-----------------+ +------------------+ |
+------------------------------------------+
```

### Markup

This goes in `partials/story_detail_body.html`, using loops and ifs only. Insert it inside
`.story-detail__reading`, after the caveats section and before `{% if view.post_proof %}`:

```html
{% if view.drawn %}
<section class="drawn" aria-labelledby="drawn-heading">
  <h2 id="drawn-heading" class="drawn__heading">{{ view.drawn.heading }}</h2>
  <p class="drawn__note">{{ view.drawn.note }}</p>
  <ul class="drawn__grid" role="list">
    {% for tile in view.drawn.tiles %}
    <li class="drawn__tile drawn__tile--{{ tile.role }}">
      <figure>
        <img src="{{ tile.src }}" width="{{ tile.width }}" height="{{ tile.height }}" alt="{{ tile.alt_text }}" loading="lazy" decoding="async">
        <figcaption>
          <span class="drawn__role">{{ tile.role_label }}</span>
          <span class="drawn__alt">{{ view.drawn.alt_label }} {{ tile.alt_text }}</span>
        </figcaption>
      </figure>
    </li>
    {% endfor %}
  </ul>
</section>
{% elif view.drawn_note %}
<p class="meta drawn-missing">{{ view.drawn_note }}</p>
{% endif %}
```

Nothing here is inside the approve `<form>`, and no `form=` attribute is used.

### CSS (`app.css`, plain CSS, no new colours)

- `.drawn` sets:
  - `container: drawn / inline-size;`
  - `margin: var(--space-6) 0 0; padding: var(--space-4);`
  - `background: var(--color-grid); color: var(--color-text);`
  - `border: var(--border-thin) solid var(--color-border); border-radius: var(--radius-md);`
  - `box-shadow: var(--shadow-none);`
- `.drawn__heading` sets `font-family: var(--font-display); font-size: var(--text-lg); font-weight:
  800; line-height: 1.2; margin: 0 0 var(--space-2);`.
- `.drawn__note` sets `margin: 0 0 var(--space-4); font-size: var(--text-sm); line-height: 1.5;
  color: var(--color-muted); max-width: 62ch;`.
- `.drawn__grid` sets `list-style: none; margin: 0; padding: 0; display: grid;
  grid-template-columns: minmax(0, 1fr); gap: var(--space-4);`.
- `@container drawn (min-width: 30rem)` sets two rules:
  - `.drawn__grid` gets `grid-template-columns: repeat(2, minmax(0, 1fr));`
  - `.drawn__tile:first-child` gets `grid-column: 1 / -1;`
- `.drawn__tile` sets `min-width: 0; background: var(--color-surface); border: var(--border-thin)
  solid var(--color-border); border-radius: var(--radius-sm); box-shadow: var(--shadow-none);`.
- `.drawn__tile figure` sets `margin: 0;`.
- `.drawn__tile img` sets `display: block; width: 100%; height: auto; border-bottom:
  var(--border-thin) solid var(--color-border);`.
- `.drawn__tile figcaption` sets `display: flex; flex-direction: column; gap: var(--space-1);
  padding: var(--space-2) var(--space-3) var(--space-3);`.
- `.drawn__role` sets `font-family: var(--font-display); font-weight: 800; font-size:
  var(--text-sm);`.
- `.drawn__alt` sets `font-size: var(--text-sm); line-height: 1.5; color: var(--color-muted);
  overflow-wrap: anywhere;`.
- `.drawn-missing` sets `margin: var(--space-5) 0 0;`.
- Add no transitions or animation, and do not use `:hover` lift on tiles.
- Contrast:
  - ink `#15172b` on tray `#d2dfcc` is about 13:1
  - muted `#4a4f5e` on `#d2dfcc` is about 6:1
  - dark muted `#b4bacb` on `#23273f` is about 8:1
  - borders reach at least 3:1 against both the tray and the tile in both themes

### Copy (constants in `view_models.py`, sentence case)

| Constant | Text |
| --- | --- |
| `DRAWN_HEADING` | `Drawings for this story` |
| `DRAWN_NOTE` | `Drawn by an AI model. For review only: these drawings are not posted.` |
| `DRAWN_ROLE_LABELS[hero]` | `Main scene` |
| `DRAWN_ROLE_LABELS[concept]` | `The idea as a picture` |
| `DRAWN_ROLE_LABELS[detail]` | `Supporting detail` |
| `DRAWN_ROLE_LABELS[setting]` | `Setting` |
| `DRAWN_FAILED_NOTE` | `AI drawing failed for this story. The pictures above are unaffected.` |
| `DRAWN_CAPPED_NOTE` | `No AI drawings for this story: today's drawing limit was reached.` |

The alt label reuses the existing `ALT_LABEL` (`Alt text:`).

### View models (`web/view_models.py`)

- `DrawnTileView` has these fields: `role`, `role_label`, `src`, `width`, `height` and
  `alt_text`.
- `DrawnGalleryView` has these fields: `heading`, `note`, `alt_label` and
  `tiles: list[DrawnTileView]`.
- `build_drawn_gallery_view(detail_id, manifest) -> DrawnGalleryView | None` builds one tile per
  `manifest.review_gallery()` entry. The `src` is
  `/stories/{quote(detail_id)}/media/{quote(artifact_id)}`, the same as the picker. It returns
  `None` for an empty gallery or a `None` manifest.
- `drawn_note_for(manifest) -> str` returns `DRAWN_FAILED_NOTE` for `failed`, `DRAWN_CAPPED_NOTE`
  for `capped`, and `""` otherwise.
- `StoryDetailView` gains `drawn: DrawnGalleryView | None = None` and `drawn_note: str = ""`.
  Set them **only in the review state**. Ready, closed and all other states leave the defaults, so
  their markup does not change.

## Acceptance criteria

- [ ] In review, a manifest with `drawn_status=drawn` renders one `li.drawn__tile` per drawing, in
  role order. Each tile has an `<img>` with width, height and the fixed alt text, its role label,
  and the visible alt text. The tray has the heading and the "Drawn by an AI model" note, and it
  contains no `input`, `form` or `form=` attribute.
- [ ] `failed` and `capped` render only the matching one-line note. `disabled`, a missing drawn
  status and stories from before this task render byte-identical markup to before. Ready shows no
  gallery.
- [ ] The picker, Approve and Reject forms, `package_note`, the Ready view and the publishing
  formatter output are unchanged for a manifest with drawings. The existing picker and publishing
  tests pass untouched.
- [ ] `GET /stories/{id}/media/{drawn_artifact_id}` serves the PNG with `image/png` through the
  existing route and manifest check. An unknown id still returns 404.
- [ ] The CSS rules above exist. The tray works with no JavaScript and has no horizontal scroll at
  360px (asserted through the CSS rules below). A manual visual checklist is added.
- [ ] README, `docs/architecture/tech.md` and `docs/runbooks/publishing.md` describe the feature:
  the settings, that it is off by default, the cost per story, the daily cap, the kill switch, the
  idempotency pointer, that there is no redraw, and that drawings are never posted. `just lint` and
  `just test` pass.

## Files to touch

- `src/ai_trend_pipeline/web/view_models.py`
- `src/ai_trend_pipeline/web/templates/partials/story_detail_body.html`
- `src/ai_trend_pipeline/web/static/app.css`
- `tests/test_story_media_view.py`
- `tests/test_web_story_media.py`
- `tests/test_design_tokens.py`
- `README.md`. Add a short "AI-drawn images" paragraph covering what it does, how to enable it and
  that drawings are never posted.
- `docs/architecture/tech.md`. Update three places:
  - the media section (the `draw_story_images` activity, `story-drawn-v1`, the pointer, the
    sanitise-then-rasterise rule, and no SVG in history)
  - the Worker section
  - the "4. Settings" table: `DRAWN_IMAGES_ENABLED`, `DRAWN_IMAGES_MODEL`, `DRAWN_IMAGES_COUNT`
    and `DRAWN_IMAGES_DAILY_CAP`
- `docs/runbooks/publishing.md`. Under "Media and providers", add an "AI-drawn images"
  subsection covering:
  - how to enable it and restart the worker
  - a rough cost: one call per story, and output of about 1,400 tokens per image times the image
    count, so about 4,600 output tokens at the default of 3. Point to the activity's log line for
    the real token counts.
  - the cap, and the `capped` note
  - the kill switch, `DRAWN_IMAGES_ENABLED=false`
  - that pointer files under `MEDIA_DIR/drawn/` prevent repeat charges, and must not be deleted
    to "redraw"
  - that there is no redraw yet

  Add the gallery items below to "Visual check (manual)".

## Tests to write

- `test_story_media_view.py`, for view models and markup:
  - drawn with 3 images gives 3 tiles in role order, with the right `src`, role labels and alt
    text
  - a 4-image manifest renders 4 tiles, which shows the loop is not hard-coded
  - the tray contains no `input`, `form` or `form=`
  - `failed` and `capped` show the note only
  - `disabled`, `None` and an old manifest give markup identical to a render without drawn fields
  - Ready with drawings shows no tray
  - a review page with drawings plus an illustration still shows the picker with the same checked
    radio
- `test_web_story_media.py`: the media route serves a drawn artifact id as `image/png`, and an
  unknown id returns 404.
- `test_design_tokens.py` covers these CSS rules, which stand in for screenshots:
  - `.drawn` declares `container` with `inline-size` and uses `--color-grid`
  - the two-column grid and the full-width first tile appear only inside
    `@container drawn (min-width: 30rem)`
  - `.drawn__tile img` has `width: 100%` and `height: auto`
  - `.drawn__tile` has `min-width: 0`
  - no `.drawn` rule uses `--shadow-picked` or `--shadow-chart`

## Visual check (manual, added to the runbook)

Headless Chromium may be unavailable (libnspr4). If so, the builder says plainly that no
screenshot was taken. The human checks these in light and dark themes:

- [ ] At 360px the tray is one column with no horizontal scroll, and the hero comes first.
- [ ] On a wide window the hero spans the full width, with two tiles below.
- [ ] The tray reads as separate from the yellow-shadowed picker, and nothing in it looks
  clickable.
- [ ] A failed or capped story shows the one-line note only.

## Out of scope

- Making drawings pickable, or any change to `candidates()`, `selected`, `media_choice`, the
  route's form handling, the approved-hash logic, the formatter, `PublicationMedia`,
  `bluesky_story.py` or Bluesky in general.
- A redraw or regenerate button, a lightbox, zoom, JavaScript, htmx behaviour, motion, new fonts
  and new colours.
- Showing drawings on Ready, `/stories` list rows or the candidates pages.
- Any change to the activity, the workflow or the manifest from 067-069.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: wrote spec
