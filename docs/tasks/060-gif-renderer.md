# 060: Short story GIF renderer

Status: built

> Plan row **059** in `docs/mvp-plan.md` ("Short engaging GIF renderer"), renumbered to **060**
> (repo numbers are plan row + 1, see `docs/tasks/058-media-foundation.md`). It builds on the
> media foundation (repo 058) and the static card renderer (repo 059,
> `docs/tasks/059-static-card-renderer.md`). Plan rows 060-063 are repo tasks **061-064**, and the
> cross-references below use the repo numbers.

## Goal

Make `StoryCardRenderer.render_animated` return a deterministic, bounded 4.5-second GIF built from
the 059 card scene. The headline wipes in, then a highlighter stroke sweeps across the fact, then
the source footer appears. The last frame is the static card, which the manifest already stores
as the poster. Also add a dedicated timeout, retry and concurrency policy for animated renders,
and a view-model rule that gives reduced-motion users the poster.

## Design

### Motion design plan

The `frontend-design` skill was in context. The subject is a share card for one AI-trend story,
seen in a Bluesky feed and on the review page. The GIF's job is to catch the eye in the first
second, show which line is the checked fact, and then stop on the exact static card.

- **Colour:** no new colours. Every frame uses only the 059 `CARD_THEMES` tokens and their
  antialiasing. There are no fades, glows or blurs, because alpha blends add colours outside the
  card palette and make the GIF bigger.
- **Type:** unchanged (Archivo ExtraBold and Medium, same layout from `layout_story_card`). The
  text does not move. Motion only reveals elements where they already sit.
- **Layout:** the same layout as the card in every frame. The opening frame shows the ground,
  grid, shadow, panel, footer rule and the `Topic Studio` wordmark, with an empty panel.
- **Principle (the one memorable thing):** the yellow marker band is drawn as a single eased
  highlighter stroke that runs left to right through the fact lines, one after another, like a
  pen. The text on the band is revealed with the stroke. The headline reveal and the footer reveal
  are plain constant-speed left-to-right wipes, so the stroke stays the special moment. There is
  no bounce, scale, slide, camera move or loop.
- **Review against the brief:** my first idea was a fade-up headline, a pulsing marker and an
  endless loop. I dropped the fade because it adds colours and is the generic default entrance.
  I dropped the pulse because it is decoration that flashes. I dropped the loop because an
  endlessly moving image needs a pause control (WCAG 2.2.2). The GIF plays **once** (no NETSCAPE
  loop extension), lasts at most 5 s and ends on the poster frame. Hard-edged wipes do not flash
  (WCAG 2.3.1).
- **Dark theme note:** fact text is ink (`#15172b`) on the marker. In dark theme, ink on the dark
  panel would be invisible, so the fact text must never appear before its band. That is why the
  text and the band are revealed together in one clip.

### Timeline (fixed, owned by code)

All values are constants in `media/animation.py`. Every duration is a multiple of 10 ms because
GIF stores centiseconds.

| Segment | Beat | Frames | Per frame | Extra hold on last frame |
| --- | --- | --- | --- | --- |
| open | always | 1 | 400 ms | - |
| headline wipe | `HEADLINE_REVEAL` | 12 | 80 ms | +400 ms |
| fact stroke | `FACT_EMPHASIS` | 10 | 80 ms | +300 ms |
| source wipe | `SOURCE_FOOTER` | 4 | 80 ms | - |
| final | always | (last frame of the last segment) | - | remainder up to the total |

- `GIF_DURATION_MS = 4500` is always the exact sum of all frame durations. The last frame gets
  `4500 - sum(all earlier durations)`, which is at least 1320 ms.
- The narrative order is fixed (headline, then fact, then footer). The order of
  `brief.animation_beats` is ignored, because the template owns timing (plan rule).
- A segment runs only if its beat is in `brief.animation_beats` **and** its element exists in the
  layout. The fact segment needs `layout.body`, and the source segment needs `layout.source`. An
  element whose segment does not run is drawn into the base scene from the open frame onwards.
