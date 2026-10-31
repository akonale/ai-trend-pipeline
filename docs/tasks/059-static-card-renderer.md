# 059: Static story-card renderer

Status: pr-open

> Plan row **058** in `docs/mvp-plan.md` ("Static story-card renderer"), renumbered to **059**
> because task 057 was already taken and plan row 057 shipped as task 058
> (`docs/tasks/058-media-foundation.md`). Plan rows 059 (GIF renderer) and 060 (integration) are
> repo tasks **060** and **061**; the cross-references below use the repo numbers.

## Goal

Add the first real `MediaRenderer`: a deterministic, code-laid-out story card (headline, one
supported key fact or short summary, small source footer) rasterised to PNG with Pillow and two
vendored Archivo fonts, registered in the 058 provider registry. Add a thin web route that serves
an artifact only when the story's own `MediaManifest` lists it.

## Design

### Rasterisation and fonts

- Add `pillow` as a runtime dependency (`uv add pillow`). No other new runtime or dev dependency.
  Pillow ships inline types; if one Pillow call still trips Pyright strict, use a narrow
  `# pyright: ignore[rule]` with a comment saying why.
- Always load fonts with `ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.BASIC)`.
  BASIC avoids output differences between machines with and without libraqm.
- Vendor two static **Archivo** TTFs (Omnibus-Type, SIL OFL 1.1) into
  `src/ai_trend_pipeline/media/fonts/`: `Archivo-ExtraBold.ttf` (weight 800, normal width:
  headline and wordmark) and `Archivo-Medium.ttf` (weight 500, normal width: fact, summary,
  footer). Take them from the `fonts/ttf/` folder of the latest `Omnibus-Type/Archivo` GitHub
  release. If a static file is missing there, instance it from the release's variable
  `Archivo[wdth,wght].ttf` with `uvx --from fonttools fonttools varLib.instancer ... wght=800
  wdth=100` (one-off, not a project dependency). Add `OFL.txt` (verbatim licence) and a short
  `README.md` with release version, source URL, any instancer command, and the SHA-256 of each
  file. Hatchling ships every file in the package and the Dockerfile copies `src`, so no
  Dockerfile change.
- Fonts are found with `Path(__file__).parent / "fonts"`. No system fonts, network access, or
  remote assets. Font objects are cached per (file, size) with `functools.cache`.
- Glyph coverage: `media/fonts.py` has `supported_codepoints(path) -> frozenset[int]`, a small
  pure-`struct` parser for the TTF `cmap` table (formats 4 and 12, platform 3 encodings 1/10 and
  platform 0), cached. Do not add fontTools as a dependency.
- Archivo covers Latin, Latin Extended and Vietnamese, not Greek, Cyrillic or CJK. That is a
  deliberate trade for a distinctive face; see the text policy for the fallback.

### Text policy (in `media/card.py`)

Applied to `headline`, `highlight` and `source_label` before layout:

1. Normalise to NFC.
2. Drop characters whose Unicode category is `So`, `Sk`, `Cf`, `Mn` or `Me` and that the font
   does not cover (emoji, ZWJ, variation selectors, stray combining marks). Collapse runs of
   spaces and strip.
3. Any remaining character not covered by **both** fonts is unsupported:
   - **Headline:** raise `MediaContentUnsupported` (outcome becomes text-only). An empty headline
     after step 2 also raises. A headline is never transliterated.
   - **Highlight / source label:** leave that element out of the card.
4. Text is drawn literally. Markdown- or entity-like text (`**x**`, `&amp;`) appears as typed.
   Everything else is already blocked by `MediaBrief` validation.

### Design plan (card visual)

`frontend-design` skill was in context. Subject: a share card for an AI-trend news story that
people see in a Bluesky feed and on the review page. Its job is to make one headline and one
verified fact readable at a glance and recognisably from Topic Studio.

- **Colour:** reuse the app's "graph paper and ink" tokens (`app.css`), so the card reads as part of
  Topic Studio. Yellow is used only for the fact marker. The panel shadow uses the ink colour (like
  `--shadow-md`), not yellow, so the eye goes to one yellow element.
