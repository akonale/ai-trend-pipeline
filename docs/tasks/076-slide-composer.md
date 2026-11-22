# 076: Slide composer

Status: needs-fix

## Goal

Add a deterministic `SlideTemplate` registry in `media/carousel/templates/` with four templates
(hook, point, number, source). Each template takes one illustration PNG and a short overlay and
returns one square carousel slide PNG plus code-built alt text. It uses the story card's vendored
fonts, palette and text helpers. This is a pure library: it has no activity, no storage, no workflow
change and no settings. Task 078 wires it in as `compose_slides`.

## Design

**Independence.** 076 must not depend on 074 (fal) or 075 (`CarouselPlan`, which is not built). It
defines its own minimal input model. Later, 075/078 map a plan slide onto it. It does not import
from `media/images/` (073) either. Input is plain PNG bytes, so `GeneratedImage.png` or a fal
download both fit.

**Package `src/ai_trend_pipeline/media/carousel/`** (new; `__init__.py` is a one-line docstring).

`templates/models.py` holds frozen Pydantic models with `extra="forbid"`. They never cross a
Temporal boundary.

- `class SlideTemplateName(StrEnum)`: `HOOK = "hook"`, `POINT = "point"`, `NUMBER = "number"`,
  `SOURCE = "source"`.
- `SlideOverlay`:
  - `text: str`, 1 to `MAX_SLIDE_TEXT_CHARS = 200`. This is the main words: the hook line, the
    point, the number itself (for example `"40%"`, `"$2.1bn"`), or the source label.
  - `caption: str | None = None`, at most `MAX_SLIDE_CAPTION_CHARS = 140`. This is the line under
    the number, or an optional short line on the source slide. It is ignored by hook and point.
  - `position: int` (1 to 4) and `total: int` (2 to 4), with a validator requiring `position <= total`.
  - `image_description: str | None = None`, at most 160. It is a plain noun phrase, used only in
    alt text.
  - The text fields go through `clean_media_text` in a `mode="before"` validator, the same as
    `MediaBrief`. This rejects URLs and markup and collapses whitespace.
- `ComposedSlide`: `template: SlideTemplateName`, `png: bytes`, `width: int`, `height: int`,
  `alt_text: str` (1 to `MAX_ALT_TEXT_CHARS`), `truncated: bool`.

`templates/registry.py`:

- `class SlideTemplate(Protocol)`: `name: SlideTemplateName`, `version: str`, and
  `def compose(self, image_png: bytes, overlay: SlideOverlay) -> ComposedSlide`.
- `SLIDE_TEMPLATES: Mapping[SlideTemplateName, SlideTemplate]` holds stateless instances.
- `slide_template_for(name: SlideTemplateName | str) -> SlideTemplate`. An unknown name raises
  `UnknownSlideTemplateError(f"Unknown slide template {name!r}")` from `None`.
- `SLIDE_TEMPLATE_VERSION = "1"`. Each template's `version` is
  `f"{SLIDE_TEMPLATE_VERSION}-{name}-1080x1080"`, ready for artifact ids in 078.

`templates/frame.py` holds the shared drawing, text fitting, image fit, encoding and alt text.
`templates/slides.py` holds the four template classes. Do not edit `media/card.py`. Import
`CARD_THEMES[CardTheme.LIGHT]`, `Box`, `TextLine`, `clean_text`, `is_supported`, `wrap_text`,
`ellipsise`, `place_line` and `encode_png` from it. Import `load_font`, `HEADLINE_FONT_PATH`
(Archivo ExtraBold) and `BODY_FONT_PATH` (Archivo Medium) from `media/fonts.py`. Convert hex colours
with `PIL.ImageColor.getrgb`. Do not copy `card._rgb`.

**Input image.** Check it with `has_media_signature(data, MediaType.PNG)`. Open it with Pillow and
read `.size` *before* `.load()`. Each side must be 256 to 4096 px. Then convert to RGB: RGBA is
flattened onto paper `#ffffff`. Cover-fit it into the art box: scale with `Image.Resampling.LANCZOS`
so it fills the box, then centre-crop. Any failure (wrong signature, decode error, size out of
range) raises `MediaContentUnsupported` with a fixed message. Never include image bytes or overlay
text in the message.

**Text.** Run `clean_text` on all text. It drops emoji and symbols the fonts cannot draw. If the main
text is empty after cleaning, or `is_supported` is false (for example CJK), raise
`MediaContentUnsupported`. A caption that is empty or unsupported after cleaning is dropped, as the
card does with `_optional_text`. Fitting works like `layout_story_card`. Try sizes largest to
smallest and wrap with `wrap_text`, which already breaks very long single words. If nothing fits,
use the smallest size, keep the max lines, `ellipsise` the last line and set `truncated=True`. A
`_check` invariant asserts that all text ink sits inside the panel content box and above the footer
rule, and that blocks do not overlap.

