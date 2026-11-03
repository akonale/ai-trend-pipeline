# 069: Drawn images in the manifest and `StoryMediaWorkflow`

Status: approved

## Goal

Carry the drawn pictures in `MediaManifest` as an optional, view-only field. Run
`draw_story_images` in `StoryMediaWorkflow` behind a new patch, after the card succeeds and
alongside the GIF and illustration renders, and register it in the worker. Every old history
replays, and every pre-existing hash stays byte-identical.

## Context

- This task builds on 067 (contracts and settings) and 068 (`DrawnImageActivities`,
  `DRAW_ACTIVITY`, and the `DrawingTransient`/`DrawingFailed` error types).
- Precedent: 065a added `illustration_artifact`, `illustration_status` and `selected` with the same
  wrap serializer (`_OPTIONAL_MANIFEST_KEYS`) and the patch `story-illustration-v1`. Copy both
  patterns.
- `StoryGenerationWorkflow` awaits the media child with `MEDIA_CHILD_TIMEOUT = 6 min`. A child
  timeout makes the story text-only. So the drawing budget must keep the whole child safely under
  6 minutes (see the timeouts below).
- **Temporal workflow code in this task:** `src/ai_trend_pipeline/story_media.py` only. It must
  stay deterministic, with no I/O, clock, randomness or LLM call. It imports only
  `media/models.py` types (inside `imports_passed_through`), never `media/drawn/`.
  `story_generation.py` is **not** changed.

## Decisions

### Manifest (`media/models.py`)

- `MediaManifest` gains two fields:
  - `drawn_artifacts: tuple[DrawnArtifact, ...] = ()`, with `max_length=MAX_DRAWN_IMAGES`
  - `drawn_status: DrawnStatus | None = None`
- The wrap serializer drops `drawn_status` when it is `None`, and drops `drawn_artifacts` when it
  is empty (an empty list in JSON mode or an empty tuple in Python mode). Generalise the loop
  without changing the output for the existing keys.
- The validator (a new `_check_drawn`, called from `_check_manifest`) requires all of these:
  - `drawn_status` is never `disabled` in a manifest. Disabled is stored as `None`.
  - `drawn_artifacts` is non-empty if and only if `drawn_status is drawn`.
  - Drawings require `static_artifact`.
  - Roles are unique.
  - Every `artifact_id` across static, animated, illustration and drawn artifacts is distinct.
  - A `text_only` outcome has no drawn fields.
- `artifact(artifact_id)` also searches `drawn_artifacts`, so the existing route serves drawings
  with no route change.
- Add `review_gallery() -> tuple[DrawnArtifact, ...]`. It returns `drawn_artifacts` in
  `DRAWN_ROLE_ORDER` when the status is `drawn`, otherwise `()`. It is the only accessor the UI
  uses.
- **`candidates()` and `with_media_choice` do not change.** Drawings are never pickable, and they
  never affect `selected` or publishing. Say this in the `candidates()` docstring.
- Drawings are part of `story_package_hash`, because the reviewer approves what they saw. They are
  not part of `story_content_hash`, because the manifest is excluded there. Old manifests dump
  byte-identically.

### Workflow (`story_media.py`)

Constants:

- `DRAW_ACTIVITY = "draw_story_images"`
- `STORY_DRAWN_PATCH = "story-drawn-v1"`
- `DRAW_START_TO_CLOSE = 110s`
- `DRAW_SCHEDULE_TO_CLOSE = 150s`
- `DRAW_RETRY_POLICY = RetryPolicy(initial_interval=2s, maximum_attempts=2,
  non_retryable_error_types=["DrawingFailed"])`

Changes in `run`:

- **Start.** Directly after the existing illustration `patched` block, and before the GIF render:
  ```python
  drawing = None
  if workflow.patched(STORY_DRAWN_PATCH):
      drawing = workflow.start_activity(
          DRAW_ACTIVITY,
          DrawImagesInput(
              story_id=...,
              story_content_hash=story_content_hash(data.package),
              headline=brief.headline,
              summary=data.package.summary[:MAX_ILLUSTRATE_SUMMARY_CHARS],
          ),
          result_type=DrawnImagesResult,
          start_to_close_timeout=DRAW_START_TO_CLOSE,
          schedule_to_close_timeout=DRAW_SCHEDULE_TO_CLOSE,
          retry_policy=DRAW_RETRY_POLICY,
      )
  ```
  The drawing starts only when the static card succeeded. The text-only path returns before it, as
  it does for the illustration.
- **Await.** Await it after the illustration. A non-cancel `ActivityError` gives
  `DrawnImagesResult(status=failed)`. A cancel re-raises.
- **Fold into the manifest.** The `manifest()` helper gains a `drawn: DrawnImagesResult | None`
  argument:
  - `None` or `disabled`: no drawn fields
  - `drawn`: `drawn_status=drawn` and the images
  - `failed` or `capped`: that status and no images
- **Size guard.** If building the manifest with drawings raises `ValidationError` (for example,
  the manifest is over 8 KB with long non-ASCII alt text), rebuild it with
  `drawn_status=failed` and no images. This is pure and deterministic. A drawing must never stop
  the card manifest from being built.
- The drawing never changes `outcome`, `static_artifact`, `animated_artifact` or the illustration
  fields.
- **Timeout budget.** `DRAW_SCHEDULE_TO_CLOSE + RENDER_MAX_ATTEMPTS * STATIC_RENDER_TIMEOUT` is
  330s, which is under `MEDIA_CHILD_TIMEOUT` (360s). The GIF and illustration renders run in
  parallel and are shorter. Pin this with a test, and add a comment that points to it.