- **Type:** one family, Archivo, in two weights. ExtraBold is the headline voice. It is a heavy
  grotesque that sits next to the app's Arial Black headings without being a system default.
  Medium is used for the fact and footer. There are no all-caps labels, no eyebrow, and no
  "Key fact" label above the fact.
- **Layout:** left-aligned, top-down reading order. The ground is graph paper, with a white
  panel on top that has a thick ink border and a hard offset shadow. Inside the panel are the
  headline, then the fact, then a ruled footer.
- **Principle (the one memorable thing):** a supported key fact gets yellow **highlighter
  bands** behind each line, as if marked with a pen. The marker means "this line is a checked
  fact". A summary fallback (brief has no `highlight_fact_ids`) is plain muted text with no
  marker, so the visual encodes evidence. This also sets up the GIF's `FACT_EMPHASIS` beat in
  task 060 (the marker sweeping in).
- **Review against the brief:** the earlier draft had a yellow shadow, an all-caps `STORY` chip,
  a blue accent bar and Inter. I dropped the chip because it was a generic eyebrow, the accent bar
  because it was decoration, and Inter because it is the default face. I moved the shadow to ink
  so yellow means one thing. The neo-brutal border and shadow follow the plan's design direction
  and are kept.

```
+--------------------------------------------------------------+
| graph-paper ground                                           |
|   +------------------------------------------------------+   |
|   | Headline in Archivo ExtraBold, up to                 |#  |
|   | four lines, left aligned                             |#  |
|   |                                                      |#  |
|   | [### fact line one on a yellow band ###]             |#  |
|   | [### fact line two ###]                              |#  |
|   |                                                      |#  |
|   | ---------------------------------------------------- |#  |
|   | Source: Hacker News                     Topic Studio |#  |
|   +------------------------------------------------------+#  |
|    ########################################################  |
+--------------------------------------------------------------+
```

**Tokens:** the `CARD_THEMES: dict[CardTheme, CardPalette]` in `media/card.py`, with values copied
from `app.css`. A test keeps them in sync.

| Role | Light (`app.css` token) | Dark (`app.css` dark token) |
| --- | --- | --- |
| ground | `#e3ecdf` (`--color-bg`) | `#191c30` |
| grid lines | `#d2dfcc` (`--color-grid`) | `#23273f` |
| panel | `#ffffff` (`--color-surface`) | `#262a44` |
| headline text, border, rule, shadow | `#15172b` (`--color-text`/`--color-border`/`--color-shadow`) | `#e3ecdf` |
| summary and source text | `#4a4f5e` (`--color-muted`) | `#b4bacb` |
| wordmark | `#2448d8` (`--color-accent`) | `#8daeff` |
| marker band | `#ffc93c` (`--color-highlight`) | `#ffc93c` |
| text on marker | `#15172b` (`--color-on-fill`) | `#15172b` |

Contrast (all checked by a test): ink on white 17:1, muted on white 8.2:1, accent on white 7:1,
ink on marker 11.5:1. In dark: text on panel 11:1, muted on panel 7.2:1, accent on panel 6.4:1.

**Geometry:** the reference canvas is 1200x675. Every number below is reference px multiplied by
`s = min(W/1200, H/675)` and rounded to int (minimum 1).

- **Ground:** fill the canvas with the ground colour. Draw 1px grid lines every `24s` px (minimum
  12) on both axes, starting at 0. This is the app's 24px grid.
- **Shadow:** the panel rect offset by `(12s, 12s)`, filled with the shadow colour, with no blur
  and square corners. It is drawn first.
- **Panel:** outer rect from `(40s, 40s)` to `(W-40s-12s, H-40s-12s)`. Fill it with the panel
  colour and draw a `6s` border in the border colour. Corners are square.
- **Content box:** the panel inset by border plus `48s` padding.
- **Headline:** Archivo ExtraBold in the text colour at `x = content.left`, starting at
  `content.top`. Line height is `1.08 x size` and there are at most 4 lines. Size starts at
  `72s` and steps down by `2s` to a minimum of `40s`.
