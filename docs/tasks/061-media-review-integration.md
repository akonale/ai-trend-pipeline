# 061: Media generation and final-review integration

Status: reviewed

> Plan row **060** in `docs/mvp-plan.md` ("Media generation and final-review integration"),
> renumbered to **061** (repo numbers are plan row + 1, see `docs/tasks/058-media-foundation.md`).
> It builds on repo 058 (contracts, `StoryMediaWorkflow`, storage, preview service), 059 (static
> card, preview route) and 060 (GIF, `StoryMediaView`). Plan rows 061-063 are repo tasks
> **062-064**, and the cross-references below use the repo numbers.

## Goal

After a story passes checking and before `review`, `StoryGenerationWorkflow` starts and awaits
the `StoryMediaWorkflow` child, attaches its manifest to the `StoryPackage`, and computes one
content hash over text and media that final approval freezes. `/stories/{id}` shows the exact
image or GIF (poster for reduced motion), its alt text, and any fallback, with no new primary status.

## Design

### Pure helpers (`media/briefs.py`)

- `story_package_hash(package: StoryPackage) -> str`: SHA-256 hex of canonical JSON of
  `package.model_dump(mode="json")`, **including** `media` (same `canonical_json` and
  `sha256_hex` as `story_content_hash`). This is the approved content hash. Any change to text,
  manifest, artifact hash or alt text changes it.
- `package_matches_hash(package, content_hash) -> bool`. Repo 062-063 use it before publishing.
- `text_only_manifest(package, facts) -> MediaManifest`: `TEXT_ONLY` with
  `story_content_hash(package)` and `media_brief_hash(media_brief_for(package, facts))`. It is
  used when the child cannot run.

### Workflow changes (`story_generation.py`)

- New internal status `StoryGenerationStatus.MEDIA = "media"`. Add it to `_WRITING_STATES` in
  `story_status.py`, `_STORY_OPEN_STATUSES` and the `StoryFilter.WRITING` visibility predicate in
  `services/stories.py`. It shows the `writing` badge everywhere.
- Constants: `STORY_MEDIA_PATCH = "story-media-v1"`, `MEDIA_CHILD_TIMEOUT = timedelta(minutes=6)`
  (static 3 x 60 s plus animated 2 x 45 s plus backoff fit), and the detail strings:
  - `MEDIA_DETAIL = "Making the story card."`
  - `MEDIA_REVIEW_DETAILS: dict[MediaOutcome, str]`:
    - `GIF`: `"Story card and animation are ready."`
    - `STATIC`: `"Story card is ready."`
    - `STATIC_FALLBACK`: `"The animation could not be made, so the still card is used."`
    - `TEXT_ONLY`: `"No story card could be made. The story is text only."`
- In both `_review` call sites, the passed package goes through a new
  `_with_media(package, facts)` step before `_review`:
  1. `if not workflow.patched(STORY_MEDIA_PATCH): return package` (old histories replay unchanged).
  2. `_transition(MEDIA, MEDIA_DETAIL)`.
  3. `execute_child_workflow(StoryMediaWorkflow.run, StoryMediaInput(story_id=package.story_id,
     package=package, facts=facts), id=media_workflow_id_for(package),
     task_queue=STORY_MEDIA_TASK_QUEUE, execution_timeout=MEDIA_CHILD_TIMEOUT)`. Import
     `StoryMediaWorkflow` and the queue through `imports_passed_through`.
  4. On `ChildWorkflowError` (failure, timeout, termination) or `WorkflowAlreadyStartedError`
     (an ID clash, so no second child starts), re-raise if cancelled. Otherwise log
     `ACTIVITY_FAILED`-style with `activity="story_media"` and use `text_only_manifest(...)`.
  5. Return `package.model_copy(update={"media": manifest})`.
  6. If `_deadline_passed()` afterwards, expire (same rule as the other steps).
- `_review` sets `self._package = package` and `self._content_hash = story_package_hash(package)`
  **before** `_transition(REVIEW, ...)`. Today `_package` is only set in `_finish`, so the review
  query returns `package=None`. Fix that on both paths; query state is not a history command. On
  the patched path the REVIEW transition passes `MEDIA_REVIEW_DETAILS[outcome]`. On the old path
  it passes no detail, as today.
- `StoryGenerationState` and `StoryGenerationResult` gain `content_hash: str | None = None`
  (pattern `^[0-9a-f]{64}$`). `_finish` keeps the hash for `ready` and `rejected` and clears it
  for the other terminal states.
