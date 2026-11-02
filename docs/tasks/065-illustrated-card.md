# 065: Illustrated card (free, offline still image)

Status: pr-open (PR #74, 065a only: https://github.com/akonale/ai-trend-pipeline/pull/74)

## Goal

Give each story a second still picture, the **illustrated card**. It combines the headline, a
free-licence drawing picked by keyword rules (a vendored CC0 Open Doodles drawing, or a simple
original topic glyph drawn for this project), and a procedural background
seeded from the story's content hash. Everything renders offline and deterministically, with no
API, key, network or cost. Review shows it next to the story card and the human picks which
picture goes out with Approve. The story card stays the fallback that always works, so a disabled,
weak-match or failed illustration never blocks review or publishing.

## Decisions

### A + D, not a hosted model

- **A, illustration:** a small curated pack of SVGs with a machine-readable licence catalogue:
  CC0 drawings from Open Doodles where one plainly fits a topic, plus **original flat topic
  glyphs authored in this repo** for the topics Open Doodles cannot cover (revision 3, human
  decision 2 Nov 2026, see "Project-original glyphs"). Code picks one with a readable keyword
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

### Third-party library to vendor: Open Doodles only

**Scope decision (human, 2 Nov 2026):** the starter set is **Open Doodles only**. Openclipart's
site currently serves no clipart (search is empty, detail pages return 500, downloads redirect to
a logo), so its licence and source cannot be verified. Openclipart is a **deferred source**, to be
added in a later task once its site serves content again. That task re-adds the
`openclipart` source and its licence value to the enums below.

**Licence evidence (verified by the builder on the official site, 2 Nov 2026):**

- Open Doodles home page (https://www.opendoodles.com/) says "Free for Commercial and Personal
  Use. No need to credit" and carries a CC0 Public Domain meta tag. It lists about 36
  illustrations.
- Its about page says the drawings may be used for "any purpose without restriction under
  copyright or database law (CC0 license)" and links
  https://creativecommons.org/publicdomain/zero/1.0/.

`LICENSES.md` quotes both statements exactly, with the URLs and the 2 Nov 2026 date.

| Source | Licence | Verdict |
| --- | --- | --- |
| Open Doodles (Pablo Stanley, opendoodles.com) | CC0 1.0, verified 2 Nov 2026 | **use** |
| Openclipart (openclipart.org) | cannot be verified, site not serving clipart | **deferred** to a later task |
| Open Peeps, unDraw, DrawKit, Storyset | not in scope | **do not use** |

Rules for each drawing:

- The builder downloads every drawing **from the official Open Doodles site**, not from mirrors,
  npm packages, GitHub forks or CDNs. If a drawing's source or licence cannot be verified on the
  official site, **skip that drawing**. Do not substitute another library.
- The catalogue's `licence` field accepts **`CC0-1.0`** for Open Doodles entries and
  `original-project` only for this project's own glyphs (see below), so no other licence can slip
  in. Hand-drawn CC0 cartoon people carry no real-person likeness, so they are allowed. The "no
  people" rule from the old Imagen design does not apply.

**Target topics.** The topics follow the pipeline's real inputs, which are Hacker News tech
stories plus the six OWID datasets (CO2, internet, solar, EVs, life expectancy, GDP). The builder
looks through the drawings actually on the site and maps a drawing to a topic **only when it
plainly shows that topic**:

AI, security, chips, code, internet, space, science, education, climate, energy, health, economy,
electric cars, food.

- Open Doodles are people-activity scenes, so most topics have no suitable drawing. **Outcome of
  the 065a build:** only education (`person-reading`, `reading-with-books`) and health
  (`person-meditating`) have one. Never force a drawing onto a topic it does not show. The other
  12 topics (ai, security, chips, code, internet, energy, electric-cars, climate, economy, space,
  science, food) get a **project-original glyph** instead (next section). A topic whose glyph is
  dropped for quality gets **no asset and no tags**, so its stories fall back to the generic
  scene with the story card preselected.
- **Size:** keep the 3 vendored Open Doodles drawings. Add no new Open Doodles drawings in this
  revision. At most 2 assets per topic and at most 30 in total, across both sources.
- Each asset gets a slug id that names what it shows (for example `person-reading`) and a
  `description` (the plain noun phrase used in alt text, for example "a person reading a book").
  The handoff note lists which topics got drawings and which did not.

What makes a drawing acceptable:

- flat colours, at most 8 distinct colours
- no text, logos, brand marks, flags, maps or landmarks
- 64 KB or less after normalisation
- a square-ish `viewBox`, aspect between 0.6 and 1.6

If a drawing fails these checks, skip it. Never hand-edit or redraw an Open Doodles drawing.
Only uncovered topics get a hand-authored glyph (next section).

### Project-original glyphs (revision 3, human decision 2 Nov 2026)

The human reviewed the first contact sheet. With only 3 drawings, 12 of 14 topics showed the
generic 8-point star, which is not visually appealing. **Decision:** author one simple, original,
flat SVG glyph per uncovered topic in this repo, at most two per topic. One is the target, and a
second is optional.

**Glyph design plan (frontend-design skill applied).**

- **Colour:** only the four authoring hexes, which are the site tokens. Each one is a recolour key:
  `#15172b` maps to `ink`, `#ffffff` to `paper`, `#2448d8` to `accent` and `#ffc93c` to
  `support`. At render time `support` becomes the hash-chosen drawing colour, exactly as for the
  doodles. No other colour may appear, and the sanitiser enforces this.
- **Type:** none. Glyphs contain no letters, digits, currency signs or other characters, not even
  ones drawn as paths. Brackets and chevrons drawn as plain strokes are allowed.
- **Layout:** authored on a 240×240 grid. There is one main object, optionally overlapped at its
  lower right by **one** smaller secondary object, like a sticker on a sticker. The main object
  is roughly centred and spans 180 to 220 units.
- **The one bold move:** every glyph's main silhouette sits on an ink copy of itself offset by
  (+6, +6) units. This echoes the cards' hard ink shadow, so the glyphs read as part of
  Topic Studio and not as an icon font. Every glyph does this the same way.
- **Check against the brief:** the generic default here is a thin, uniform line-icon set
  (Feather or Material style), or glossy gradient or emoji-like 3D objects. We rejected both. A
  chunky ink outline, flat token fills and a hard offset shadow match the neo-brutalist card and
  the flat colour blocks the plan asks for.

**Visual vocabulary (rules the builder follows and the tests check where they can):**

- **Outline:** every stroke is ink `#15172b` with `stroke-width` 6 on the 240 grid,
  `stroke-linejoin="round"` and `stroke-linecap="round"`. Use no hairlines and no other stroke
  widths. Before committing, the builder compares the rendered line weight with the cropped
  doodles at the scene's illustration size, and may change the single glyph stroke width
  (for example to 5 or 7) so the two sources look alike. Whatever value is chosen, every glyph
  uses it, and the test pins it as one constant.
- **Corners:** rectangles use `rx` 12, and small parts (under 40 units) use `rx` 6. Use no sharp
  90° outer corners, except where the object is pointed by nature (bolt, fins, flask neck).
- **Fills:** 2 or 3 fill regions besides ink, chosen from paper (the main body), support (the
  secondary region) and accent (one small detail, at most about 10 % of the glyph's area).
  Accent is never the main body. Ink is used only for outlines, the offset shadow and small solid
  details (eyes, keyhole, wheel hubs).
- **Simplicity:** at most 40 elements, a file of at most 8 KB, and no detail smaller than 12
  units. Use only plain `path`, `circle`, `ellipse`, `rect`, `line`, `polyline`, `polygon` and
  `g`, which the existing sanitiser allowlist already covers. Do not add `clipPath` unless it is
  needed.
- **Content rules:** the same as for the doodles: no text, logos, brand marks, flags, maps or
  landmarks. That means a globe gets meridians and parallels only, with no continents, a car is
  generic and not a recognisable make, and a coin carries no currency symbol.

**Subject per topic** (pick one; the alternative is the optional second glyph or a fallback idea):

| Topic | Glyph (main + secondary) | `description` for alt text (example) |
| --- | --- | --- |
| `ai` | friendly robot head (round eyes, antenna with accent ball); alt: sparkle-node brain | a friendly robot head |
| `security` | padlock (shackle arc, ink keyhole); alt: shield | a padlock |
| `chips` | square chip with pins on four sides and a support die | a computer chip |
| `code` | terminal window with a chevron prompt and cursor bar; alt: curly brackets | a code terminal window |
| `internet` | globe of meridians and parallels with three linked nodes | a globe with network links |
| `energy` | lightning bolt over a tilted solar panel | a lightning bolt over a solar panel |
| `electric-cars` | small generic car with a plug cable | an electric car with a charging plug |
| `climate` | leaf with veins in front of a sun disc | a leaf in front of the sun |
| `economy` | three-bar rising chart with a plain coin stack | a coin stack beside a rising bar chart |
| `space` | rocket with an accent porthole and support flame; alt: ringed planet | a rocket |
| `science` | conical flask with support liquid and bubbles; alt: atom | a lab flask |
| `food` | bowl of round fruit; alt: fork and plate | a bowl of fruit |

**How the builder checks quality (required):**

1. Author each glyph by hand as geometric SVG in a local scratch file, then normalise it with
   `tests/vendor_illustrations.py`, the same path the doodles take.
2. Render it with `tests/regen_illustration_goldens.py --asset-sheet` and **look at the image**
   (open the PNG). Iterate on coordinates until the silhouette reads at a glance at 50 % scale,
   the parts line up, and the strokes do not collide.
3. Check it on the contact sheet next to the doodles.
4. If a glyph still looks clumsy after a reasonable effort, **drop it**. Its topic then falls back
   to the generic star. Shipping fewer good glyphs beats shipping a bad one. The handoff note
   lists every dropped glyph.

**Licence.** The glyphs are original work authored for this project. The project owner decides
whether to dedicate them to the public domain under CC0 1.0; **that confirmation is still
pending** (the human has not yet stated it). The builder writes the `LICENSES.md` section as
specified below but must NOT claim that anyone confirmed ownership, and the PR must list the
dedication as an open item for the human to confirm before merge. Their catalogue records use
the project-original fields described below.

### Files and catalogue

- `src/ai_trend_pipeline/media/illustrations/assets/<id>.svg` holds the normalised, sanitised SVG.
  It lives under `src/` so the wheel and the Docker image ship it, as the fonts already do.
- `src/ai_trend_pipeline/media/illustrations/catalogue.json` is a list of entries, validated by the
  `IllustrationAsset` Pydantic model (frozen, `extra="forbid"`) with these fields:
  - `id`: slug `^[a-z][a-z0-9-]{1,31}$`. `file` is `<id>.svg`.
  - `title`
  - `description`: 3..80 characters, the plain noun phrase of what the drawing shows.
  - `topic`: one of the topic keys in the matching table below.
  - `source`: enum `open-doodles` | `project`.
  - `licence`: enum `CC0-1.0` | `original-project`.
  - `licence_url`: always `https://creativecommons.org/publicdomain/zero/1.0/`. For project
    originals this is the CC0 dedication.
  - `source_url: str | None`, `author`, `licence_verified_on: date | None`,
    `retrieved_on: date | None`. A model validator ties these to the source, and any mix fails
    validation:
    - **`open-doodles`**: `licence` is `CC0-1.0`, `author` is `Pablo Stanley`, `source_url` is
      an https page on `opendoodles.com`, and both dates are set (`licence_verified_on` is
      `2026-11-02` unless re-checked later). This is unchanged from 065a.
    - **`project`**: `licence` is `original-project`, `author` is `this project`, and
      `source_url`, `licence_verified_on` and `retrieved_on` are all `None` (there is no external
      source). `modified` is `false` and `modification_note` is `None`. `attribution_required`
      is `false`.
  - `sha256`: of the committed file.
  - `modified`: bool. `modification_note` is required when `modified` is true (for example
    "style attributes inlined, metadata removed, colours flattened, viewBox cropped to
    content").
  - `tags`: 3..30 entries, lowercase, unique. They equal the tags of the asset's `topic` row.
  - `recolour`: a map from every source hex colour in the file to a slot, `ink`, `paper`, `accent`
    or `support`. This map is the "safe recolour regions".
  - `attribution_required: bool` and `attribution_text: str | None`. For CC0
    assets it is `false`. If `true`, the text must appear in `LICENSES.md`. The field stays so
    a later source (for example Openclipart) fits without a model change.
- `src/ai_trend_pipeline/media/illustrations/LICENSES.md` is the human-readable list (asset, author,
  source URL, licence and link, verified date). It includes the CC0 1.0 legal-code link and the two
  Open Doodles licence statements quoted above, exactly as read on 2 Nov 2026. It notes that
  Openclipart is deferred. No attribution is needed in posts.
  It also has a **"Project-original glyphs"** section that lists each glyph id, gives the author
  as "this project", and states: "These glyphs are original work created for this project. The
  project owner dedicates them to the public domain under CC0 1.0
  (https://creativecommons.org/publicdomain/zero/1.0/). No attribution is required." It does not
  record any confirmation; the owner's CC0 dedication is confirmed in the PR before merge.
- **Normalisation happens once, when an asset is vendored or authored**, and its output is
  committed. The normalisation steps:
  1. Inline `style` into presentation attributes.
  2. Drop metadata, comments, `<title>`, `<desc>` and editor namespaces.
  3. Flatten colours to `#rrggbb`.
  4. Remove anything the sanitiser rejects.
  5. **Crop the `viewBox` to the content bounds.** Rasterise once with `rasterise_svg` at a
     fixed size of at least 1024 px on the long side. Take the alpha bounding box
     (`Image.getbbox()` on the alpha channel), map it back to viewBox units, and add padding of
     2 % of the long side so strokes are never clipped. Rewrite the root `viewBox` (and
     `width`/`height`, if present) to that box. Round to 2 decimals. Leave all geometry
     untouched.

  Steps 1 to 4 change a doodle file, so it keeps `modified: true` and its note names the crop.
  Glyphs go through the same steps but stay `modified: false`, because the cropped file is the
  original. The builder commits the small helper as `tests/vendor_illustrations.py`. It is
  dev-only, takes local file paths and makes no network calls. **Re-run it on the 3 existing
  doodles.** Their files, SHA-256 values and catalogue entries change.

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
| `vl-convert-python` `svg_to_png` (already a dependency, bundles resvg) | none | +0 | pinned by the lock, but tied to the chart library's upgrade cycle | not needed (`resvg-py` wheels cover our platforms) |

**Already done by the builder (commit `dcbaeee`, do not redo):**

- `resvg-py==0.5.0` is pinned with `==` in `pyproject.toml` and `uv.lock`, with `[tool.uv]
  no-build-package = ["resvg-py"]`, so a missing wheel fails the lock instead of needing a Rust
  toolchain in Docker.
- Its abi3 wheels cover Python 3.12 on manylinux x86_64 and aarch64.
- The real API is `resvg_py.svg_to_bytes(svg_string=..., width=..., height=...,
  skip_system_fonts=True)`, which returns PNG bytes. Always pass `skip_system_fonts=True`.

Remaining work:

- Wrap the call in one typed function, `rasterise_svg(svg: str, width: int, height: int) ->
  Image.Image` (RGBA), in `media/illustrations/rasterise.py`. It decodes the returned PNG bytes
  with Pillow and converts to RGBA. If the library has no type
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
- **Score per topic** = 3 × (distinct tags hit in the headline) + 1 × (distinct tags hit in the
  summary, first 600 characters only).
- **Only covered topics take part.** Matching runs over the topics that have at least one asset
  in the catalogue. A topic with no asset contributes no tags, so a story about it scores 0 and
  gets the generic scene.
- **Pick:** the highest score wins. Break ties by more headline hits, then by topic order, which
  is the table order below and acts as priority.
- **Asset within a topic:** if the winning topic has 2 assets, a byte from the scene's seed stream
  (see "Seed and stream") picks one, so the choice is deterministic per story.
- **Threshold:** a score of 3 or more is `matched`. That means at least one headline hit, or three
  summary hits. Anything below 3 is `generic`: background plus a code-drawn abstract motif and
  **no figurative illustration**, so a weak match never shows a wrong-topic drawing.

Target topic table. Rows whose topic gets no drawing stay in the code table but are **inactive**
(no catalogue tags, never matched). The matching module keeps the whole table so a later asset
(for example from Openclipart) needs no matching change, only a catalogue entry (and, for a new
source, its enum values).

| Order | Topic key | Tags |
| --- | --- | --- |
| 1 | `ai` | ai, artificial intelligence, llm, llms, chatbot, robot, robots, machine learning, neural, gpt, openai, anthropic, claude, gemini, agent, agents |
| 2 | `security` | security, privacy, hack, hacked, hackers, breach, vulnerability, exploit, malware, ransomware, encryption, password, passwords |
| 3 | `chips` | chip, chips, semiconductor, gpu, gpus, cpu, processor, silicon, nvidia, hardware |
| 4 | `code` | software, code, coding, programming, developer, developers, open source, compiler, linux, python, rust, javascript, database, app |
| 5 | `internet` | internet, online, broadband, web, network, browser, connectivity, wifi |
| 6 | `energy` | solar, wind, renewable, renewables, energy, electricity, power grid, battery, batteries |
| 7 | `electric-cars` | electric car, electric cars, electric vehicle, ev, evs, tesla, charging |
| 8 | `climate` | climate, co2, carbon, emissions, environment, nature, forest, warming, pollution |
| 9 | `economy` | gdp, economy, economic, income, money, prices, inflation, market, markets, funding, startup, revenue, tax |
| 10 | `health` | health, life expectancy, longevity, medicine, medical, disease, hospital, vaccine, fitness |
| 11 | `space` | space, rocket, nasa, satellite, satellites, orbit, launch, mars, moon |
| 12 | `science` | science, research, researchers, scientists, study, lab, physics, biology, chemistry |
| 13 | `education` | education, school, schools, students, learning, book, books, teachers |
| 14 | `food` | food, farming, agriculture, crops, harvest, diet |

The old `bike-commute` transport row is dropped: it was not in the topic list. The tags of every
catalogue entry must equal the row of its `topic`, and a test checks it.

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
  - 2-3 small accent shapes (dot, ring, bar), each placed in a distinct slot from a fixed tuple
    `ACCENT_SLOTS` (see "Accent placement"); never anywhere on a free grid
  - which asset to draw, when the matched topic has 2

  There is no Python `random`. Bump `SCENE_TEMPLATE_VERSION` to `"2"`. The byte order of the
  stream may change, and the goldens are regenerated anyway.
- **Accent placement (fixes the stray-confetti flaw).** No accent may be half-hidden behind the
  backdrop or its shadow.
  - `ACCENT_SLOTS` is a hand-picked tuple of accent centres in the free bands of the art area:
    above and below the backdrop, and in the corners that every backdrop shape leaves empty.
  - Every accent's full bounding box, stroke included, must lie inside `ART_AREA` with an 8 px
    margin. It must also stay at least 8 px away from the backdrop's bounding box for **every**
    shape, with the tilt rotation and the 12 px shadow offset included, and away from the
    illustration's fitted box.
  - Sizes: a dot is at most 24 px across, a ring at most 28 px outer diameter with a 5 px stroke,
    and a bar is 56×12 px and horizontal.
  - Today's numbers leave thin bands: the art area is 680..1160 × 40..623 and the tilted square's
    bounding box with its shadow is about 513 px tall. If no slots fit, shrink `BACKDROP_SIZE` and
    `TILTED_SIZE` (for example to 440 and 420) until every slot clears every shape. Do not drop
    the slots or clip the accents.
  - The `_check` invariants assert the slot rules for the chosen spec, and a test checks them
    for all slots against all three shapes.
- **Illustration size (fixes the small-drawing flaw).** Because each `viewBox` is cropped to its
  content, fitting the `viewBox` fits the drawing itself.
  - Set the illustration box so that the fitted drawing's longest side is `round(0.80 ×
    BACKDROP_SIZE)` (368 px at 460). Replace `ILLUSTRATION_BOX = 440` with that derived value.
  - The tests accept 0.75 to 0.85 of `BACKDROP_SIZE`, so the drawing fills about 75 to 85 % of
    the tile.
  - On a circle, corners of a wide drawing may overlap the rim slightly, which is an intended
    sticker look. The drawing must stay inside `ART_AREA` and clear of the panel and its shadow.
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
  - the illustration fits its box, aspect preserved and centred, and its longest side is 0.75 to
    0.85 of `BACKDROP_SIZE`
  - every accent is fully inside the art area and clear of the backdrop, its shadow and the
    illustration box (see "Accent placement")
- **Illustration rendering:**
  1. Sanitise the SVG.
  2. Substitute `recolour` colours. This is string replacement of the exact mapped hex values,
     which works because the sanitiser guarantees no other colours exist.
  3. Rasterise to RGBA at the fitted size.
  4. Alpha-composite the result onto the backdrop.
- **Generic motif** (`generic`, no illustration): a code-drawn ink-outlined 8-point burst in the
  second support colour, centred on the backdrop. It is abstract, not figurative. It stays as the
  fallback for weak or no matches, and for topics whose glyph was dropped. Size it with the same
  0.80 × `BACKDROP_SIZE` rule.
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
  the Temporal payloads of pre-065 packages byte-identical. A golden test pins it with these
  literals, **already computed by the builder on `main` before any code change** (do not
  recompute them on the branch):
  - `story_package_hash(make_package("story-1"))` =
    `108f57c12e08b7f317e357cf8820262f041a21a5ba1734889daa5849a3df9a93`
  - the same package with `media=make_manifest(MediaOutcome.GIF)` =
    `855c28a843fe38403dc57e4b1f242ad0a19481c08ecc8057fee066a1977ef0cf`
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

- [ ] For a review story whose headline matches a covered topic (one that has an Open Doodles
      drawing or a project-original glyph), the illustrated card shows that topic's asset on a hash-seeded background and is preselected,
      with the story card beside it. Approve with either choice gives a Ready story whose
      `content_hash` covers `selected`, and the Bluesky formatter uploads exactly that still with
      its alt text.
- [ ] Nothing below the score threshold, and no story about a topic without a drawing, ever gets
      a figurative illustration. It gets the generic scene with the story card preselected. `ILLUSTRATION_PROVIDER=off`, or a failed render,
      leaves review and publishing exactly as before 065 (except the fixed failed note).
- [ ] Rendering is offline and deterministic: the same input gives byte-identical PNG bytes, the
      goldens match, and the output is within `MAX_STATIC_BYTES`. Storage is content-addressed and
      atomic, and re-running the activity is safe.
- [ ] Every SVG asset passes the sanitiser allowlist and has a complete catalogue record whose
      SHA-256 matches the file. Each record is either an Open Doodles drawing (`CC0-1.0`, official
      `opendoodles.com` URL, author, retrieved and verified dates) or a project-original glyph
      (`original-project`, author `this project`, source `project`, no URL or dates). No other
      mix validates. `LICENSES.md` lists every asset, quotes the Open Doodles statements and
      states the CC0 dedication of the glyphs. There are at most 30 assets, at most 2 per topic.
- [ ] Of the 12 topics without a doodle, every one has a shipped glyph that follows the glyph
      vocabulary, or the handoff names it as dropped for quality. Every asset's `viewBox` is
      cropped to its content, so the drawing fills 75 to 85 % of the backdrop tile, the 3
      doodles included. No accent is half-hidden behind the backdrop or its shadow.
- [ ] Pre-065 `StoryMediaWorkflow` and `StoryGenerationWorkflow` histories replay, and a pre-065
      package's `story_package_hash` equals the pinned golden value. The card goldens are
      unchanged.
- [ ] The review picker works without JavaScript and stacks at 360px (065b). The contact sheet
      and the asset sheet are committed for the human to judge before merge, and `just lint` and
      `just test` pass with no network.

## Files to touch

- `pyproject.toml` and `uv.lock`: **done** in `dcbaeee` (`resvg-py==0.5.0`, `[tool.uv]
  no-build-package`). Touch again only if a later step needs it.
- `src/ai_trend_pipeline/media/illustrations/__init__.py` (new)
- `src/ai_trend_pipeline/media/illustrations/assets/*.svg`: the 3 doodles are re-normalised with
  the viewBox crop, and up to 12 new glyphs are added, one per uncovered topic, for example
  `glyph-robot.svg`, `glyph-padlock.svg`, `glyph-chip.svg`, `glyph-terminal.svg`,
  `glyph-globe.svg`, `glyph-bolt-panel.svg`, `glyph-electric-car.svg`, `glyph-leaf-sun.svg`,
  `glyph-coins-chart.svg`, `glyph-rocket.svg`, `glyph-flask.svg` and `glyph-fruit-bowl.svg`.
  The rule is at most 2 per topic and at most 30 in total.
- In revision 3, also `catalogue.json`, `catalogue.py` (the `project` source, the
  `original-project` licence, the per-source validator, `MAX_ASSETS = 30`), `LICENSES.md`,
  `scene.py` (accent slots, illustration size, `SCENE_TEMPLATE_VERSION = "2"`) and possibly
  `matching.py` (only if a test needs a helper; the table is unchanged), all in the list below.
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
- `tests/vendor_illustrations.py` (new, dev-only normaliser, no network; revision 3 adds the
  viewBox crop step and accepts local glyph source files)
- `tests/regen_illustration_goldens.py` (new; writes the contact sheet, and in revision 3 also
  `--asset-sheet`)
- `tests/fixtures/media/illustrated/*.png` (new goldens; regenerate all, and add one per glyph
  topic)
- `tests/test_illustration_glyphs.py` (new in revision 3)
- `docs/media/illustration-assets.png` (new in revision 3: the asset sheet)
- `tests/fixtures/histories/story_media_v1_gif.json`: **done** in `6edf767` (recorded from `main`
  code before any change). Do not re-record or edit it.
- `tests/fixtures/histories/story_media_v2_illustrated.json` and
  `story_generation_v3_media_choice_ready.json` (new)
- `docs/media/illustrated-card-samples.png` (new contact sheet, regenerated in revision 3)
- Docs: `README.md`, `docs/architecture/tech.md`, `docs/runbooks/publishing.md`

## Tests to write

- `tests/test_illustration_catalogue.py` checks every SVG in `assets/` against the catalogue:
  - each file has exactly one entry, and each entry has a file
  - every entry is **either** a full Open Doodles record (`open-doodles`, `CC0-1.0`, `Pablo
    Stanley`, a `source_url` on `opendoodles.com`, both dates set and not in the future) **or** a
    project-original record (`project`, `original-project`, `this project`, `source_url` and both
    dates `None`, `modified` false)
  - the existing verified-licence tests for the 3 Open Doodles entries are kept
  - mixes fail model validation: a `project` source with a URL or dates, an `open-doodles`
    source with `original-project`, a `project` entry with another author, and any other licence
    or source value (for example an Openclipart value)
  - the SHA-256 matches the file
  - `attribution_required` implies the text is in `LICENSES.md`, every asset id appears in
    `LICENSES.md`, and `LICENSES.md` contains the CC0 dedication sentence for the glyphs
  - each entry's `topic` is a key of the topic table and its tags equal that row
  - there are at most 30 assets and at most 2 per topic
  - **tight viewBox:** each asset rasterised at 512 px has an alpha bounding box that spans at
    least 95 % of the canvas on its long side, and its viewBox aspect is 0.6 to 1.6
- `tests/test_illustration_glyphs.py` checks every `project` asset:
  - the only colours are the four authoring hexes, and its `recolour` map is exactly those four
    mapped to their slots
  - every stroked element uses ink and the one pinned glyph stroke width, with round joins
  - 2 or 3 distinct non-ink fills
  - at most 40 elements and at most 8 KB
  - no `text` element (the sanitiser already rejects it), and the id starts with `glyph-`
  - each of the 12 uncovered topics either has a glyph or is listed in a `DROPPED_GLYPHS` constant
    in the test, which the handoff explains
- `tests/test_illustration_sanitize.py`:
  - all vendored assets pass
  - crafted SVGs are each rejected: `<script>`, `onload=`, `<foreignObject>`, `<image href>`,
    `<text>`, `<style>`, `xlink:href`, `url(http…)`, `<!DOCTYPE>`/`<!ENTITY>`, an unmapped
    colour, a missing `viewBox`, and files over 64 KB
- `tests/test_illustration_matching.py` is table-driven. Unit tests use a small in-test
  catalogue, so they do not depend on which topics the real assets cover:
  - one headline per covered topic picks an asset of that topic
  - a headline about a topic with no asset in the in-test catalogue (for example "Ransomware
    hits hospital billing" when `security` has none) is `generic`, never another topic's drawing
  - multi-word tags match only as consecutive tokens
  - "rust" in a headline picks the `code` topic
  - a summary-only single hit is `generic`
  - three summary hits make `matched`
  - ties use headline hits, then topic order
  - with 2 assets in a topic, the pick is stable for one content hash and both can be reached
  - the result does not depend on case or Unicode form
  - a headline with no tags gives `generic`
  - against the real catalogue: one fixed headline per covered topic matches that topic
- `tests/test_illustrated_scene.py`:
  - golden images: one per covered topic (every doodle topic and every glyph topic), generic from
    no tags, generic from a summary-only single hit (and from an uncovered topic, if any glyph
    was dropped), a 5-line ellipsised headline, Unicode, and two stories with the same topic but
    different seeds that must differ
  - the fitted illustration's longest side, and the burst's, is 0.75 to 0.85 of `BACKDROP_SIZE`
  - every slot in `ACCENT_SLOTS`, at each accent size, lies inside the art area with an 8 px
    margin and clears the backdrop bounding box of all three shapes (tilt and shadow included)
    and the illustration box
  - over 200 fixed content hashes, `_check` passes and no accent intersects the backdrop
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
  - the two pinned golden `story_package_hash` literals above (`108f57c1…` and `855c28a8…`)
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
- More than 30 assets (or more than 2 per topic), or any source other than Open Doodles and
  project-original glyphs. Openclipart is deferred to a later task once its site serves content
  again. Open Peeps, unDraw, DrawKit and Storyset are not used. Do not trace, copy or adapt
  third-party icons (for example Feather, Material, Font Awesome or emoji sets) into glyphs: they
  must be original geometry.
- Forcing a drawing onto a topic it does not show, editing or redrawing an Open Doodles drawing,
  adding new Open Doodles drawings, or adding glyphs for education or health (they already
  have drawings).
- Changing the score threshold, the topic table or the generic star design.
- Any 065b work (picker, `media_choice`, formatter selection). 065b is unchanged by revision 3.
- Re-recording `story_media_v1_gif.json`, recomputing the pinned hashes, or changing the
  `resvg-py` pin.
- Changing how the story card or GIF looks, the chart publisher, or the Bluesky activity, beyond
  the formatter's artifact choice.
- Drawing the summary, facts or source on the illustrated card.

**Split point (recommended if the diff grows past one comfortable review):**

- **065a:** (the human approved starting here; branch `task/065-illustrated-card`.)
  - Already done: the `story_media_v1_gif.json` baseline history (`6edf767`), the `resvg-py`
    pin (`dcbaeee`), and the two baseline hash literals above.
  - Remaining: Open Doodles assets, catalogue, sanitiser, rasteriser, matching, scene, provider,
    activity, workflow patch, manifest fields, config, worker, goldens, contact sheet and docs.
  - With no picker, `selected` stays `None` and publishing uses the card, so 065a is safe and
    invisible except for the failed note.
  - **Revision 3, still part of 065a** (built and verified, but not merged; all existing commits
    stay): add the project-original glyphs and their catalogue and licence handling, crop every
    viewBox, enlarge the drawing to about 80 % of the tile, move the accents to fixed slots, and
    regenerate the goldens, the contact sheet and the asset sheet. Then re-verify.
- **065b:** the picker UI, `media_choice` through the route, service and workflow
  (`story-media-choice-v1`), and the formatter selection.

## Visual quality and the contact sheet

Nobody can judge whether these pictures look good until the human sees real output. The goldens
prove determinism, not taste. So:

- `tests/regen_illustration_goldens.py --contact-sheet` writes `docs/media/illustrated-card-samples.png`.
  It is a 3-column grid of 12 samples at 50 % scale, each labelled under the image with its
  topic and status, and kept under 1.5 MB. The 12 samples are:
  - 8 matched samples covering 8 **distinct** topics: at least 1 doodle topic and at least 6
    glyph topics
  - at least 2 generic star fallbacks: a headline with no tags, and a summary-only single hit (or
    a headline about a topic whose glyph was dropped, labelled with that topic and `generic`)
  - one long-headline sample
  - one same-topic, different-seed sample (keep it on `education`, which has 2 doodles)

  The samples use made-up headlines that are fixed in the script. The script makes no live
  calls.
- `tests/regen_illustration_goldens.py --asset-sheet` writes `docs/media/illustration-assets.png`.
  It has one tile per catalogue asset, each drawn on a fixed sample backdrop with its matched
  scene's support colour, and labelled with id and source (`open-doodles` or `project`). It stays
  under 1.5 MB. This is the builder's iteration tool for glyphs, and the human's per-asset check.
- The builder opens and looks at both sheets before setting `built` and says in the handoff what
  they changed after looking.
- The scribe links both sheets in the PR body. "Tasteful" is subjective, so **the human judges the
  glyphs on these sheets before merge**. The human can ask for a glyph to be redrawn or dropped
  (which makes that topic fall back to the star), or for the threshold to be tuned in a
  follow-up.

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
- **Coverage:** list which topics have Open Doodles drawings, which have project-original glyphs,
  and which (if any) fall back to the generic scene. Note that Openclipart is a deferred source.
- **Adding or changing a glyph:** follow the glyph vocabulary (the four token hexes, one stroke
  width, round joins, 2-3 fills, an ink offset shadow, no text). Normalise it with
  `tests/vendor_illustrations.py` and add a `project` / `original-project` catalogue entry plus
  its `LICENSES.md` line. Regenerate the asset sheet, look at it, and regenerate the goldens and
  the contact sheet.
- **Adding or replacing a third-party asset:**
  1. Verify the licence on the official site and download from it. Adding a new source (for
     example Openclipart) also needs a new `source` and `licence` enum value, reviewed in its own
     task.
  2. Normalise with `tests/vendor_illustrations.py`.
  3. Add the catalogue entry with the SHA-256 and the `LICENSES.md` line.
  4. Update the matching table.
  5. Regenerate the goldens and the contact sheet.

  A changed catalogue changes the illustration artifact IDs for new renders only.
- **Safety summary:**
  - The assets are either vendored from the official Open Doodles site, or original glyphs this
    project dedicates under CC0. All are sanitised, with no text, logos or real people.
  - Matching is code-only, and a weak match or an uncovered topic never shows a figurative
    drawing.
  - A human picks the picture, and the card is always available.

## Risks

- **Thin coverage (addressed in revision 3).** Open Doodles covered only 2 of 14 topics, so
  project-original glyphs cover the rest.
- **Hand-written glyph quality.** Geometric SVG written by hand can come out stiff, misaligned or
  clip-art-like, and "tasteful" is subjective. Mitigations:
  - a strict, small vocabulary
  - the asset sheet as an iteration loop
  - permission to drop a glyph rather than ship it
  - the human judging both sheets before merge

  Expect a fix loop on looks alone.
- **Two visual languages.** Loose-line doodles of people next to chunky object glyphs may clash.
  The shared recolour palette, the matched stroke weight and the 80 % fill keep them close. The
  human decides on the contact sheet.
- **Accent slots too tight.** The free bands around the tilted square are thin. Shrinking the
  backdrop is the allowed fix; clipping or half-hiding the accents is not.
- **Loose fits.** The temptation is to stretch a people scene over a topic it does not show.
  Do not: no drawing beats a wrong one. The human checks fit on the contact sheet.
- **Site changes.** If opendoodles.com stops serving a drawing or its licence page before the
  builder downloads it, skip that drawing. If none can be verified, stop and ask the human.
- **Load-bearing hash compatibility.** The serializer that omits `None` fields is pinned by the
  two baseline hash literals; any change to them is a bug.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: Rewrote spec 065 as the free offline illustrated card (vendored CC0/public-domain SVGs + hash-seeded background, `resvg-py` pinned, on by default with `ILLUSTRATION_PROVIDER=off` kill switch). It replaces the rejected Imagen design. The frontend-design skill was in context; the picker design is above.
Risks: the builder needs one-time web access to verify licences and download assets (stop and ask if not). The `resvg-py` API and wheels must be checked. The hash-compat serializer is load-bearing (pinned test). Record `story_media_v1_gif.json` on main code before changing anything.
Recommend splitting at 065a/065b. Visual quality can only be judged from the committed contact sheet and real review output.
Next: the human reviews and approves the spec.

**planner**: Revised the approved spec (human decision): starter set is Open Doodles only (CC0, verified 2 Nov 2026), and Openclipart is deferred because its site serves no clipart. Licence and source enums allow only `CC0-1.0` / `open-doodles`.
Matching is now keyed by 14 target topics. A topic with no suitable drawing gets no tags and falls back to the generic scene; the target is about 10-20 drawings (at most 2 per topic), and fewer is fine. Tests, contact sheet, docs and risks are updated to match.
Recorded the builder's facts as done: the `resvg-py==0.5.0` pin and API (`dcbaeee`), the `story_media_v1_gif.json` baseline (`6edf767`) and the two baseline hash literals. The UI design is unchanged.
Next: the builder continues 065a. Status stays approved.

**builder**: Built 065a ONLY (picker, `media_choice`, `story-media-choice-v1`, formatter change and `story_generation_v3` history are 065b; Status `built` covers 065a, not the whole task). `resvg-py==0.5.0` (abi3 wheels, cp312 manylinux x86_64 and aarch64, typed, no stub). Coverage: education (`person-reading`, `reading-with-books`) and health (`person-meditating`); the other 11 topics fall back to the generic scene. Skipped: `plant` (88 KB after normalising, over 64 KB, so no climate drawing) and `running` (drawn "Si" lettering on the shirt, so it counts as text).
Added beyond the file list: the failed note (view model, template, CSS) plus tests, `ILLUSTRATION_PROVIDER` in compose, `worker.illustration_provider()`. Contact sheet viewed: no clipped text, no empty scenes, recolours right; drawings sit smallish in the backdrop (the Open Doodles viewBox has wide margins) and some accent dots or rings peek out from behind the backdrop like stray confetti. Render about 0.4 s per story.


**verifier**: 065a scope. `just lint` passed (ruff check, ruff format --check, pyright 0 errors). `just test` passed (1594 passed, 2 pydantic serializer warnings in test_web_story_detail.py). ANTHROPIC_* unset for both runs.
All checks pass.

**planner**: Revision 3 (human-approved after the contact sheet review). 065a gains original flat topic glyphs for the 12 topics without a doodle, using a strict vocabulary (4 token hexes, one ink stroke, 2-3 fills, an ink offset hard shadow, no text). They get an `original-project` / `project` catalogue record and a CC0 dedication in LICENSES.md. Assets are capped at 30 in total.
It also fixes both visual flaws: every viewBox, the 3 doodles included, is cropped to its content and the drawing fills about 80 % of the tile, and accents go only in fixed `ACCENT_SLOTS` that clear the backdrop. Goldens, the contact sheet and the new asset sheet are regenerated.
A builder may drop a glyph that looks bad, and its topic then falls back to the star. The human judges the glyphs on the sheets before merge. 065b is unchanged.
Next: the builder continues 065a on the same branch, then the verifier runs again.

**builder (revision 3, 065a only)**: Added 12 project-original glyphs (all topics without a doodle: ai, security, chips, code, internet, energy, electric-cars, climate, economy, space, science, food), none dropped (`DROPPED_GLYPHS` is empty). Glyph stroke is 5 (not 6) so lines sit nearer the doodles, which are still finer. Cropped all viewBoxes (the 3 doodles re-normalised, SHA-256s updated), scene version "2" (fixed `ACCENT_SLOTS`, `BACKDROP_SIZE` 440 and `TILTED_SIZE` 420, drawing at 0.80 of the tile), goldens, contact sheet and `docs/media/illustration-assets.png` regenerated and viewed.
Weaker glyphs: `glyph-bolt-panel` (bolt over the panel is busy) and `glyph-fruit-bowl` (plain round fruit, floating leaf); doodles and glyphs share the palette and hard shadow but not line weight, so they read as two related styles, not one.
OPEN ITEM for the human: the owner's CC0 dedication of the glyphs is not confirmed. `LICENSES.md` carries the spec's dedication sentence and records no confirmation; confirm it before merge. Still 065a only (no picker, no `media_choice`).


**verifier (revision 3, 065a scope)**: `just lint` passed (ruff check, ruff format --check, pyright 0 errors). `just test` passed (1692 passed, 2 pydantic serializer warnings in tests/test_web_story_detail.py). ANTHROPIC_* unset for both runs.
All checks pass.

**reviewer (065a)**: Approved. Replay of both histories, pinned hashes, sanitiser, catalogue validation, config/worker/compose wiring all check out; 065b not built; `.claude/settings.json` not in the diff.
Notes: (1) human must still confirm the glyph CC0 dedication before merge (LICENSES.md states it per spec, claims no confirmation). (2) glyph-bolt-panel and glyph-fruit-bowl are the weakest; doodles are finer-lined and sit smaller in the tile than the bold glyphs. (3) leave `.claude/settings.json` untracked.

**scribe (065a)**: Docs checked, no stale text (README, tech.md, publishing runbook and LICENSES.md match 065a). Branch pushed; PR https://github.com/akonale/ai-trend-pipeline/pull/74 opened against main (065a only).
Open items for the human before merge: owner's CC0 glyph dedication unconfirmed; doodle vs glyph line weight; glyph-bolt-panel and glyph-fruit-bowl keep/redraw/drop; no browser screenshots of the review page.
Status set to pr-open. Not merged or deployed.