- If no segment runs, raise `MediaContentUnsupported("nothing to animate")`.
- Progress for step `k` of `n`:
  - Headline and source: linear, `k / n`.
  - Fact stroke: ease-out, `1 - (1 - k / n) ** 2`.
  - Pixel positions are rounded to int.
- Merge identical frames: if a step's image equals the previous frame (`ImageChops.difference`
  bbox is `None`), add its duration to the previous frame instead of adding a new frame. This
  keeps the decoded frame count equal to the frame list (Pillow would merge them silently).
- Limits: `MAX_GIF_FRAMES = 40` (the template produces at most 27), asserted.
  `GIF_MAX_COLOURS = 64`. `MAX_GIF_SIDE = 1200`. `GIF_BYTE_BUDGET = 1_500_000`, which is below
  `MAX_ANIMATED_BYTES`. `GIF_RENDER_BUDGET_S = 20.0`.

### Frame construction (`media/animation.py`, pure apart from the clock)

`build_story_frames(layout: CardLayout, beats: Sequence[AnimationBeat], *, clock: Callable[[],
float] = time.monotonic, budget_s: float = GIF_RENDER_BUDGET_S) -> tuple[GifFrame, ...]`

- `GifFrame` is a frozen dataclass: `image: Image.Image` (RGB), `duration_ms: int`,
  `keyframe: Keyframe | None`. `Keyframe(StrEnum)` has `OPEN`, `HEADLINE_DONE`, `FACT_DONE` and
  `FINAL`. Tests find frames by keyframe, not by index.
  - `HEADLINE_DONE` and `FACT_DONE` mark the last frame of their segments when those segments run.
  - `FINAL` is always the last frame. When it coincides with another keyframe, `FINAL` wins.
- `full = draw_story_card(layout)` is the static card, unchanged from 059.
- `base` is `draw_story_card(dataclasses.replace(layout, ...))`. In it, every element whose segment
  runs is removed: the headline gets `lines=()`, the body becomes `None`, `bands` becomes `()` and
  the source becomes `None`. The rule and wordmark always stay.
- **Reveal strips.** For an element with lines `L0..Ln`, let `R` be the union of its line ink
  boxes, its bands and its line boxes (`y` to `y + line_height`).
  - Line `i` owns the vertical span `[top_i, top_(i+1))`, where `top_0 = R.top`,
    `top_i = L_i.y` for `i > 0`, and the last line ends at `R.bottom`.
  - Its width is `w_i = (band_i.right if fact else ink_i.right) - content.left`.
  - At revealed length `r = progress * sum(w)`, line `i` is clipped horizontally to
    `[content.left, content.left + clamp(r - sum(w_0..w_(i-1)), 0, w_i))`. A fully revealed line
    uses `content.right` as its right edge.
  - The source footer is one strip: from `footer_top + rule` to `content.bottom` vertically, and
    from `content.left` to `source.ink.right` horizontally. It never reaches the wordmark.
- **Compositing.** Each frame starts as a copy of the previous frame state (or `base`).
  `full.crop(strip)` is pasted for every revealed strip. Earlier segments stay fully revealed.
- **Final frame.** The last frame's image is `full` itself. Assert that the composited
  progress-1 state equals `full` (`ImageChops.difference(...).getbbox() is None`). If it does not,
  that is a strip bug.
- **Time budget.** Call `clock()` before the first frame and after each frame. If more than
  `budget_s` has passed, raise `MediaRenderError("animation exceeded its render time budget")`.
  This is retryable, because load can cause it.

`encode_gif(frames) -> bytes`:

- **Palette.** `palette = frames[-1].image.quantize(colors=GIF_MAX_COLOURS,
  method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)`. Every frame is converted with
  `frame.image.quantize(palette=palette, dither=Image.Dither.NONE)`.
- **Save.** Use `save(buf, "GIF", save_all=True, append_images=..., duration=[...], disposal=1,
  optimize=False)`, with **no** `loop`, `comment` or other metadata. Pillow's own frame-delta
  cropping is fine. All encoding happens in a `BytesIO`, never in a file.