- `FinalStoryDecision` gains `content_hash: str | None = None` (same pattern). In `_record`, an
  **approve** whose `content_hash` is set and differs from `self._content_hash` is ignored and
  logged as `IGNORED_SIGNAL` with `cause="stale_content"` (`reason` is a forbidden log field). `None` is still accepted so that
  recorded pre-media approve signals replay. Reject never needs a hash. Signals during `MEDIA`
  are ignored by the existing "not in REVIEW" rule.
- Nothing publishes. The child's bytes never enter the parent history (only the manifest).

### Service (`services/stories.py`)

- `StoryDetail` gains `content_hash: str | None = None`, filled from `state.content_hash` (review)
  and `result.content_hash` (ready).
- `DecideStoryRequest` gains `content_hash: str = ""`. For `approve`, `decide` raises new
  `StoryReviewStale(detail_id)` (subclass of `StoryDetailError`) **before** signalling when
  `request.content_hash != state.content_hash` (also when either is empty). Otherwise it passes
  the hash in `FinalStoryDecision`. Reject is unchanged.

### Route (`web/routes/stories.py`)

- `decide_story` reads `content_hash: Annotated[str, Form()] = ""` and passes it on.
  `StoryReviewStale` redirects 303 to `?decided=stale#story-notice`.
- `STORY_DETAIL_NOTICES["stale"] = "This story changed after you opened it. Nothing was decided. Review it again."`

### Settings and worker

- `config.py`: `media_provider: MediaProviderName = MediaProviderName.STORY_CARD`, read from
  `MEDIA_PROVIDER` (blank gives the default). An unknown value raises `ValueError` naming the
  registered providers (same style as `SOURCE_ENABLED`).
- `worker.py`:
  - `MEDIA_WORKFLOWS = [StoryMediaWorkflow]` and `MEDIA_WORKER_MAX_CONCURRENT_ACTIVITIES = 2`.
  - `media_activities(settings) -> list[Callable[..., Any]]` builds
    `StoryMediaActivities(Path(settings.media_dir), renderer_for(settings.media_provider),
    max_concurrent_animations=1)` and returns `[render_static_media, render_animated_media]`.
  - Add a fifth `Worker` on `STORY_MEDIA_TASK_QUEUE` with those workflows and activities and
    `max_concurrent_activities=MEDIA_WORKER_MAX_CONCURRENT_ACTIVITIES`, then add it to the gather.
    The activities are async (they render in `asyncio.to_thread`), so no executor is needed.
- No compose change. `MEDIA_DIR` and the shared volume already exist from 058.

### UI design plan (frontend-design skill in context)

**Subject and job:** the final-review page of one AI-trend story. The reviewer approves text and
image together, so the page must show the exact card that will ship, the words a screen reader
will hear, and plainly say when the image or animation is missing.

- **Colour:** use only existing `app.css` tokens. Surface `#ffffff` / dark `#262a44`, ink border
  `#15172b` / dark `#e3ecdf`, muted `#4a4f5e` / dark `#b4bacb`. There are no new hex values. The
  card already carries the page's one yellow element (its marker band), so the frame gets no
  yellow and no shadow.
- **Type:** the existing system body stack. The caption is `--text-sm` with line height 1.5. The
  first caption line (what the media is) is weight 700. The alt-text line is muted. The package
  code uses `font-variant-numeric: tabular-nums` in the body face. No monospace, no all-caps
  labels, no eyebrow.
- **Layout:** the figure is the first thing in the reading column, above the summary, at full
  column width. On desktop the reading column sits beside the 17rem rail; on a 360px phone it is
  full width. `width`/`height` attributes come from the poster, so the space is reserved and the
  page does not jump.

```
+-------------------------------------------+  +----------------+
| [ exact card image / GIF, 16:9         ]  |  | Final review   |
|-------------------------------------------|  | You approve    |
| Animated card. Plays once, 4.5 seconds.   |  | text and card  |
| (reduced motion: still card is shown.)    |  | together.      |
| Alt text: Story card: ...                 |  | Package 3f9a2c |
+-------------------------------------------+  | [Approve]      |
Summary ...                                    | [Reject]       |
Body ...                                       +----------------+
```

- **Principle:** this is a print proof, not a gallery. The card is the bold object. The frame is a
  plain ink mount (3px border, `--radius-sm`, no shadow), because the card already has its own
  panel and shadow and a second shadow would double it. The **absence** of media is drawn with
  a dashed border. Dashed means "nothing here", so text-only reads as a deliberate state, not a
  broken image.
