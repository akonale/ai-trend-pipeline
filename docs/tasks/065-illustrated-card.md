# 065: Illustrated card (free, offline still image)

Status: planned

## Goal

Give each story a second still picture, the **illustrated card**. It combines the headline, a
vendored free-licence cartoon illustration picked by keyword rules, and a procedural background
seeded from the story's content hash. Everything renders offline and deterministically, with no
API, key, network or cost. Review shows it next to the story card and the human picks which
picture goes out with Approve. The story card stays the fallback that always works, so a disabled,
weak-match or failed illustration never blocks review or publishing.

## Decisions

### A + D, not a hosted model

- **A, illustration:** a small curated pack of CC0 / public-domain SVG illustrations, vendored in
  the repo with a machine-readable licence catalogue. Code picks one with a readable keyword
  table. It is recoloured to the site tokens and composed with the headline.
- **D, background:** a procedural pattern plus flat shapes drawn under the illustration. Every
  parameter comes from the SHA-256 of the story content hash, so two stories with the same topic
  still look different, and every story with no topic match still gets an on-brand picture.
- The **illustration provider slot stays** (`IllustrationProvider` protocol plus registry keyed
  by `IllustrationProviderName`). A hosted image model can be added later as one more provider,
  with no workflow, manifest or UI change.
- **No gradients and no per-pixel noise.** The design direction calls for flat saturated colour
  blocks, and noise makes PNG bytes grow fast. "Pattern" here means dot grids, stripes, arcs and
  blocks drawn with integer coordinates.

### Libraries to vendor (starter set)

The builder **verifies each licence on the library's official site** before vendoring and records
the date in the catalogue. Our sources are secondary.

| Source | Use | Licence (to verify) | Verdict |
| --- | --- | --- | --- |
| Open Doodles (Pablo Stanley, opendoodles.com) | people-activity scenes | CC0 1.0 | **use** |
| Open Peeps (Pablo Stanley, openpeeps.com) | only if a doodle is missing for "society/people" | CC0 1.0 | optional |
| Openclipart (openclipart.org) | object motifs (robot, chip, solar panel...) | public domain / "unlimited commercial use" | **use**, flat-style items only |
| unDraw | | forbids redistributing packs; vendoring may count | **do not use** |
| DrawKit | | sources conflict (MIT vs personal use) | **do not use** |
| Storyset | | unverified, attribution terms | **do not use** |

If a licence check fails, or the builder cannot reach the official sites, the builder **stops
and asks the human**. Do not substitute another library. The catalogue's `licence` field is an
enum (`CC0-1.0`, `openclipart-public-domain`) so no other licence can slip in. Hand-drawn CC0
cartoon people carry no real-person likeness, so they are allowed. The "no people" rule from the
old Imagen design does not apply.

Starter set: 15 assets, each covering one topic. The topics follow the pipeline's real inputs,
which are Hacker News tech stories plus the six OWID datasets (CO2, internet, solar, EVs, life
expectancy, GDP).

| Asset id | Preferred source | Shows (catalogue `description`, used in alt text) |
| --- | --- | --- |
| `ai-robot` | Openclipart | a friendly cartoon robot |
| `laptop-code` | Openclipart | a laptop with code brackets on screen |
| `chip` | Openclipart | a computer chip |
| `padlock` | Openclipart | a closed padlock |
| `globe-network` | Openclipart | a globe wrapped in network lines |
| `solar-wind` | Openclipart | a solar panel and a wind turbine |
| `electric-car` | Openclipart | a small electric car at a charger |
| `bike-commute` | Open Doodles (bike) | a person riding a bike |
| `plant-growth` | Open Doodles (plant) | a person tending a large plant |
| `coins` | Openclipart | a stack of coins |
| `healthy-run` | Open Doodles (running / meditating) | a person out for a run |
| `rocket` | Openclipart | a rocket lifting off |
| `microscope` | Openclipart | a microscope |
| `reading` | Open Doodles (reading) | a person reading a book |
| `wheat` | Openclipart | ears of wheat |

What makes an asset acceptable:

- flat colours, at most 8 distinct colours
- no text, logos, brand marks, flags, maps or landmarks
- 64 KB or less after normalisation
- a square-ish `viewBox`, aspect between 0.6 and 1.6

If no acceptable Openclipart item exists for a row, drop the row and its tags, and note it in the
handoff. Do not draw a replacement by hand.

### Files and catalogue

- `src/ai_trend_pipeline/media/illustrations/assets/<id>.svg` holds the normalised, sanitised SVG.
  It lives under `src/` so the wheel and the Docker image ship it, as the fonts already do.