### Worker (`worker.py`)

`media_activities(settings, anthropic_client)` adds
`DrawnImageActivities(anthropic_client, Path(settings.media_dir),
enabled=settings.drawn_images_enabled, model=settings.drawn_images_model,
count=settings.drawn_images_count, daily_cap=settings.drawn_images_daily_cap).draw_story_images`.
`main()` passes the existing client. The activity is always registered. When the feature is off it
returns `disabled` at no cost, so the patch never schedules an activity that no worker serves.

### Test helpers

Every test helper that runs `StoryMediaWorkflow` must register a fake `draw_story_images`
(`@activity.defn(name="draw_story_images")`). It returns `disabled` by default, so existing
manifests and assertions stay unchanged. These helpers include `_run` in
`tests/test_story_media_workflow.py` and `tests/story_generation_fixtures.py`.

## Acceptance criteria

- [ ] A manifest with no drawn fields dumps byte-identically to the code before this task:
  - the two pinned literals in `tests/test_media_models.py` (`PRE_065_PACKAGE_HASH` and
    `PRE_065_GIF_PACKAGE_HASH`) are unchanged
  - a new pinned literal for an illustrated manifest with `selected=illustration` is unchanged.
    **Compute it on `main` before any source change and commit it first.**
- [ ] The manifest validator rejects each of these: a `disabled` status, `drawn` with no images,
  images with a status other than `drawn`, drawings without a card, duplicate roles or ids, and
  drawings on `text_only`.
  - `artifact()` finds drawn ids.
  - `review_gallery()` orders by role.
  - `candidates()` never contains a drawing, and `with_media_choice` leaves the drawn fields
    unchanged.
  - The worst-case ASCII manifest fits within `MAX_MANIFEST_JSON_BYTES`. That is three artifacts
    with 1000-character alt text, plus 4 drawings with 240-character alt text.
- [ ] Time-skipping workflow tests cover each fake activity outcome:
  - `drawn` puts the images in the manifest
  - `failed` and `capped` set the status and no images
  - `disabled` sets no drawn keys
  - a raised `DrawingTransient` twice gives `failed` after exactly 2 attempts
  - `DrawingFailed` gives `failed` after 1 attempt
  - an activity that never finishes gives `failed` at `schedule_to_close`

  In every case the card, GIF and illustration outcomes match the same run without drawing, and
  text-only stories never schedule `draw_story_images`.
- [ ] The history shows `draw_story_images` scheduled before the GIF and illustration complete,
  with `startToCloseTimeout` 110s, `scheduleToCloseTimeout` 150s, `maximumAttempts` 2 and
  `nonRetryableErrorTypes == ["DrawingFailed"]`. The history contains no `iVBOR` and no `<svg`.
- [ ] `story_media_v1_gif.json` and `story_media_v2_illustrated.json` replay with the new code. A
  new `story_media_v3_drawn.json`, recorded with the new code and a fake drawn result of 2 images,
  replays. All four story-generation histories in `test_story_generation_replay.py` still replay.
- [ ] `media_activities` registers `draw_story_images`, and the existing `test_worker.py` checks
  are updated. `just lint` and `just test` pass with no network.

## Files to touch

- `src/ai_trend_pipeline/media/models.py` (manifest fields, serializer, validator, `artifact()`,
  `review_gallery()`)
- `src/ai_trend_pipeline/story_media.py` (**Temporal workflow code**)
- `src/ai_trend_pipeline/worker.py`
- `tests/test_media_models.py`
- `tests/test_story_media_workflow.py`
- `tests/story_generation_fixtures.py` (register the fake activity)
- `tests/test_worker.py`
- `tests/fixtures/histories/story_media_v3_drawn.json` (new). Record it the same way as
  `story_media_v2_illustrated.json`.

## Tests to write

- `test_media_models.py`:
  - each validator rule above
  - the serializer drops the drawn keys when empty, and keeps them with the right shape when set
  - the three pinned package hashes
  - `artifact()` lookup of a drawn id
  - `review_gallery()` ordering, and `()` for `failed` and `capped`
  - `candidates()` and `with_media_choice` ignore drawings
  - the worst-case size fits
- `test_story_media_workflow.py` (time-skipping, fake draw activity):
  - drawn, failed, capped and disabled
  - transient raised twice gives 2 attempts and then `failed`
  - `DrawingFailed` gives 1 attempt
  - a hanging activity hits `schedule_to_close` and gives `failed`
  - text-only has no draw scheduled
  - the oversize manifest guard falls back to `failed` with the card intact
  - the history attribute assertions, and no `iVBOR` or `<svg` in the history
  - the timeout-budget invariant (`< MEDIA_CHILD_TIMEOUT`)
  - replay of v1, v2 and v3 media histories
- `test_worker.py`: `media_activities(Settings(...), client)` includes `draw_story_images`. Build
  the client with a dummy key and no network use.

## Out of scope

- Any UI, view model, template, CSS, README, `tech.md` or runbook change. That is task 070.
- Changes to `story_generation.py`, `MEDIA_CHILD_TIMEOUT`, `candidates()`, `selected`,
  `media_choice`, the formatter, `PublicationMedia`, `bluesky_story.py` or the media route.
- A redraw action, and regenerating drawings for existing stories.
- Changes to the 068 activity behaviour, except for bugs found while wiring. Report those in the
  handoff.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: wrote spec