- **Review against the brief:** my first draft reused `.chart-frame` (yellow shadow and axis
  ticks) and added a "MEDIA" eyebrow and a warning badge for fallbacks. I dropped the eyebrow
  (template chrome) and the badge (it would read as a sixth status). I dropped the chart frame
  because yellow would compete with the card's marker and the ticks mean "chart". Fallbacks are
  plain sentences in the caption.

**Markup** (`partials/story_detail_body.html`, inside `.story-detail__reading`, before the
summary; loops and ifs only):

```
{% if view.media %}
<figure class="story-media">
  <picture>
    {% if view.media.animated_src %}<source media="{{ view.media.motion_media }}" srcset="{{ view.media.animated_src }}" type="image/gif">{% endif %}
    <img src="{{ view.media.poster_src }}" width="{{ view.media.poster_width }}" height="{{ view.media.poster_height }}" alt="{{ view.media.alt_text }}" decoding="async">
  </picture>
  <figcaption>
    <p class="story-media__kind">{{ view.media.kind_text }}</p>
    {% if view.media.motion_note %}<p class="story-media__motion-note">{{ view.media.motion_note }}</p>{% endif %}
    <p class="story-media__alt">{{ view.media.alt_label }} {{ view.media.alt_text }}</p>
  </figcaption>
</figure>
{% elif view.media_missing_heading %}
<section class="story-media story-media--none">
  <h2>{{ view.media_missing_heading }}</h2>
  <p>{{ view.media_missing_body }}</p>
</section>
{% endif %}
```

In the review rail, after `decision_note`, add `<p class="meta">{{ view.package_note }}</p>` and
`{% if view.package_code %}<p class="story-rail__code">{{ view.package_code_label }} {{ view.package_code }}</p>{% endif %}`.
Both approve and reject forms get `<input type="hidden" name="content_hash" value="{{ view.content_hash }}">`.
The ready rail shows the same code line.

**View model** (`web/view_models.py`):

- `StoryMediaView` gains `kind_text: str`, `motion_note: str = ""` and `alt_label: str = "Alt text:"`.
  `build_story_media_view` gains a keyword argument `outcome` handling:
  - `GIF`: `"Animated card. Plays once, {s:g} seconds."` (from `duration_ms`), with
    `motion_note = "Reduced motion is on, so the still card is shown."`
  - `STATIC`: `"Still card."`
  - `STATIC_FALLBACK`: `"Still card. The animation could not be made, so this still card is used."`
- `StoryDetailView` gains:
  - `media: StoryMediaView | None`.
  - `media_missing_heading` and `media_missing_body`. These are set only for a `TEXT_ONLY`
    manifest: `"No story card"` and `"No image could be made for this story. Approving it
    approves the text only."`. A package with `media=None` (old stories) shows neither.
  - `content_hash` (the full hash, for the form), `package_code` (its first 8 characters),
    `package_code_label = "Package"` and
    `package_note = "You approve the text and the story card together."`. The note is
    `"You approve the text only."` for text-only or no media.
- Media views are built only for `review` and `ready`.

**CSS** (`app.css`, in the story-detail block):

```
.story-media { margin: 0 0 var(--space-5); background: var(--color-surface);
  border: var(--border-width) solid var(--color-border); border-radius: var(--radius-sm); overflow: hidden; }
.story-media picture, .story-media img { display: block; width: 100%; max-width: 100%; height: auto; }
.story-media img { border-bottom: var(--border-width) solid var(--color-border); }
.story-media figcaption { padding: var(--space-3) var(--space-4); font-size: var(--text-sm); line-height: 1.5; }
.story-media__kind { margin: 0; font-weight: 700; }
.story-media__motion-note { display: none; margin: var(--space-1) 0 0; }
.story-media__alt { margin: var(--space-2) 0 0; max-width: 62ch; color: var(--color-muted); overflow-wrap: anywhere; }
.story-media--none { padding: var(--space-4); border-style: dashed; }
.story-media--none h2 { margin: 0 0 var(--space-2); font-family: var(--font-display); font-size: var(--text-base); font-weight: 800; }
.story-media--none p { margin: 0; max-width: 62ch; }
.story-rail__code { font-weight: 700; font-variant-numeric: tabular-nums; }
@media (prefers-reduced-motion: reduce) { .story-media__motion-note { display: block; } }
```

Contrast uses existing pairs: ink on surface 17:1 (dark 11:1), muted on surface 8.2:1 (dark
7.2:1), border on bg at least 3:1. The focus ring is unchanged. Nothing in the figure is focusable.

## Acceptance criteria