**Size limit.** `encode_within(image, limit=MAX_STATIC_BYTES) -> bytes`. It first tries
`encode_png(image)`. If the result is larger than `limit`, it tries
`image.quantize(colors=c, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)` for `c` in
`(256, 128, 64)` and encodes each one. If all are still too large it raises
`MediaContentUnsupported("slide is too large")`. Every step is deterministic, so identical input
gives identical bytes.

**Alt text** (`slide_alt_text(template, overlay)`) is built only from overlay fields. It is cleaned
with `clean_media_text` and cut to `MAX_ALT_TEXT_CHARS`:

- hook and point: `"Slide {p} of {n}. {text}. Illustration: {description}."`
- number: `"Slide {p} of {n}. {text}: {caption}. Illustration: {description}."`
- source: `"Slide {p} of {n}. Source: {text}. {caption}. Illustration: {description}."`
- The `Illustration: …` sentence is left out when `image_description` is `None`, and the caption
  part is left out when there is no caption. Avoid a double full stop when the text already ends
  in `.`, `!` or `?`.

## Visual spec (all templates, 1080x1080 = `CARD_SQUARE`, light theme only)

Tokens come from `CARD_THEMES[CardTheme.LIGHT]`. Do not add new colours.

| Token | Value | Use |
| --- | --- | --- |
| ground / grid | `#e3ecdf` / `#d2dfcc` | background, 1 px grid lines every 24 px (same as the card) |
| panel | `#ffffff` | text panel fill |
| text (ink) | `#15172b` | headline text, 6 px borders, 12 px hard offset shadows, 3 px footer rule |
| muted | `#4a4f5e` | body and caption text, the counter, the "Source" label |
| accent | `#2448d8` | "Topic Studio" wordmark |
| marker / on_marker | `#ffc93c` / `#15172b` | number band and the number on it |

All of these pairs pass WCAG AA on their grounds (ink and muted on white, ink on marker, accent on
white at ≥ 4.5:1).

Frame, shared by all templates:

- Outer margin 40 px. The shadow is a 12 px solid ink offset (right and down), as on the card. The
  right and bottom edges of every boxed element stop at 1080 − 40 − 12 = 1028.
- **Art box**: `Box(40, 40, 1028, F)`. The image fills it edge to edge, with a 6 px ink border drawn
  on top and the shadow behind.
- **Text panel**: `Box(40, F + 28, 1028, 1028)`. It is white with a 6 px ink border and the shadow.
  The content box is inset 6 + 40 px.
- **Footer** is pinned to the bottom of the content box, like the card: a 3 px ink rule, a 16 px
  gap, then a 22 px line. On the left is the counter `"{p} / {n}"` (Medium, muted). On the right is
  the `Topic Studio` wordmark (ExtraBold, accent).
- Text sits above the footer, top-aligned, with a 24 px gap above the rule.

Per template:

| Template | F (art bottom) | Main text | Caption |
| --- | --- | --- | --- |
| hook | 640 | ExtraBold, ink, 64→40 px step 2, line height 1.08, max 3 lines | not drawn |
| point | 640 | Medium, ink, 44→28 px step 2, line height 1.3, max 4 lines | not drawn |
| number | 560 | ExtraBold 150→96 px step 6, one line only, on_marker on a marker band (10 px horizontal padding, band as wide as the text, the same band style as the card's fact bands) | Medium, muted, 34→26 px step 2, line height 1.3, max 2 lines, 20 px below the band |
| source | 640 | first a fixed `Source` label (Medium, muted, 26 px), then 12 px gap, then the label in ExtraBold, ink, 52→34 px step 2, line height 1.08, max 2 lines | optional, Medium, muted, 30→24 px step 2, max 2 lines, 16 px gap |

A number that does not fit on one line at 96 px raises `MediaContentUnsupported`. Do not ellipsise
a number. "Topic Studio", "Source" and the counter are template chrome, as on the card. Every other
word comes from `SlideOverlay`.

## Acceptance criteria

- [ ] `slide_template_for` returns a template for each of `hook`, `point`, `number`, `source`. The
      registry has exactly those four. An unknown name raises `UnknownSlideTemplateError`.
- [ ] Each template's output matches its golden PNG under `tests/fixtures/media/slides/` (≤ 0.1%
      differing pixels, the same tolerance as the card goldens). Output is 1080x1080 RGB PNG, and
      it is byte-identical when `compose` is called twice with the same input.
- [ ] Long text wraps and then ellipsises with `truncated=True`. Accented Latin and curly quotes
      render, emoji are stripped, and CJK main text raises `MediaContentUnsupported`. No text ink
      leaves the panel content box (`_check` passes for every case).
- [ ] Every composed slide is ≤ `MAX_STATIC_BYTES`, including one built from a noisy full-colour
      input. `encode_within` raises `MediaContentUnsupported` when even 64 colours exceed the limit.