- **Errors.** Any `OSError` or `ValueError` from quantize or save is re-raised as
  `MediaRenderError("gif encoding failed")`.
- **Byte budget.** If the result is over `GIF_BYTE_BUDGET`, raise
  `MediaContentUnsupported("gif exceeds byte budget")`.
- Identical frames give byte-identical output.

### Renderer changes (`media/card.py`)

- `GIF_TEMPLATE_VERSION = "1"`. The renderer `version` becomes
  `f"{CARD_TEMPLATE_VERSION}.{GIF_TEMPLATE_VERSION}-{theme}-{width}x{height}"`, so it covers both
  kinds. No real manifests exist yet, so the static ID change is harmless. Bump
  `GIF_TEMPLATE_VERSION` whenever the timeline, palette or strips change.
- `render_animated(brief)`:
  - If either side is over `MAX_GIF_SIDE`, raise `MediaContentUnsupported`. The outcome becomes
    `STATIC_FALLBACK`.
  - Otherwise lay out with `layout_story_card` (same text policy, so a CJK headline still raises),
    then call `build_story_frames(layout, brief.animation_beats)` and `encode_gif`.
  - Return `RenderedMedia(gif, MediaType.GIF, width, height, GIF_DURATION_MS)`.
  - Import `media.animation` inside the method, with a comment explaining the import cycle (the
    same pattern as `providers.py`).
- `render_static` and the 059 goldens are unchanged.

### Activity and workflow policy

- `StoryMediaActivities.__init__` gains `max_concurrent_animations: int = 1` (must be at least 1,
  else `ValueError`). `render_animated_media` holds an `asyncio.Semaphore` of that size around the
  `asyncio.to_thread` render call. Static renders are not limited. This stops CPU-heavy GIF
  renders from piling up on one worker. Worker options and registration stay in task 061.
- In `story_media.py`:
  - `ANIMATED_RENDER_TIMEOUT` becomes `timedelta(seconds=45)`.
  - Add `ANIMATED_RENDER_RETRY_POLICY = RetryPolicy(maximum_attempts=2,
    non_retryable_error_types=[...same two...])`, used only for the animated activity, because a
    deterministic render rarely succeeds on a third try.
  - The static policy is unchanged.
  - Fallback behaviour is unchanged: an animated failure gives `STATIC_FALLBACK` with the static
    poster.
  - Activity options are not part of replay determinism, and the workflow is not registered
    anywhere yet.

### Reduced-motion selection (`web/view_models.py`)

- `StoryMediaView(BaseModel)` has these fields: `poster_src: str`, `poster_width: int`,
  `poster_height: int`, `animated_src: str | None`, `alt_text: str`, and `motion_media:
  Literal["(prefers-reduced-motion: no-preference)"]`.
- `build_story_media_view(detail_id: str, manifest: MediaManifest | None) -> StoryMediaView |
  None`:
  - It returns `None` for no manifest or `TEXT_ONLY`.
  - The poster is always `static_artifact`. `animated_src` is set only for the `GIF` outcome.
  - URLs are `/stories/{quote(detail_id, safe='')}/media/{artifact_id}` (the 059 route). They are
    built only from manifest IDs.
  - `alt_text` comes from the static artifact.