- `src/ai_trend_pipeline/media/illustrations/catalogue.json` is a list of entries, validated by the
  `IllustrationAsset` Pydantic model (frozen, `extra="forbid"`) with these fields:
  - `id`: slug `^[a-z][a-z0-9-]{1,31}$`. `file` is `<id>.svg`.
  - `title`
  - `description`: 3..80 characters, the plain noun phrase in the "Shows" column above.
  - `source`: enum `open-doodles | open-peeps | openclipart`.
  - `source_url`: https, the item page. `author`.
  - `licence`: the enum above. `licence_url`: https.
  - `licence_verified_on` and `retrieved_on`: ISO dates.
  - `sha256`: of the committed file.
  - `modified`: bool. `modification_note` is required when `modified` is true (for example
    "style attributes inlined, metadata removed, colours flattened").
  - `tags`: 3..30 entries, lowercase, unique.
  - `recolour`: a map from every source hex colour in the file to a slot, `ink`, `paper`, `accent`
    or `support`. This map is the "safe recolour regions".
  - `attribution_required: bool` and `attribution_text: str | None`. For CC0 and public-domain
    assets it is `false`. If `true`, the text must appear in `LICENSES.md`.
- `src/ai_trend_pipeline/media/illustrations/LICENSES.md` is the human-readable list (asset, author,
  source URL, licence and link, verified date). It includes the CC0 1.0 legal-code link and the
  Openclipart licence statement exactly as quoted on the day it was checked. No attribution is
  needed in posts for the starter set.
- **Normalisation happens once, when an asset is vendored**, and its output is committed. The
  normalisation steps:
  1. Inline `style` into presentation attributes.
  2. Drop metadata, comments, `<title>`, `<desc>` and editor namespaces.
  3. Flatten colours to `#rrggbb`.
  4. Remove anything the sanitiser rejects.

  This changes the file, so set `modified: true` and say what changed. The builder commits the
  small helper that did it as `tests/vendor_illustrations.py`. It is dev-only, takes local file
  paths and makes no network calls.

### SVG sanitiser (`media/illustrations/sanitize.py`)

It runs in tests over every asset, and again at render time as defence in depth. If any rule
fails, it raises `UnsafeSvg`.

- Raw-text pre-checks reject `<!DOCTYPE`, `<!ENTITY`, `<?xml-stylesheet` and files over 64 KB.
  The file is then parsed with `xml.etree.ElementTree`.
- **Allowed elements:** `svg`, `g`, `defs`, `clipPath`, `path`, `circle`, `ellipse`, `rect`,
  `line`, `polyline`, `polygon`.
  This means `script`, `foreignObject`, `image`, `text`, `tspan`, `style`, `a`, `use`, `font`,
  `filter`, `mask`, `pattern`, gradients and everything else are rejected.
- **Allowed attributes:**
  - structure and geometry: `id`, `xmlns`, `version`, `viewBox`, `width`, `height`, `d`,
    `points`, `transform`, `cx`, `cy`, `r`, `rx`, `ry`, `x`, `y`, `x1`, `y1`, `x2`, `y2`
  - paint: `fill`, `stroke`, `stroke-width`, `stroke-linecap`, `stroke-linejoin`,
    `stroke-miterlimit`, `fill-rule`, `clip-rule`, `opacity`, `fill-opacity`, `stroke-opacity`
  - `clip-path`, whose value must match `^url\(#[A-Za-z0-9_-]+\)$`

  This means `style`, `href`, `xlink:href`, any `on*`, and `url(` pointing anywhere other than
  `#id` are rejected.
- `fill` and `stroke` values must be `none` or a hex colour that is a key of the asset's `recolour`
  map, so an unmapped colour cannot appear. A root `viewBox` is required.

### Rasteriser: `resvg-py`, pinned

Pillow cannot draw SVG. The options:

| Option | System libs | Image size | Determinism | Verdict |
| --- | --- | --- | --- | --- |
| **`resvg-py`** (Rust resvg + tiny-skia, manylinux wheels) | none | a few MB wheel | same version gives the same pixels; no system fonts used, because our SVGs have no text | **chosen** |
| `cairosvg` | needs `libcairo2` via apt in the `Dockerfile` (which today installs only `fonts-liberation`) | +~10-20 MB of OS packages | output follows the OS cairo version, so dev and Docker goldens drift | rejected |
| `svglib` + `reportlab` | none | medium | weak SVG support, poor anti-aliasing | rejected |
| `vl-convert-python` `svg_to_png` (already a dependency, bundles resvg) | none | +0 | pinned by the lock, but tied to the chart library's upgrade cycle | **fallback only**, if `resvg-py` has no cp312 wheel for the deploy architecture |

- Add the dependency with `uv add "resvg-py==<exact current version>"` and pin it with `==`. Also
  add `[tool.uv] no-build-package = ["resvg-py"]` to `pyproject.toml`, so a missing wheel fails
  the lock instead of needing a Rust toolchain in Docker.
- The builder checks the package's real API and wheel tags (cp312, manylinux x86_64 and aarch64)
  and records the version in the handoff.
- Wrap the call in one typed function, `rasterise_svg(svg: str, width: int, height: int) ->
  Image.Image` (RGBA), in `media/illustrations/rasterise.py`. If the library has no type
  information, add a minimal stub at `typings/resvg_py.pyi`, which is Pyright's default stub
  path, rather than using `Any`.
- **How determinism is guaranteed:**
  1. Pinned `resvg-py`, Pillow and fonts in `uv.lock`.
  2. No system fonts, because the SVGs have no text and the headline is drawn by Pillow with the
     vendored Archivo fonts.
  3. Background parameters come from SHA-256 bytes, not `random`.
  4. Golden images are compared with the same tolerance helper as the card goldens (at most 0.1 %
     of pixels may differ, to absorb anti-aliasing differences between CPU architectures).
  5. A same-process test renders twice and requires byte-identical output.