- **Fact (supported highlight, `highlight_fact_ids` non-empty):** starts `28s` below the
  headline's last line box. Archivo Medium, line height `1.4 x size`, at most 4 lines. Size starts
  at `32s` and steps down by `2s` to a minimum of `22s`. Wrap width is
  `content.width - 2*10s`, and text is drawn at `x = content.left + 10s`.
  - Behind each line, draw a marker band filling the line box inset vertically by
    `round(0.1 x line_height)` top and bottom. It runs horizontally from `content.left` to
    `content.left + 10s + getlength(line) + 10s`. Corners are square. The fill is the marker
    colour and the text uses the text-on-marker colour.
- **Summary (highlight present but `highlight_fact_ids` empty):** same sizes and position, but
  `x = content.left`, wrap width `content.width`, muted colour, line height `1.35 x size`, and
  no band.
- **Footer:** pinned to the bottom of the content box. From the top down: a `3s` rule in the
  border colour across the full content width, a `16s` gap, then one line at `22s`.
  - Left: `Source: {label}` in Archivo Medium, muted colour.
  - Right-aligned: the wordmark `Topic Studio` in Archivo ExtraBold, accent colour.
  - Ellipsise the source with `…` so it stops `24s` before the wordmark. With no label, the
    left side is empty.
  - `footer_top` is the top of the rule. Keep at least `28s` between the last body line box and
    `footer_top`.

Note: at a 360px phone width the card shows at about 0.27x, so the footer is decorative there.
The alt text carries the full content.

**Supported sizes** (constants in `card.py`):

- `CARD_LANDSCAPE = (1200, 675)`: the default 16:9 social card.
- `CARD_SQUARE = (1080, 1080)`.
- `CARD_MIN = (640, 360)`: the smallest mobile/social size allowed.
- The constructor raises `ValueError` for a width below 640, a height below 360, or either side
  above 2048.

### Layout algorithm (pure, deterministic)

`layout_story_card(brief, theme, width, height) -> CardLayout` does not draw; it raises
`MediaContentUnsupported` per the text policy. It returns frozen dataclasses:

- `Box(left, top, right, bottom)`
- `TextLine(text, x, y, ink: Box)`, where `ink` comes from `ImageDraw.textbbox(..., anchor="la")`
  on a 1x1 scratch image
- `TextBlock(font_size, line_height, lines)`
- `CardLayout(width, height, theme, content_box, footer_top, headline: TextBlock,
  body: TextBlock | None, body_is_fact: bool, bands: tuple[Box, ...], source: TextLine | None,
  wordmark: TextLine, truncated: bool)`

Steps:

1. **Wrap.** Greedy word wrap on single spaces, measured with `font.getlength`. A word wider than
   the line breaks at character boundaries with no hyphen.
2. **Fit.** Try headline sizes from largest to smallest. For each, try body sizes from largest to
   smallest. Take the first pair where both blocks stay within their line limits and the total
   height fits above `footer_top - 28s`.
3. **Truncate only if nothing fits.** Use the minimum sizes. Drop body lines from the end and end
   the last kept line with `…`, trimming words until it fits the width. If no body line fits,
   leave the body out. If the headline still needs more than 4 lines, keep 4 and end the 4th with
   `…`. Set `truncated=True`. The alt text always carries the full text.
4. **Invariant (asserted, a bug if broken).** Every `ink` box and every band lies inside
   `content_box`. Headline and body ink ends above `footer_top`. Body ink starts below headline
   ink. Bands do not overlap each other.

`draw_story_card(layout) -> Image.Image` draws in RGB mode only, in this order: ground, grid,
shadow, panel, bands, text, rule, footer. `encode_png(image) -> bytes` calls
`image.save(buf, "PNG", optimize=True)` with no text chunks or metadata, so identical input gives
identical bytes.

### Renderer and registry

- `StoryCardRenderer(theme: CardTheme = CardTheme.LIGHT, width: int = 1200, height: int = 675)` in
  `media/card.py`.
  - `CardTheme(StrEnum)` has `LIGHT="light"` and `DARK="dark"`.
  - `name = "story-card"`.
  - `version = f"{CARD_TEMPLATE_VERSION}-{theme}-{width}x{height}"` with
    `CARD_TEMPLATE_VERSION = "1"`, so theme and size change the artifact ID. Bump the template
    version whenever layout, fonts or tokens change.
  - `render_static(brief)` returns `RenderedMedia(png, MediaType.PNG, width, height, None)`.
  - `render_animated(brief)` raises `MediaContentUnsupported("animation not available")`. Task 060
    replaces this.