- [ ] Time-skipping tests with a media worker (fake renderer) show `checking -> media -> review`.
      `GIF`, `STATIC`, `STATIC_FALLBACK` and `TEXT_ONLY` manifests are attached to the review
      package with the matching review detail. A child that never finishes times out to
      `TEXT_ONLY`. A pre-existing running child with the same ID gives `TEXT_ONLY`, and no second
      child starts.
- [ ] Review never appears before media resolves: while in `media`, the badge is `writing` and
      approve/reject signals are ignored. The review query now returns the package (with media)
      and its `content_hash`. The review deadline still expires a story stuck in media.
- [ ] Final approval freezes the package: the `ready` result's `content_hash` equals
      `story_package_hash(result.package)`. An approve with a mismatched hash is ignored by the
      workflow and refused by the service (`StoryReviewStale`, redirect `?decided=stale`).
      Changing media, an artifact hash or alt text after approval makes `package_matches_hash`
      false.
- [ ] The recorded pre-media history `story_generation_v1_ready.json` still replays. The worker
      registers `StoryMediaWorkflow` and both media activities on `story-media-task-queue` with the
      configured renderer, 2 concurrent activities and 1 concurrent animation. `MEDIA_PROVIDER` is
      read only in `config.py`.
- [ ] `/stories/{id}` in review and ready renders the `<picture>` (GIF only under
      `(prefers-reduced-motion: no-preference)`, poster `<img>` with width, height and alt), a
      caption with kind and alt text, a dashed "No story card" box for text-only, and nothing for
      old stories without media. All media URLs are manifest-derived `/stories/{id}/media/...`.
- [ ] The page has no horizontal scroll at 360px (img `max-width: 100%`) and works in light and
      dark. Existing story, candidate, chart and media tests still pass, and `just lint` and
      `just test` pass.

## Files to touch

- `src/ai_trend_pipeline/media/briefs.py` (`story_package_hash`, `package_matches_hash`,
  `text_only_manifest`)
- `src/ai_trend_pipeline/story_generation.py` (status, patch, child call, review package fix,
  `content_hash` fields, decision check)
- `src/ai_trend_pipeline/story_status.py` (`media` is a writing state)
- `src/ai_trend_pipeline/services/stories.py` (open statuses, WRITING predicate, `content_hash`,
  `StoryReviewStale`)
- `src/ai_trend_pipeline/config.py` (`media_provider`, `MEDIA_PROVIDER`)
- `src/ai_trend_pipeline/worker.py` (media worker, `media_activities`)
- `src/ai_trend_pipeline/web/routes/stories.py` (form hash, stale redirect)
- `src/ai_trend_pipeline/web/view_models.py` (`StoryMediaView` fields, detail view fields,
  notices)
- `src/ai_trend_pipeline/web/templates/partials/story_detail_body.html`
- `src/ai_trend_pipeline/web/static/app.css`
- `tests/story_generation_fixtures.py` (helper that also runs a media worker with
  `FakeMediaRenderer` and a `tmp_path` media dir; existing generation tests use it)
- `tests/fixtures/histories/story_generation_v2_media_ready.json` (new, recorded from the new
  code through `ready` with a GIF manifest)
- `tests/test_story_generation_media.py` (new); add cases to `tests/test_story_generation_replay.py`,
  `tests/test_media_briefs.py`, `tests/test_story_status.py` (or wherever the mapping is tested),
  `tests/test_story_detail_service.py`, `tests/test_web_story_detail.py`,
  `tests/test_story_media_view.py`, `tests/test_config.py` and `tests/test_worker.py`
- `docs/architecture/tech.md` (media step in the story flow, `MEDIA_PROVIDER`, media worker),
  `README.md` (review shows the story card; `MEDIA_PROVIDER`)

## Tests to write

`tests/test_story_generation_media.py` (time skipping; the parent worker plus a media worker)
- `test_gif_static_fallback_and_text_only_manifests_reach_review`: parametrized by fake renderer
  failures. Checks the review package's `media.outcome` and the review `status_detail`.
- `test_static_outcome_when_child_returns_static`: a test-double media workflow returns `STATIC`.
- `test_media_child_timeout_gives_text_only`: a test-double `StoryMediaWorkflow` that never
  returns.
- `test_running_child_with_same_id_gives_text_only_without_second_start`.
- `test_status_is_media_and_signals_are_ignored_until_media_resolves`: a gated test-double child.
- `test_review_query_returns_package_with_media_and_content_hash`.
- `test_ready_result_freezes_content_hash`.
- `test_approve_with_mismatched_hash_is_ignored_then_matching_hash_approves`.
- `test_review_deadline_expires_story_stuck_in_media`.
- `test_parent_history_contains_no_media_bytes`.