### Topic matching (`media/illustrations/matching.py`, pure, runs in the activity)

- **Normalise:** NFKC, casefold, then split into tokens with `[a-z0-9]+`. Single-word tags match
  whole tokens. Multi-word tags (for example `electric car`, `life expectancy`) match consecutive
  tokens. A tag is counted once per field.
- **Score per asset** = 3 × (distinct tags hit in the headline) + 1 × (distinct tags hit in the
  summary, first 600 characters only).
- **Pick:** the highest score wins. Break ties by more headline hits, then by catalogue order,
  which is the table order below and acts as priority.
- **Threshold:** a score of 3 or more is `matched`. That means at least one headline hit, or three
  summary hits. Anything below 3 is `generic`: background plus a code-drawn abstract motif and
  **no figurative illustration**, so a weak match never shows a wrong-topic drawing.

| Order | Asset | Tags |
| --- | --- | --- |
| 1 | `ai-robot` | ai, artificial intelligence, llm, llms, chatbot, robot, robots, machine learning, neural, gpt, openai, anthropic, claude, gemini, agent, agents |
| 2 | `padlock` | security, privacy, hack, hacked, hackers, breach, vulnerability, exploit, malware, ransomware, encryption, password, passwords |
| 3 | `chip` | chip, chips, semiconductor, gpu, gpus, cpu, processor, silicon, nvidia, hardware |
| 4 | `laptop-code` | software, code, coding, programming, developer, developers, open source, compiler, linux, python, rust, javascript, database, app |
| 5 | `globe-network` | internet, online, broadband, web, network, browser, connectivity, wifi |
| 6 | `solar-wind` | solar, wind, renewable, renewables, energy, electricity, power grid, battery, batteries |
| 7 | `electric-car` | electric car, electric cars, electric vehicle, ev, evs, tesla, charging |
| 8 | `bike-commute` | transport, transit, bike, bikes, cycling, train, trains, rail, commute, traffic |
| 9 | `plant-growth` | climate, co2, carbon, emissions, environment, nature, forest, warming, pollution |
| 10 | `coins` | gdp, economy, economic, income, money, prices, inflation, market, markets, funding, startup, revenue, tax |
| 11 | `healthy-run` | health, life expectancy, longevity, medicine, medical, disease, hospital, vaccine, fitness |
| 12 | `rocket` | space, rocket, nasa, satellite, satellites, orbit, launch, mars, moon |
| 13 | `microscope` | science, research, researchers, scientists, study, lab, physics, biology, chemistry |
| 14 | `reading` | education, school, schools, students, learning, book, books, teachers |
| 15 | `wheat` | food, farming, agriculture, crops, harvest, diet |

The tags in `catalogue.json` must equal this table, and a test checks it. If an asset row is
dropped, its tags are dropped too.

### Scene layout and drawing (`media/illustrations/scene.py`)