- `media/providers.py` gets two additions:
  - `class MediaContentUnsupported(Exception)`. It is deliberately **not** a `MediaRenderError`,
    so it is never retried.
  - `MediaProviderName.STORY_CARD = "story-card"`, registered with a lazy factory (same pattern
    as the fake) that returns `StoryCardRenderer()`. The fake stays.
- `media/activities.py` maps `MediaContentUnsupported` to
  `ApplicationError(type="MediaRenderInvalid", non_retryable=True)`. `story_media.py` is
  unchanged: no card gives `TEXT_ONLY`, and no GIF gives `STATIC_FALLBACK`.
- `media/illustration.py` holds the optional AI-illustration interface, separate and unused:
  - `IllustrationProvider` Protocol: `name`, `version`, `illustrate(brief) -> Illustration`.
  - `Illustration` frozen dataclass: `data`, `media_type`, `width`, `height`, `provenance: str`,
    `licence: str`.
  - `ILLUSTRATION_PROVIDERS: Mapping[str, Callable[[], IllustrationProvider]] = {}`.
  - `card.py` does not import this module.

### Web preview of a manifest-approved artifact

- `services/media.py` adds `StoryMediaPreviewService(details: StoryDetailService, media:
  MediaService)` with `async preview(detail_id, artifact_id, *, now) -> ServedArtifact`.
  - It raises `MediaArtifactNotFound` when the artifact ID does not match `ARTIFACT_ID_PATTERN`,
    when the detail is missing, expired or malformed, when `package` is `None` or `package.media`
    is `None`, or when `MediaService.artifact` raises.
  - It lets `StoryDetailUnavailable` propagate.
  - It never takes a path, filename or MIME type from the caller.
- `web/dependencies.py` adds `get_story_media_preview_service`, built from
  `get_story_detail_service` and `get_settings`.
- `web/routes/stories.py` adds `GET /stories/{detail_id}/media/{artifact_id}`, which calls only
  `preview`.
  - On success it returns `FileResponse(path, media_type=served.media_type)` with these headers:
    `X-Content-Type-Options: nosniff`, `Content-Security-Policy: default-src 'none'` and
    `Cache-Control: private, max-age=3600`.
  - `MediaArtifactNotFound` returns 404 and `StoryDetailUnavailable` returns 503, both as plain
    text with no path in the body.
  - Auth is the same as the sibling `/stories/...` GET routes.
- No template, view-model or CSS change. `/stories/{id}` does not show the image yet (task 061).

## Acceptance criteria

- [ ] `StoryCardRenderer` produces a valid PNG (decodes with Pillow, RGB, exact requested size,
      `<= MAX_STATIC_BYTES`) for short, long (160-char headline, 280-char fact), summary-only,
      no-highlight and Unicode (accented Latin, Vietnamese, curly quotes, em dash, ellipsis, emoji)
      briefs, in both themes, at 1200x675, 1080x1080 and 640x360.
- [ ] Text never clips or overflows: in every case all `CardLayout` ink boxes and bands are inside
      the content box, body blocks end above the footer, and blocks do not overlap. Headlines with
      unsupported letters (CJK, Cyrillic) raise `MediaContentUnsupported`. Emoji are dropped, and
      unsupported fact or source text is omitted.
- [ ] Output is deterministic: the same brief, theme and size give byte-identical PNGs and the
      same `artifact_id`, and a different theme or size changes the ID. Renders match the
      committed golden PNGs within tolerance. Marker bands appear only for fact highlights.
- [ ] The renderer reads only the vendored Archivo fonts (no network, no system fonts, licence
      committed). Markup-like text renders literally. Card colours equal the `app.css` tokens, and
      every text pair meets 4.5:1.
- [ ] `renderer_for("story-card")` returns the card renderer. Through `StoryMediaActivities` a card
      is stored as a `STATIC` PNG artifact, and `MediaContentUnsupported` becomes non-retryable
      `MediaRenderInvalid`.