- [ ] Bad image input (not PNG, truncated, smaller than 256 px or larger than 4096 px on a side)
      raises `MediaContentUnsupported` with a message that contains no overlay text.
- [ ] Alt text follows the formats above, uses only overlay words, and stays within
      `MAX_ALT_TEXT_CHARS`. No file outside "Files to touch" changes. `just lint` and `just test`
      pass.

## Files to touch

- `src/ai_trend_pipeline/media/carousel/__init__.py` (new)
- `src/ai_trend_pipeline/media/carousel/templates/__init__.py` (new; re-exports the public names)
- `src/ai_trend_pipeline/media/carousel/templates/models.py` (new)
- `src/ai_trend_pipeline/media/carousel/templates/registry.py` (new)
- `src/ai_trend_pipeline/media/carousel/templates/frame.py` (new)
- `src/ai_trend_pipeline/media/carousel/templates/slides.py` (new)
- `tests/slide_cases.py` (new). It holds the shared overlays, a deterministic `sample_art(width,
  height, seed)` drawn with Pillow flat shapes (no network, no fal fixture) and the golden case
  table.
- `tests/regen_slide_goldens.py` (new). Run it by hand as
  `uv run python -m tests.regen_slide_goldens`, the same way as `regen_card_goldens.py`.
- `tests/fixtures/media/slides/*.png` (new goldens)
- `tests/test_slide_templates.py` (new)
- Scribe: one short paragraph in `docs/architecture/tech.md` (media section)

## Tests to write

- Registry: each of the four names resolves, with matching `name` and `version`. There are no
  extras. `"nope"` and `""` raise `UnknownSlideTemplateError`.
- Goldens (parametrised): `hook`, `point`, `number`, `source`, `source-no-caption`,
  `point-long-truncated` and `hook-non-ascii` (`"Zürich café’s “naïve” Łódź plan — 😀"`). Each
  matches its golden at ≤ 0.1%.
- Determinism: for each template, two `compose` calls give identical `png` bytes.
- Input fit: a 1200x900 and a 900x1200 input both produce 1080x1080 output, cover-cropped (no
  ground colour visible in the art box corners).
- Text: a CJK main text raises `MediaContentUnsupported`. A main text that is all emoji raises. An
  unsupported caption is dropped and the slide still renders. A 200-character unbroken word wraps
  without escaping the content box. A 6-character number fits on one line, and a number that cannot
  fit at 96 px raises.
- Size: a slide built from seeded random-noise art is ≤ `MAX_STATIC_BYTES` and has a PNG signature.
  `encode_within(noise_image, limit=10_000)` raises `MediaContentUnsupported`.
- Bad input: non-PNG bytes, a truncated PNG, a 100x100 PNG and a 5000x10 PNG each raise
  `MediaContentUnsupported`. The message contains none of the overlay text.
- Models: `position > total`, `total` of 1 or 5, a URL in `text` and empty `text` are rejected.
- Alt text: the exact strings for each template, with and without a description or caption, and no
  double full stop.

## Out of scope

- The `compose_slides` activity, media storage, artifact ids, manifests and workflow patches (078).
- `CarouselPlan`, the art-director prompt, and checking numbers against facts (075). The critic (077).
- fal, `media/images/` changes and any `ImageBackend` change (074).
- Dark theme, other sizes or aspect ratios, animation, and per-story colour variation.
- Settings or kill switches (the carousel switch comes with 078). Review UI and publishing
  (079/080).
- Any edit to `media/card.py`, `media/fonts.py`, the card or illustration goldens, or existing tests.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: Spec for a pure-library slide composer: 4 templates, 1080x1080 light theme, reusing
card.py's palette, fonts and text helpers without editing them. It has its own `SlideOverlay` input,
so it is independent of 074 and 075, and it adds no activity or settings (078 wires it). Sizes are
kept within `MAX_STATIC_BYTES` by deterministic quantize fallbacks. Not a web UI task, so the
frontend-design skill was not loaded, but the visual tokens are given above. Next: orchestrator
approves (`no` task), then builder.

**builder**: Built `media/carousel/templates/` (models, registry, frame, slides), tests and 7 goldens; lint, pyright and the new test file pass.
The truncated golden uses wide uppercase words, since 200 normal chars fit in 4 lines at 28px. `slide_alt_text` falls back to whitespace collapse if joining false-hits the URL check (field ending in "www"). One `pyright: ignore` on Pillow's `resize` stub. Full suite left to the verifier.
**verifier**: `just lint` passed (ruff check, ruff format --check, pyright 0 errors). `env -u ANTHROPIC_BASE_URL -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_API_KEY uv run pytest` gave 1 failed, 1991 passed.
Failing test: `tests/test_story_generation_e2e.py::test_success_unsupported_rejection_retention_and_no_duplicates`, line 149, `assert expired_stories.stories == []`, with one StoryRow still listed (not a 076 file). The slide composer tests passed.
Next: builder or human to look at the failure, then re-run the verifier.