The canvas is 1200×675, light theme only (the story card's production default), and the output is
RGB PNG with `optimize=True`.

```
+------------------------------------------------------------------+
| ground #e3ecdf + hash-chosen pattern, full bleed                 |
|  +----------------------------+        ___ hash shapes ___       |
|  | panel #ffffff, 6px ink     |      /                    \      |
|  | border, 12px ink hard      |     |   backdrop shape     |     |
|  | shadow                     |     |   (support colour,   |     |
|  | Headline, Archivo XB,      |     |   12px ink shadow)   |     |
|  | 64 -> 36px, max 5 lines    |     |   + illustration     |     |
|  | -------------- 3px rule    |     |   440x440 max box    |     |
|  |          Topic Studio      |      \____________________/      |
|  +----------------------------+                                  |
+------------------------------------------------------------------+
   x 40..640 (panel)                 x 680..1160 (art area)
```

- **Palette.** Use only existing tokens, read from `CARD_THEMES[LIGHT]` plus three app tokens
  added to a `SCENE_PALETTE` constant. A sync test checks `app.css`:
  - ground `#e3ecdf`, grid `#d2dfcc`, paper `#ffffff`, ink `#15172b`, accent `#2448d8`
  - supports: `#ffc93c` (highlight), `#a8c8ff` (info), `#7ddba3` (success), `#c7b8ff` (published)
- **Recolour slots.** `ink` is `#15172b` and `paper` is `#ffffff`. `accent` is `#2448d8`, used only
  for small details. `support` is one hash-chosen support colour. The backdrop shape takes a
  *different* hash-chosen support colour.
- **Contrast guard (tested with the WCAG formula):** ink against every backdrop and ground colour
  must be at least 3:1, and the headline (ink on paper) at least 4.5:1. Accent is never a backdrop
  fill, because ink on `#2448d8` fails 3:1.
- **Seed and stream.** `seed = sha256(b"illustrated-card-v1|" + story_content_hash)`. Further bytes
  come from `sha256(seed + counter.to_bytes(4, "big"))`. Choices are discrete:
  - pattern family (4): dot grid, 45° stripes, corner quarter-arcs, chunky checker blocks
  - spacing from {24, 32, 40}
  - pattern ink: grid `#d2dfcc`
  - two support colours
  - backdrop shape (3): circle, rounded square (radius 24), tilted square at ±6°
  - 2-3 small accent shapes (dot, ring, bar) placed on a 40 px grid inside the art area and
    clipped to it

  There is no Python `random`.
- **Headline.** Reuse the card's text code:
  - Promote `_wrap`, `_ellipsise`, `_line`, `_clean` and `_supported` in `card.py` to public names
    without changing behaviour. The existing card goldens must stay byte-identical.
  - Use the Archivo ExtraBold sizes 64→36 in steps of 2, max 5 lines, ellipsis at 36 px.
  - The footer is a 3 px ink rule plus the `Topic Studio` wordmark in accent, as on the card. The
    summary and source are **not** drawn, because the card already carries the fact.
- **Invariants** (asserted in a `_check` like the card's):
  - headline and wordmark ink stay inside the panel's content box
  - text never reaches the footer rule
  - the art area never overlaps the panel or its shadow
  - the illustration fits its box, aspect preserved and centred
- **Illustration rendering:**
  1. Sanitise the SVG.
  2. Substitute `recolour` colours. This is string replacement of the exact mapped hex values,
     which works because the sanitiser guarantees no other colours exist.
  3. Rasterise to RGBA at the fitted size.
  4. Alpha-composite the result onto the backdrop.
- **Generic motif** (`generic`, no illustration): a code-drawn ink-outlined 8-point burst in the
  second support colour, centred on the backdrop. It is abstract, not figurative.
- **Bytes.** The output must be within `MAX_STATIC_BYTES` (1,000,000). The goldens test asserts
  each output is at most 600,000 bytes for headroom. If the existing activity check ever fires, the
  result is `failed`, not a crash.

### Alt text (code-built, from approved story text only)

- matched: `"Illustrated card with the headline: {headline}. Drawing: {description}, on a
  patterned background."`
- generic: `"Illustrated card with the headline: {headline}. Abstract shapes on a patterned
  background."`

`{headline}` is the `MediaBrief.headline`, which is already cleaned, and `description` comes from
the catalogue. The result passes `clean_media_text` and is cut to `MAX_ALT_TEXT_CHARS`.

### Models (`media/models.py`)

- `IllustrationStatus(StrEnum)`: `matched`, `generic`, `failed`, `disabled`.
- `MediaSelection(StrEnum)`: `illustration`, `card`.
- `MediaManifest` gains three optional fields, each defaulting to `None`:
  `illustration_artifact: MediaArtifact | None`, `illustration_status: IllustrationStatus | None`,
  and `selected: MediaSelection | None`. Validation rules:
  - `illustration_artifact` must be static PNG or WebP.
  - It is allowed only when `static_artifact` is set, because the card must always exist.
  - `illustration_status in {matched, generic}` if and only if the artifact is set.
  - `selected == illustration` requires an artifact.
  - The illustration's ID must differ from the other artifact IDs.
  - `artifact()` also returns the illustration, so the existing media route serves it with no
    route change.
- **Hash compatibility.** The three keys are omitted from `model_dump` when `None`, using a wrap
  `model_serializer` or an equivalent. This keeps `story_content_hash`, `story_package_hash` and
  the Temporal payloads of pre-065 packages byte-identical. A golden test pins it: the builder
  computes `story_package_hash` of a fixed pre-065 package **on `main` before changing code** and
  pastes the literal into the test.
- Activity I/O:
  - `IllustrateInput`: `story_id`, `story_content_hash` (sha256 pattern), `headline` (the brief
    headline), and `summary` (str, at most 600 characters, matching only, never drawn).
  - `IllustrationResult`: `status`, plus `artifact: MediaArtifact | None`, set only for
    `matched`/`generic`.
- `illustration_artifact_id_for(data: IllustrateInput, renderer_name, renderer_version) -> str`
  returns `"art-v1-" + sha256(canonical_json({"illustration": ..., "renderer": ..., "version":
  ...}))[:16]`. The renderer version includes `SCENE_TEMPLATE_VERSION` and the SHA-256 of
  `catalogue.json`, so changing an asset gives a new ID.

### Provider slot and activity

- In `media/illustration.py`:
  - Replace the unused `MediaBrief`-based protocol with `IllustrationProvider`, which has `name`
    and `version` and the method `illustrate(data: IllustrateInput) -> IllustrationOutput`.
    `IllustrationOutput` is a dataclass of `status` (matched/generic) and `RenderedMedia`.
  - `IllustrationProviderName`: `off`, `illustrated-card`, `fake`.
  - Keep a static registry, like `MEDIA_RENDERERS`.
- `media/illustrations/renderer.py`, `IllustratedCardProvider`:
  - It loads and validates the catalogue once, lazily. It checks each file's SHA-256 against the
    catalogue and sanitises each asset.
  - It matches, draws the scene and encodes the PNG.
  - An unsupported headline glyph or a broken asset raises `MediaContentUnsupported`.
- `media/illustration_activities.py`, `IllustrationActivities(media_root, provider | None)`, with
  the activity `illustrate_story`:
  1. If the provider is `None`, return `disabled`.
  2. Otherwise run the provider in `asyncio.to_thread`.
  3. Validate the type, signature, bytes and dimensions, reusing the checks from `activities.py`.
  4. Write with `write_artifact`, which is content-addressed and atomic.
  5. Return the artifact.

  It is idempotent because the same input gives the same bytes, path and ID, with no pointer
  cache and no cost. `MediaContentUnsupported` maps to a non-retryable `ApplicationError` of type
  `IllustrationInvalid`. Any other exception is retryable, with type `IllustrationFailed`.

### Workflow (`story_media.py`)

- Add `STORY_ILLUSTRATION_PATCH = "story-illustration-v1"`. The illustration activity runs only
  when `workflow.patched(...)` is true **and** the static card succeeded. A text-only outcome
  never calls it.
- Build `IllustrateInput` in workflow code. This is pure: `brief.headline`, `package.summary[:600]`
  and `story_content_hash(package)`.
- When animation is included, start the illustration activity with `workflow.start_activity` at
  the same time as the GIF render, then await both.
  - Timeout: `start_to_close` 30 s.
  - Retry: `maximum_attempts=2`, non-retryable `["IllustrationInvalid"]`.
- If an `ActivityError` occurs (other than cancellation, which re-raises), set
  `illustration_status=failed` with no artifact. The card outcome (`gif`/`static`/`static_fallback`)
  is never changed. `selected` stays `None` until approval.

### Approval choice (`story_generation.py`, services, route)

- `FinalStoryDecision` gains `media_choice: MediaSelection | None = None`.
- After an approve decision, under `workflow.patched("story-media-choice-v1")` and only when the
  manifest has an `illustration_artifact`:
  - Set `selected = illustration` only if the choice is `illustration`. Otherwise set `card`, so
    a missing choice (from the CLI or an old form) means `card`.
  - Recompute `self._content_hash` before `_finish`.
- Packages without an illustration are untouched and keep their hash. The stale check still
  compares the hash the human reviewed with the review-time hash.
- `DecideStoryRequest` gains `media_choice`. The route allow-lists the `media_choice` form field:
  `illustration`, `card`, or empty, where empty means `None`. Anything else re-renders with HTTP
  400 and the existing `INVALID_DECISION_ERROR`.

### Publishing (`publishing/formatter.py`)

`_choose_artifact`: if `selected is illustration` and the artifact is present, return the
illustration still, even when the destination supports GIF. Otherwise keep the current
behaviour. The capability checks are unchanged. Bluesky takes PNG up to 1,000,000 bytes, and the
alt text comes from the artifact.

### Config (`config.py` only)

| Env var | Default | Rule |
| --- | --- | --- |
| `ILLUSTRATION_PROVIDER` | `illustrated-card` | `off`, `illustrated-card` or `fake`. Unknown values raise a config error that lists the valid names. This is the kill switch. |

**Why it is on by default:**

- It matches the precedent of `MEDIA_PROVIDER` defaulting to the real `story-card` renderer.
- It has no cost, key or network.
- A failure only falls back to the card.
- Nothing posts without a human picking it in review.

`worker.py` builds the provider from `Settings` (`off` gives `None`) and registers
`illustrate_story` on the media task queue. Add `ILLUSTRATION_PROVIDER:
${ILLUSTRATION_PROVIDER:-illustrated-card}` to the worker service in `infra/compose.prod.yaml`, so
`.env.prod` can turn it off.

## UI design (frontend-design skill applied)

**Design plan.**

- **Colour:** the shipped neo-brutalist tokens only: ground `#e3ecdf`, ink `#15172b`, cobalt
  `#2448d8`, highlight `#ffc93c`, surface `#ffffff`, plus their dark pairs.
- **Type:** the existing `--font-display` and `--font-body`.
- **Layout:** two tiles of unequal weight.
- **The one bold move:** only the picture that will be posted carries the yellow hard shadow. The
  chart preview already uses `--shadow-chart` to mean "this is the artefact", so here it means
  "this goes in the post". The shadow follows the checked radio (`:has(input:checked)`), so it
  moves when the human changes the pick, and it tells the truth in both the matched and generic
  cases.

**Check against the brief.** The generic default is two identical rounded cards with the same
soft shadow. We rejected it for three reasons:

- The tiles differ in size (2fr/1fr).
- Only the chosen tile is shadowed.
- No new colours, fonts or motion are added.

**Layout.** In review state with `illustration_status in {matched, generic}`, a picker replaces
the single `figure.story-media` at the top of the reading column:

```
desktop (>= 40rem)                              360px (stacked, illustration first)
+--------------------------------+---------+   +----------------------+
| (o) Illustrated card           | ( ) St. |   | (o) Illustrated card |
| [ 1200x675 scene ............ ]| card    |   | [ scene ........... ]|
| hint, alt text                 | [card]  |   | hint, alt text       |
+--------------------------------+---------+   +----------------------+
   ^ checked tile: 3px ink border +              | ( ) Story card       |
     6px 6px 0 0 #ffc93c shadow                  | [ card ]             |
                                                 +----------------------+
```

- **Markup:**
  - A `<fieldset class="pick">` with the `<legend>` "Picture for the post", styled like an h2:
    `--font-display`, `--text-lg`, weight 800.
  - It holds two `<label class="pick__tile pick__tile--illustration|--card">` elements. Each wraps
    `<input type="radio" name="media_choice" value="illustration|card" form="story-approve">`, the
    image, a tile name, a one-line hint and the alt-text line. The card image is a `<picture>`, so
    the existing GIF and reduced-motion source stays.
  - The approve `<form>` in the rail gets `id="story-approve"`, so the radios submit with Approve
    and no JavaScript is needed. The reject form ignores them.
- **Default selection:**
  - `matched`: the illustration is `checked`.
  - `generic`: the **story card** is `checked`, because a weak match falls back to the card.
  - The view model sets `checked` and the template only prints it.
- **Grid:** `display:grid; grid-template-columns: 2fr 1fr; gap: var(--space-4); align-items:
  start`. Below `40rem` it becomes one column. Images use `width:100%; height:auto` with real
  `width`/`height` attributes, and there is no horizontal scroll at 360px.
- **Tile styles:**
  - Base: `background: var(--color-surface)`, `border: var(--border-thin) solid
    var(--color-border)`, `border-radius: var(--radius-sm)`, `box-shadow: var(--shadow-none)`,
    `padding: 0 0 var(--space-3)`.
  - The image gets `border-bottom: var(--border-thin) solid var(--color-border)`.
  - Chosen (`.pick__tile:has(input:checked)`): `border-width: var(--border-width)` and
    `box-shadow: var(--shadow-picked)`. Add the new token `--shadow-picked: 6px 6px 0 0
    var(--color-highlight)` in `:root`. Both themes use it.
  - Focus (`.pick__tile:has(input:focus-visible)`): `outline: var(--border-width) solid
    var(--color-focus); outline-offset: 3px`.
  - The radio stays visible at 1.25rem with `accent-color`. The checked radio plus the border
    change carry the state without `:has` and without colour; the shadow is extra.
- **Tile text:**
  - The name uses `--font-display`, weight 800, `--text-base`, in a flex row with the radio
    (`gap: var(--space-2)`).
  - Hint and alt text use `--text-sm`, line-height 1.5, `max-width: 62ch`. The alt text is in
    `--color-muted`.
  - Inline padding is `var(--space-3)`.
  - Use sentence case and no all-caps. Add no motion.
- **Rail:** under Approve, one `.meta` line: "Approve sends the picture you picked with the story."

**Copy (constants in `view_models.py`):**

| Constant | Text |
| --- | --- |
| Legend | `Picture for the post` |
| Illustration tile name | `Illustrated card` |
| Hint, matched | `Headline with a drawing from our free illustration set, picked to match the story.` |
| Hint, generic | `Headline on a pattern made from this story. No drawing matched the topic.` |
| Card tile name | `Story card` |
| Card tile hint | `Headline and key fact drawn by this app.` (the existing GIF kind text is appended when animated) |
| Rail note | `Approve sends the picture you picked with the story.` |
| Failed note | `The illustrated card could not be made, so the story card is used.` |
| Ready kind text, illustration | `Illustrated card. This picture goes in the post.` |
| Ready kind text, card | Existing card kind text plus ` This picture goes in the post.` |

**Other states:**

- `failed`: show the existing card figure with the failed note as `<p class="story-media__note">`
  (`--text-sm`, weight 700).
- `disabled` or `None` (old stories): render exactly as today.
- Ready: show only the selected picture in the existing `figure.story-media`.

**View models:**

- `StoryPickView` has `legend`, `form_id` and `tiles: list[PickTileView]`. Each `PickTileView`
  has `value`, `name`, `hint`, `src`, `animated_src`, `width`, `height`, `alt_text`, `checked`
  and `modifier`.
- `StoryMediaView` gains `illustration_note`.

## Acceptance criteria

- [ ] For a review story whose headline matches a topic (for example "OpenAI ships a new model"),
      the illustrated card shows the right asset on a hash-seeded background and is preselected,
      with the story card beside it. Approve with either choice gives a Ready story whose
      `content_hash` covers `selected`, and the Bluesky formatter uploads exactly that still with
      its alt text.
- [ ] Nothing below the score threshold ever gets a figurative illustration. It gets the generic
      scene with the story card preselected. `ILLUSTRATION_PROVIDER=off`, or a failed render,
      leaves review and publishing exactly as before 065 (except the fixed failed note).
- [ ] Rendering is offline and deterministic: the same input gives byte-identical PNG bytes, the
      goldens match, and the output is within `MAX_STATIC_BYTES`. Storage is content-addressed and
      atomic, and re-running the activity is safe.
- [ ] Every vendored SVG passes the sanitiser allowlist and has a complete catalogue record whose
      SHA-256 matches the file and whose licence is CC0 or Openclipart public domain, with a
      verified date. `LICENSES.md` lists every asset.
- [ ] Pre-065 `StoryMediaWorkflow` and `StoryGenerationWorkflow` histories replay, and a pre-065
      package's `story_package_hash` equals the pinned golden value. The card goldens are
      unchanged.
- [ ] The review picker works without JavaScript and stacks at 360px. A contact sheet of sample
      outputs is committed for the human to eyeball, and `just lint` and `just test` pass with no
      network.

## Files to touch

- `pyproject.toml` and `uv.lock` (`uv add "resvg-py==<pin>"`, `[tool.uv] no-build-package`)
- `src/ai_trend_pipeline/media/illustrations/__init__.py` (new)
- `src/ai_trend_pipeline/media/illustrations/assets/*.svg` (new, at most 15)
- `src/ai_trend_pipeline/media/illustrations/catalogue.json` (new)
- `src/ai_trend_pipeline/media/illustrations/LICENSES.md` (new)
- `src/ai_trend_pipeline/media/illustrations/catalogue.py` (new: `IllustrationAsset` model and
  loader)
- `src/ai_trend_pipeline/media/illustrations/sanitize.py` (new)
- `src/ai_trend_pipeline/media/illustrations/rasterise.py` (new)
- `src/ai_trend_pipeline/media/illustrations/matching.py` (new)
- `src/ai_trend_pipeline/media/illustrations/scene.py` (new)
- `src/ai_trend_pipeline/media/illustrations/renderer.py` (new)
- `typings/resvg_py.pyi` (new, only if the library ships no types)
- `src/ai_trend_pipeline/media/card.py` (promote the text helpers to public, no behaviour change)
- `src/ai_trend_pipeline/media/models.py`
- `src/ai_trend_pipeline/media/illustration.py`
- `src/ai_trend_pipeline/media/illustration_activities.py` (new)
- `src/ai_trend_pipeline/media/fake.py` (`FakeIllustrationProvider`: a fixed tiny PNG, with
  switchable generic and fail modes)
- `src/ai_trend_pipeline/story_media.py`
- `src/ai_trend_pipeline/story_generation.py`
- `src/ai_trend_pipeline/services/stories.py`
- `src/ai_trend_pipeline/publishing/formatter.py`
- `src/ai_trend_pipeline/config.py`
- `src/ai_trend_pipeline/worker.py`
- `src/ai_trend_pipeline/web/routes/stories.py`
- `src/ai_trend_pipeline/web/view_models.py`
- `src/ai_trend_pipeline/web/templates/partials/story_detail_body.html`
- `src/ai_trend_pipeline/web/static/app.css`
- `infra/compose.prod.yaml`
- `tests/vendor_illustrations.py` (new, dev-only normaliser, no network)
- `tests/regen_illustration_goldens.py` (new; also writes the contact sheet)
- `tests/fixtures/media/illustrated/*.png` (new goldens)
- `tests/fixtures/histories/story_media_v1_gif.json` (new: recorded **on `main` code before any
  change**), `story_media_v2_illustrated.json` and `story_generation_v3_media_choice_ready.json`
  (new)
- `docs/media/illustrated-card-samples.png` (new contact sheet)
- Docs: `README.md`, `docs/architecture/tech.md`, `docs/runbooks/publishing.md`

## Tests to write

- `tests/test_illustration_catalogue.py` checks every SVG in `assets/` against the catalogue:
  - each file has exactly one entry, and each entry has a file
  - every field is complete, the licence is in the allowed enum, and the dates are not in the
    future
  - the SHA-256 matches the file
  - `attribution_required` implies the text is in `LICENSES.md`, and every asset id appears in
    `LICENSES.md`
  - the tags equal the matching table
- `tests/test_illustration_sanitize.py`:
  - all vendored assets pass
  - crafted SVGs are each rejected: `<script>`, `onload=`, `<foreignObject>`, `<image href>`,
    `<text>`, `<style>`, `xlink:href`, `url(http…)`, `<!DOCTYPE>`/`<!ENTITY>`, an unmapped
    colour, a missing `viewBox`, and files over 64 KB
- `tests/test_illustration_matching.py` is table-driven:
  - one headline per asset picks that asset
  - multi-word tags match only as consecutive tokens
  - "rust" in a headline picks `laptop-code`
  - a summary-only single hit is `generic`
  - three summary hits make `matched`
  - ties use headline hits, then catalogue order
  - the result does not depend on case or Unicode form
  - a headline with no tags gives `generic`
- `tests/test_illustrated_scene.py`:
  - golden images (at least 9 topics, generic, a 5-line ellipsised headline, Unicode, and two
    stories with the same topic but different seeds that must differ)
  - rendering twice gives identical bytes
  - every output is 600,000 bytes or less
  - the layout invariants hold
  - the palette syncs with `app.css`
  - the WCAG contrast guard passes
  - an unsupported glyph raises `MediaContentUnsupported`
- `tests/test_story_card_renderer.py` (existing) must still pass unchanged after the `card.py`
  helper rename.
- `tests/test_media_models.py` (extend):
  - the manifest rules for the illustration fields
  - a pre-065 manifest dumps without the new keys
  - the pinned golden `story_package_hash`
  - illustration and card artifact IDs differ
- `tests/test_illustration_activities.py`:
  - disabled returns `disabled`
  - matched and generic write one artifact, and a re-run returns the identical artifact
  - an unsupported glyph raises `IllustrationInvalid`, non-retryable
  - other errors raise `IllustrationFailed`
  - the alt text matches the templates
- `tests/test_story_media_workflow.py` (extend, time-skipping):
  - matched, generic and failed-after-2-attempts paths
  - a card failure makes no illustration call
  - the illustration runs concurrently with the GIF
  - the card outcome is unchanged by an illustration failure
- `tests/test_story_generation_replay.py` (extend): `story_media_v1_gif.json` and the existing
  story-generation histories replay with the new code, and the two new histories replay.
- `tests/test_story_generation_media.py` (extend):
  - approve with `illustration`, `card` or no choice sets `selected` and changes the final hash
  - a story without an illustration keeps its review hash
- `tests/test_publishing_formatter.py` (extend):
  - the selected illustration is uploaded even when the destination supports GIF
  - `card` or `None` keeps the current behaviour
- `tests/test_config.py` (extend):
  - the default is `illustrated-card`
  - `off` and `fake` parse
  - an unknown value fails and the error lists the valid names
- `tests/test_web_story_media.py` and `tests/test_story_media_view.py` (extend):
  - the picker renders with `form="story-approve"`
  - matched preselects the illustration and generic preselects the card
  - the route passes `media_choice` and rejects bad values with 400
  - the failed note renders
  - Ready shows only the selected picture
  - the stacking class is present
  - `disabled` and old stories render unchanged
  - the illustration artifact is served through the existing media route

## Out of scope

- Any hosted or local image model, API keys or network calls. The provider slot is kept, but only
  `illustrated-card` and `fake` ship.
- Motion, animation of the illustrated card, Remotion, Node and Chromium. Motion is a later task.
- A dark-theme scene, or square/portrait sizes.
- A re-roll or "pick another drawing" control. Overriding the match by hand is a later task.
- More than 15 assets, or any library outside the table (unDraw, DrawKit, Storyset).
- Changing how the story card or GIF looks, the chart publisher, or the Bluesky activity, beyond
  the formatter's artifact choice.
- Drawing the summary, facts or source on the illustrated card.

**Split point (recommended if the diff grows past one comfortable review):**

- **065a:** assets, catalogue, sanitiser, rasteriser, matching, scene, provider, activity,
  workflow patch, manifest fields, config, worker, goldens, contact sheet and docs. With no
  picker, `selected` stays `None` and publishing uses the card, so 065a is safe and invisible
  except for the failed note.
- **065b:** the picker UI, `media_choice` through the route, service and workflow
  (`story-media-choice-v1`), and the formatter selection.

## Visual quality and the contact sheet

Nobody can judge whether these pictures look good until the human sees real output. The goldens
prove determinism, not taste. So:

- `tests/regen_illustration_goldens.py --contact-sheet` writes `docs/media/illustrated-card-samples.png`.
  It is a 3-column grid of 12 samples at 50 % scale, each labelled under the image with its
  topic and status, and kept under 1.5 MB. The samples are:
  - one sample for each of 9 topics (AI, security, code, energy, EVs, climate, economy, health,
    space)
  - one generic sample
  - one long-headline sample
  - one same-topic, different-seed sample

  The samples use made-up headlines that are fixed in the script. The script makes no live
  calls.
- The scribe links the contact sheet in the PR body. The human reviews it before merging, and
  can ask for assets to be swapped or the threshold tuned in a follow-up.

## Docs (builder updates README, tech.md and the publishing runbook)

- **Env var:** `ILLUSTRATION_PROVIDER` in the README configuration table and in
  `compose.prod.yaml`.
- **Cost:** zero. There is no API, key or network. CPU cost is about one PNG render per story
  (benchmark it and record the time in the docs).
- **Disable / rollback:** set `ILLUSTRATION_PROVIDER=off` in `.env.prod` and run `just deploy`.
  - New stories then get the card only, and in-flight and approved stories keep their frozen
    manifest.
  - A code rollback is safe because the new fields are optional and dropped when `None`.
  - Stories already approved with the illustration still publish it from the stored artifact.
- **Adding or replacing an asset:**
  1. Verify the licence on the official site.
  2. Normalise with `tests/vendor_illustrations.py`.
  3. Add the catalogue entry with the SHA-256 and the `LICENSES.md` line.
  4. Update the matching table.
  5. Regenerate the goldens and the contact sheet.

  A changed catalogue changes the illustration artifact IDs for new renders only.
- **Safety summary:**
  - The assets are vendored, sanitised, CC0 or public domain, with no text, logos or real people.
  - Matching is code-only, and a weak match never shows a figurative drawing.
  - A human picks the picture, and the card is always available.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: Rewrote spec 065 as the free offline illustrated card (vendored CC0/public-domain SVGs + hash-seeded background, `resvg-py` pinned, on by default with `ILLUSTRATION_PROVIDER=off` kill switch). It replaces the rejected Imagen design. The frontend-design skill was in context; the picker design is above.
Risks: the builder needs one-time web access to verify licences and download assets (stop and ask if not). The `resvg-py` API and wheels must be checked. The hash-compat serializer is load-bearing (pinned test). Record `story_media_v1_gif.json` on main code before changing anything.
Recommend splitting at 065a/065b. Visual quality can only be judged from the committed contact sheet and real review output.
Next: the human reviews and approves the spec.