- [ ] `GET /stories/{id}/media/{artifact_id}` serves only an artifact listed in that story's
      manifest, with the allowlisted MIME type and `nosniff`. Unknown, malformed or foreign IDs,
      missing files, stories without media and missing stories return 404. `just lint` and
      `just test` pass.

## Files to touch

- `pyproject.toml`, `uv.lock` (`uv add pillow`)
- `src/ai_trend_pipeline/media/fonts/Archivo-ExtraBold.ttf`, `Archivo-Medium.ttf`, `OFL.txt`,
  `README.md` (new)
- `src/ai_trend_pipeline/media/fonts.py` (new: font paths, cached loading, `supported_codepoints`)
- `src/ai_trend_pipeline/media/card.py` (new: themes, sizes, text policy, layout, draw, encode,
  renderer)
- `src/ai_trend_pipeline/media/illustration.py` (new)
- `src/ai_trend_pipeline/media/providers.py` (`MediaContentUnsupported`, `STORY_CARD` entry)
- `src/ai_trend_pipeline/media/activities.py` (map `MediaContentUnsupported`)
- `src/ai_trend_pipeline/services/media.py` (`StoryMediaPreviewService`)
- `src/ai_trend_pipeline/web/dependencies.py`, `src/ai_trend_pipeline/web/routes/stories.py`
- `tests/fixtures/media/cards/*.png` (new goldens), `tests/regen_card_goldens.py` (new script,
  run by hand: `uv run python -m tests.regen_card_goldens`)
- `tests/test_media_fonts.py`, `tests/test_story_card_layout.py`,
  `tests/test_story_card_renderer.py`, `tests/test_web_story_media.py` (new)
- `tests/test_media_activities.py`, `tests/test_media_service.py` (add cases)
- `docs/architecture/tech.md` (card renderer, fonts and preview route in the media note),
  `README.md` (one line on the media preview route)

## Tests to write

`tests/test_media_fonts.py`
- `test_supported_codepoints_covers_latin_and_punctuation`: `A`, `é`, `ł`, `ạ`, `—`, `“`, `…` and
  `€` are in both fonts.
- `test_supported_codepoints_excludes_cjk_cyrillic_and_emoji`: `中`, `Ж` and `😀` are absent.
- `test_fonts_load_from_package_directory`: paths are under `media/fonts/`, and `OFL.txt` exists.

`tests/test_story_card_layout.py` (parametrized over 1200x675, 1080x1080 and 640x360, and both
themes)
- `test_ink_and_bands_stay_inside_content_box_and_above_footer`: short, long, summary,
  no-highlight and Unicode briefs.
- `test_long_text_shrinks_before_truncating`: a medium brief fits without `truncated`. The maximum
  brief at 640x360 may set `truncated`, and its cut lines end with `…`.
- `test_unbreakable_long_word_is_split_without_overflow`.
- `test_fact_gets_marker_bands_and_summary_does_not`: band count equals fact line count, or 0
  for a summary.
- `test_missing_highlight_renders_headline_and_footer_only`.
- `test_unsupported_headline_letters_raise`: CJK and Cyrillic.
- `test_emoji_dropped_and_unsupported_fact_or_source_omitted`.
- `test_markup_like_text_is_kept_literally`: `**bold** &amp;` appears verbatim in the lines.
- `test_constructor_rejects_sizes_below_minimum_or_above_max`.

`tests/test_story_card_renderer.py`
- `test_render_static_returns_valid_png_of_requested_size_within_byte_limit`: parametrized over
  sizes and themes.
- `test_render_is_byte_identical_for_same_input`.
- `test_matches_golden_images`: 7 goldens (`light_fact_1200x675`, `light_long_1200x675`,
  `light_summary_1200x675`, `light_unicode_1200x675`, `dark_fact_1200x675`,
  `light_long_640x360`, `light_long_1080x1080`).
  - Size and mode must match exactly.
  - At most 0.1% of pixels may differ by more than 8 on any channel.
- `test_version_and_artifact_id_change_with_theme_and_size`.
- `test_render_animated_raises_content_unsupported`.
- `test_render_makes_no_network_calls`: monkeypatch `socket.socket.connect` to raise; the render
  still succeeds.