Existing files
- `test_story_generation_replay.py`: `test_pre_media_history_still_replays_without_child` (existing
  test kept) and `test_media_history_replays`.
- `test_media_briefs.py`: `test_story_package_hash_includes_media_and_alt_text`,
  `test_package_matches_hash_detects_post_approval_media_change` and
  `test_text_only_manifest_is_valid_and_deterministic`.
- Status mapping: `test_media_maps_to_writing` (primary status and WRITING filter predicate).
- `test_story_detail_service.py`: `test_detail_carries_content_hash_for_review_and_ready`,
  `test_approve_with_stale_or_missing_hash_raises_before_signal` and
  `test_reject_needs_no_hash`.
- `test_web_story_detail.py`: `test_review_renders_picture_with_poster_alt_and_gif_source`,
  `test_static_and_static_fallback_have_no_gif_source_and_show_fallback_text`,
  `test_text_only_shows_dashed_no_story_card_box`,
  `test_old_story_without_media_renders_no_media_section`,
  `test_decision_forms_post_content_hash` and `test_stale_decision_redirects_with_notice`.
  Also `test_media_urls_are_manifest_derived` (no path or filename from the package text) and
  `test_ready_shows_media_read_only_with_package_code`.
- `test_story_media_view.py`: `test_kind_text_and_motion_note_per_outcome`.
- CSS: `test_story_media_css_is_responsive_and_reduced_motion_aware` (img `max-width: 100%`, the
  motion note is shown only under `prefers-reduced-motion: reduce`, no fixed widths).
- `test_config.py`: `test_media_provider_default_env_and_unknown_value`.
- `test_worker.py`: `test_media_worker_registers_workflow_activities_and_concurrency`.

## Out of scope

- Publishing, a Publish button, `published` status, Bluesky or destination formatting (repo
  062-064). Approval still never publishes.
- LLM-written media briefs, new renderers, illustration providers, theme selection, or a
  per-story animation toggle setting.
- Regenerating or editing media after review, media cleanup, and showing media for `pending`,
  `writing` or `closed` stories.
- New primary statuses or badges, `/stories` list thumbnails, JavaScript, new fonts in the web
  app, or new colour tokens.
- Changes to the card/GIF renderers, their goldens, the media preview route or `MediaService`.
- Compose, Dockerfile or deploy changes.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: Wrote the spec for plan row 060 (repo 061). The `frontend-design` skill was in context. The design is a plain ink proof mount with no shadow and no yellow, a dashed box for text-only, and fallback text in the caption. There are no new tokens.
Found a latent bug: `_review` never sets `self._package`, so the live review query returns `package=None`. The spec fixes it. The approve hash check lives in the service (it refuses before signalling) because an ignored signal would hang `decide`.
It is one PR, but large (workflow, worker, config, service, UI). If needed, split it into workflow/worker/config first and UI/service second.
I could not create the branch or commit, because I have no shell tool. The spec is uncommitted in the main checkout. Next: the orchestrator creates `task/061-media-review-integration`, commits, and approves (row is `no`).

**builder**: Built the media step, content hash, service/route, worker, config, UI and docs. Replay of `story_generation_v1_ready.json` passes and `story_generation_v2_media_ready.json` (new, recorded) replays. The full suite and `just lint` pass.
Deviations: the stale-content log field is `cause` because `reason` is a forbidden story log field; `build_story_media_view` derives text from `manifest.outcome` and has no `outcome` kwarg. Existing generation tests and the e2e run a fake-renderer media worker (`with_media_worker`) and post `content_hash`.
Not done: screenshots, because headless Chromium lacks `libnspr4` here, so 360px and dark theme are checked only by CSS tests. Tests were written alongside the code, not strictly before it.

**verifier**: Ran `just lint` (ruff check, ruff format --check, pyright strict: all clean) and `just test` (1291 passed, 0 failed) on a clean full run with ANTHROPIC_* unset. Screenshots of /stories/{id} not done: the headless shell lacks libnspr4 here (same as builder), and a populated story needs the dev Temporal stack. All checks pass.

**reviewer**: Approved. Patch gating (`story-media-v1`) leaves old histories unchanged and both v1 and v2 replay fixtures exist; workflow code is deterministic. Stale approve is refused in the service before signalling (no hang) and ignored in the workflow. Deviations (`cause`, manifest-derived view text, shared media-worker helpers) are acceptable. `.claude/settings.json` is not in the diff.
Notes: spec says `reason="stale_content"` but `cause` is used (reason is a forbidden log field); `StoryReviewStale` is out of alphabetical order in `__all__`; screenshots (360px, dark) were not taken.