- The contract for task 061's template is: `<picture>` with `<source media="{{ motion_media }}"
  srcset="{{ animated_src }}" type="image/gif">` and `<img src="{{ poster_src }}">`. With that
  markup, reduced-motion users and browsers that ignore `<source>` get the poster, with no
  JavaScript. This task adds no template or CSS.

## Acceptance criteria

- [ ] `renderer_for("story-card").render_animated(brief)` returns a GIF89a that Pillow decodes. It
      is exactly the requested size, at most 40 frames, with frame durations summing to exactly
      4500 ms, no loop extension, at most 64 colours in every frame, and at most
      `GIF_BYTE_BUDGET` bytes. This holds for the short, long, summary, no-highlight and Unicode
      briefs in both themes at 1200x675, 1080x1080 and 640x360.
- [ ] Keyframes follow the narrative order. In `OPEN`, the headline, fact and source strips are
      plain panel. In `HEADLINE_DONE`, the headline matches the final frame and the fact and
      source strips are still empty. In `FACT_DONE`, the bands and fact text are present and the
      source is still empty. During the stroke, the fact text never appears outside the revealed
      band. The last decoded frame matches the static PNG from `render_static` (at most 1% of
      pixels differ by more than 16 on any channel).
- [ ] Bounds are enforced. A side over 1200, output over the byte budget, an exceeded render time
      budget (fake clock) or an encoder exception each raise the specified error. Through
      `StoryMediaWorkflow`, an encoder failure gives `STATIC_FALLBACK` with the static poster
      after exactly 2 animated attempts.
- [ ] Output is deterministic. The same brief, theme and size give byte-identical GIFs, the same
      artifact IDs and, across two workflow runs, equal manifests. No temp files remain, either in
      the temp dir or as `.media-*.tmp` in the media root, after a successful or a failed render.
- [ ] Animated renders run at most `max_concurrent_animations` at a time per activity instance.
      The animated activity uses the 45 s timeout and the 2-attempt policy.
      `build_story_media_view` picks the poster as the `<img>` source and offers the GIF only
      under `(prefers-reduced-motion: no-preference)`. `just lint` and `just test` pass.

## Files to touch

- `src/ai_trend_pipeline/media/animation.py` (new: timeline constants, `Keyframe`, `GifFrame`,
  `build_story_frames`, `encode_gif`)
- `src/ai_trend_pipeline/media/card.py` (`GIF_TEMPLATE_VERSION`, `version`, `render_animated`)
- `src/ai_trend_pipeline/media/activities.py` (animation semaphore)
- `src/ai_trend_pipeline/story_media.py` (animated timeout and retry policy)
- `src/ai_trend_pipeline/web/view_models.py` (`StoryMediaView`, `build_story_media_view`)
- `tests/fixtures/media/gifs/light_fact_1200x675_{open,headline_done,fact_done,final}.png` and
  `dark_fact_1200x675_fact_done.png` (new goldens). Add the regeneration of these files to
  `tests/regen_card_goldens.py`.
- `tests/test_story_gif.py` (new), `tests/test_story_media_view.py` (new)
- `tests/test_story_card_renderer.py` (replace `test_render_animated_raises_content_unsupported`,
  and update the version test)
- `tests/test_media_activities.py`, `tests/test_story_media_workflow.py` (add cases)
- `docs/architecture/tech.md` (GIF renderer paragraph after the card renderer note), `README.md`
  (one line: story media can include a short GIF with a static poster)

## Tests to write

`tests/test_story_gif.py`
- `test_gif_is_valid_bounded_and_plays_once`: parametrized over briefs, themes and sizes. It
  checks size, frame count, a duration sum of 4500, no `loop` in `info`, colours per frame of at
  most 64, and bytes within the budget.
- `test_keyframes_follow_headline_fact_footer_order`: the strip checks per keyframe, using the
  layout's boxes.
- `test_fact_text_only_appears_with_its_band`: in every stroke frame, no ink-coloured pixel sits
  in the fact strip outside the band's revealed part (dark theme).
- `test_final_frame_matches_static_card`: within the stated tolerance.
- `test_missing_beats_or_elements_are_drawn_from_the_start`: no highlight means no fact segment
  and the total is still 4500. Without `SOURCE_FOOTER`, the source is already in `OPEN`.
- `test_identical_frames_are_merged`: the frame list has no two equal neighbours, and the decoded
  frame count equals `len(frames)`.
- `test_render_is_byte_identical_for_same_input`.
- `test_keyframes_match_golden_images`: 5 goldens, with the same tolerance rule as 059 (size and
  mode exact, at most 0.1% of pixels differ by more than 8).
- `test_oversized_side_raises_content_unsupported`: 1201x675.
- `test_byte_budget_exceeded_raises_content_unsupported`: monkeypatch `GIF_BYTE_BUDGET` to 1000.
- `test_render_time_budget_raises_render_error`: a fake clock that advances 30 s per call.
- `test_encoder_exception_becomes_render_error`: monkeypatch `Image.Image.save` to raise `OSError`.
- `test_no_temp_files_after_success_or_failure`: point `tempfile.tempdir` at `tmp_path`, then
  render once successfully and once with a failing encoder. The directory stays empty.
- `test_unsupported_headline_raises_for_animation`: a CJK headline.

`tests/test_story_card_renderer.py`
- `test_render_animated_returns_gif_with_duration`, which replaces the old "raises" test.
- `test_version_and_artifact_id_change_with_theme_and_size`: update it for the new version format.

`tests/test_media_activities.py`
- `test_story_card_render_stores_animated_gif_artifact`: a real GIF, `kind=ANIMATED`,
  `duration_ms=4500`, and a `.gif` file in `tmp_path`.
- `test_animated_renders_respect_concurrency_limit`: a slow fake renderer that counts concurrent
  entries. Two `render_animated_media` calls run with `asyncio.gather`, and the maximum observed
  concurrency is 1. Two static calls overlap.
- `test_max_concurrent_animations_must_be_positive`.

`tests/test_story_media_workflow.py` (time skipping, real `StoryCardRenderer`)
- `test_real_renderer_gif_outcome_has_poster_and_gif`.
- `test_encoder_failure_falls_back_to_static_after_two_attempts`.
- `test_real_renderer_same_package_produces_same_manifest`.
- `test_animated_activity_uses_dedicated_timeout_and_retry_policy`: assert the constants, and
  that the scheduled activity's options (from history) show a 45 s start-to-close and 2 attempts.

`tests/test_story_media_view.py`
- `test_gif_manifest_uses_poster_as_img_and_gif_only_for_motion_ok`.
- `test_static_and_static_fallback_have_no_animated_src`.
- `test_text_only_or_missing_manifest_gives_none`.
- `test_urls_are_built_from_manifest_ids_and_quoted_detail_id`.

## Out of scope

- Starting `StoryMediaWorkflow` from `StoryGenerationWorkflow`, worker registration and worker
  concurrency options, a `MEDIA_PROVIDER` setting, templates, CSS, showing media on
  `/stories/{id}`, and fallback status text (task 061).
- Any change to the static card layout, its tokens, fonts or 059 goldens.
- WebP, APNG, MP4 or video output. Browsers, JavaScript or headless rendering. New dependencies.
- Fades, looping, extra beats, LLM-chosen timing, colours or effects. Per-story timing variations.
- Publishing, Bluesky upload or capability fallback (tasks 062-063), and media cleanup.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: Wrote the spec for plan row 059 (repo 060). The `frontend-design` skill was in context, and the motion plan is in the spec: hard-edged wipes, one eased highlighter stroke, plays once, 4.5 s, ends on the poster, no new colours.
One PR: `media/animation.py`, `render_animated`, a semaphore in the activity, a 45 s and 2-attempt animated policy, and the reduced-motion view model (template in 061).
I could not create the branch or commit, because I have no shell tool. The spec file is uncommitted in the main checkout. The orchestrator must create `task/060-gif-renderer` from main and commit it.
Next: the orchestrator approves (row is `no`), then the builder writes the tests first.

**builder**: Built per spec: `media/animation.py`, `render_animated`, animation semaphore, 45 s and 2-attempt animated policy, `StoryMediaView`, goldens and docs. All new tests pass; ruff and pyright clean; full suite left to the verifier.
Existing workflow tests assumed 3 animated attempts, so I changed them to 2 (fail-always now counts 2; the recover test fails once).
Pyright-ignored imports of private `_px` and `_scale` from `card.py` (comment explains) to share pixel scaling.