- `test_card_theme_colours_match_app_css_tokens`: reuse `theme()` from
  `tests/test_design_tokens.py`.
- `test_card_theme_text_pairs_meet_wcag`: headline, summary/source and wordmark text against the
  panel, and marker text against the marker, are each at least 4.5:1. The border against the
  ground is at least 3:1.
- `test_registry_returns_story_card_renderer`: `renderer_for("story-card")`.
- `test_illustration_registry_is_empty_and_unused`: `ILLUSTRATION_PROVIDERS == {}` and `card.py`
  does not import `illustration`.

Existing files
- `tests/test_media_activities.py`:
  - `test_story_card_render_stores_static_png_artifact`: a real card written to `tmp_path`.
  - `test_content_unsupported_is_non_retryable_invalid`.
- `tests/test_media_service.py`: `test_preview_resolves_only_manifest_listed_artifacts`,
  covering a foreign-manifest ID, a malformed ID, `package=None` and `media=None`.

`tests/test_web_story_media.py` (dependency overrides, fake detail service, `tmp_path` media dir)
- `test_serves_manifest_artifact_with_mime_and_nosniff`.
- `test_unknown_malformed_or_foreign_artifact_id_is_404`.
- `test_missing_file_or_text_only_story_is_404`.
- `test_missing_story_is_404_and_unavailable_is_503`.
- `test_path_like_artifact_ids_are_404`: `..%2F..%2Fetc%2Fpasswd` and `x.png`.

## Out of scope

- Animated GIF rendering, frames, posters or the animation activity timeout policy (task 060).
- Starting `StoryMediaWorkflow` from `StoryGenerationWorkflow` or registering it in the worker.
  Also out: a `MEDIA_PROVIDER` setting, manifests on real stories, and showing the image on
  `/stories/{id}` (all task 061).
- Any template, CSS or view-model change, or using Archivo in the web app. No WebP output.
- Any `IllustrationProvider` implementation, LLM-written briefs, stock or remote images,
  scraping, system or remote fonts, and a second font family for Greek, Cyrillic or CJK.
- New settings or env vars, publishing, Bluesky, chart rendering, and media cleanup.
- Right-to-left or complex-script shaping (libraqm). Such headlines fall back to text-only.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: Re-planned plan row 058 (repo 059) with the `frontend-design` skill in context. This replaces the earlier approved draft, so the status is back to `planned`.
Design changes: Inter is replaced by Archivo (vendored, OFL). The all-caps `STORY` chip and the accent bar are gone. The shadow is now ink. Yellow is used only for highlighter bands behind a verified fact; a summary fallback stays plain. Trade-off: Archivo has no Greek or Cyrillic, so those headlines fall back to text-only and emoji are dropped.
Engineering is unchanged: Pillow with BASIC layout, a `struct` cmap parser, the preview route, non-retryable `MediaContentUnsupported`, and seven goldens. It is one PR.
Next: the orchestrator re-approves (row is `no`), then the builder vendors the Archivo TTFs and records their SHA-256.

**builder**: Replaced the stale Inter draft with the Archivo design: vendored ExtraBold/Medium (repo has no releases, so pinned commit 2111276), `card.py` rewritten (marker bands, ink shadow, text policy, 4-line caps), tests and 7 goldens regenerated.
Preview route, service, activity mapping and registry were already in the tree and kept. Docs: tech.md and README updated.
Unsure: unicode golden uses Latin/Vietnamese plus emoji only; ran only the touched test files plus ruff and pyright (clean).

**verifier**: Ran `just lint` (ruff check, ruff format --check, pyright: clean) and `just test` (1190 passed) with ANTHROPIC_* unset.
All checks pass.
Next: reviewer.

**reviewer**: Approved. Diff matches spec scope; all listed tests exist and the 121 touched-area tests pass. Workflow code is untouched, the renderer is pure and deterministic, and the activity maps MediaContentUnsupported to non-retryable.
Note: README media paragraph is one long line; fine but could be rewrapped.
Next: scribe.

**scribe**: Wrapped the README media paragraph and pushed. Opened PR https://github.com/akonale/ai-trend-pipeline/pull/68 against main with `gh pr create`, since the relay uses gh.
Status set to pr-open. Next: human merges PR #68.
