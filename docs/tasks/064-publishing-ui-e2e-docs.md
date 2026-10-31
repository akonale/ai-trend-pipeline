# 064: Publishing UI, end-to-end QA, and docs

Status: pr-open

> Plan row **063** in `docs/mvp-plan.md` ("Publishing UI, end-to-end QA, and docs"), renumbered to
> **064** (repo numbers are plan row + 1, see `docs/tasks/058-media-foundation.md`). This is the last
> task of the media and publishing series. It builds on repo 058-063: `publishing/` (formatter,
> registry, `publication_key`), `story_publishing.py` (`PublicationStatus`, `StoryPublishingState`,
> `FAILURE_DETAILS`), `services/publishing.py` (`StoryPublishingService.publish`),
> `TemporalStoryPublishingGateway`, and `StoryMediaView`. It also resolves the 063 carry-forward in
> `docs/architecture/tech.md` ("For 064: show the formatted post text during review").

## Goal

Add an explicit **Publish to Bluesky** action to Ready stories. The page shows publishing progress
under the Ready badge, a sanitized failure with **Try again**, and one new `Published` badge with the
post link and time. `/stories` gets a Published filter that hides internal upload states. The task
also proves the whole flow end to end with fakes, and documents Bluesky setup, media config and
recovery from a failed publication.

## Design

### 1. Status mapping (`story_status.py`)

- Add `PrimaryStoryStatus.PUBLISHED = "published"`. `primary_story_status`: `"published"` maps to
  `PUBLISHED`, and `"publishing"` and `"publish_failed"` map to `READY`. All other mappings stay the
  same. Nothing else changes in workflow code. No workflow imports `primary_story_status`, so replay
  is not affected.
- The UI therefore has six primary statuses. Internal publishing states are never badges. They only
  appear as the detail line under Ready.

### 2. Formatted post preview (pure, new `publishing/preview.py`)

- Refactor `publishing/formatter.py` without changing behaviour. Extract
  `formatted_post(package, capabilities) -> tuple[str, list[LinkFacet], str | None]`, which returns
  the text, the facets, and the link anchor text (or `None`). `format_publication` calls it. Existing
  formatter tests must pass unchanged.
- `PostPreview` (frozen, `extra="forbid"`): `text`, `body_text` (the text before the anchor),
  `link_text: str | None`, `char_count` (`len(text)`, the same count the formatter uses),
  `max_chars`, `destination: DestinationName`, and `problem: PublicationErrorCode | None`.
- `preview_post(package, content_hash, destination=BLUESKY) -> PostPreview` builds the text with
  `formatted_post` and then runs `format_publication`. A `PublicationRejected` sets `problem=exc.code`
  and still returns the text, so an over-length post can be shown. The function is pure: no clock,
  no I/O, no httpx.

### 3. Services

`services/stories.py`:

- Add `StoryFilter.PUBLISHED = "published"`, placed between READY and CLOSED.
- Add `PUBLISHING_WORKFLOW_TYPE = "StoryPublishingWorkflow"`. `story_visibility_query` gains a third
  branch, `OR (WorkflowType = 'StoryPublishingWorkflow' AND StoryCreatedAt > '<cutoff>')`. Publishing
  runs carry the inherited `StoryCreatedAt` (repo 063), so a published story leaves the list at
  exactly 48 hours.
- Filter predicates: both `READY` and `PUBLISHED` use
  `StoryStatus IN ('ready','publishing','publish_failed','published')`. The final split happens after
  collapse, which the gateway already does with `matches_story_filter`. Other predicates do not
  change. `publish_failed` never matches the CLOSED predicate's `'failed'`.
- `collapse_story_rows` handles publishing rows (`workflow_type == PUBLISHING_WORKFLOW_TYPE`):
  - Choose one publication per candidate. If any row is `published`, that row wins. Otherwise the
    row with the latest `updated_at` wins.
  - If the generation row is present and its status is `ready`, keep the generation row's
    `workflow_id`, title and source. Take `internal_status` and `status_detail` (bounded) from the
    publication, and set `updated_at` to the later of the two.
  - If the generation row is missing (for example, a scan page split), emit the publication's status
    with `workflow_id = story_id_for(candidate_id)` and the title and source from the publication
    row. A publication workflow ID is **never** a row's `detail_id`.
  - If the generation row exists but is not `ready`, ignore the publication.
- `StoryRow` gains `publishing: bool = False`. `build_story_row` sets it to True when
  `internal_status == "publishing"`.
- `StoryDetail` gains `publication: StoryPublishingState | None = None`,
  `publication_unknown: bool = False` and `post_preview: PostPreview | None = None`.
- Add a `PublicationLookup(Protocol)` with
  `async def publication(self, publication_id: str) -> StoryPublishingState | None`. The constructor
  becomes `StoryDetailService(gateway, publications: PublicationLookup | None = None)`.
  `TemporalStoryPublishingGateway` already satisfies the protocol.
- In `_generation_detail`:
  - For `review`, attach `post_preview = preview_post(package, content_hash)`.
  - For `ready`, attach the preview too. When `publications` is set, look up
    `publication_key(package.story_id, BLUESKY, content_hash)`.
  - A `StoryDetailUnavailable` or `StoryDetailMalformed` from the lookup sets
    `publication_unknown=True`. The page still renders.
  - A `published` publication sets `status=PUBLISHED`. `internal_status` becomes the publication
    status. Retention is checked first and does not change, so a published story still returns 410
    at 48:00:00.
- `services/publishing.py` does not change. The routes use `publish`. `publication_for` stays for
  the CLI and tests.

### 4. Web

`web/dependencies.py`:

- Add `get_story_publishing_service`, which builds
  `StoryPublishingService(TemporalStoryPublishingGateway(client))` on the shared cached client and
  maps connect errors the same way as the other story services.
- `get_story_detail_service` passes `publications=TemporalStoryPublishingGateway(client)`.

`web/routes/stories.py`:

- `GET /stories/{id}` gains `publish: str = ""`. The notice is
  `story_decision_notice(decided) or story_publish_notice(publish)`.
- New route `POST /stories/{detail_id}/publish`. It takes the form field `content_hash`, uses
  `current_user`, and calls only `StoryPublishingService.publish(PublishStoryRequest(detail_id,
  content_hash, requested_by=user.email), now=datetime.now(UTC))`. Every outcome redirects with
  303 to `/stories/{id}?publish=<token>#story-notice`:
  - A returned state of `publishing` redirects with `started`. A `published` state redirects with
    `published`. A `publish_failed` state redirects with `failed`.
  - `StoryPublishStale`, or a `ValidationError` from a malformed hash, redirects with `stale`.
  - `StoryNotPublishable` redirects with `not_ready`.
  - Not found returns 404, expired 410, malformed 502 and unavailable 503. These render the existing
    not-available pages.
- The route stays thin: it parses input, calls one service and redirects. It never calls Temporal
  directly.

`web/view_models.py` (all copy lives here):

- `STORY_STATUS_LABELS[PUBLISHED] = "Published"` and
  `STORY_STATUS_BADGE_CLASSES[PUBLISHED] = "badge--published"`.
  `STORY_FILTER_LABELS[PUBLISHED] = "Published"`. `_STORY_DETAIL_STATES[PUBLISHED] = "ready"`, so the
  package stays read-only and the rail stays visible.
- List polling becomes `bool(rows) and any(row.status in _ACTIVE_STORY_STATUSES or row.publishing)`.
  The READY and CLOSED filter exclusion is removed, because a Ready row can now be publishing.
- `PostPreviewView`:
  - Fields: `heading`, `body_text`, `link_text`, `count_text`, `used_percent` (int, 0-100), `over`
    (bool), `problem_text`.
  - `heading` is `"Bluesky post"`. `count_text` is `"212 of 300 characters"`.
  - `problem_text` is `FAILURE_DETAILS[problem]` plus a stage-specific sentence. In review it is
    " Reject this story: it can't be published as written." When Ready it is " It can't be published
    as approved."
  - It replaces the old `Post text` section in both review and ready. `StoryPackageView.post_text`
    stays in the model but the template no longer renders it.
- `PublishPanelView`, used only in the ready state:
  - Fields: `mode: Literal["available", "blocked", "publishing", "failed", "published",
    "unknown"]`, `heading`, `body`, `hint`, `button_label`, `action`
    (`/stories/{workflow_id}/publish`), `content_hash`, `show_form`, `button_disabled`,
    `post_href: str | None`, `post_link_label`, `published_iso`, `published_text`, `requested_by`.
  - Modes:

    | mode | when | heading | body | button |
    | --- | --- | --- | --- | --- |
    | available | no publication, preview fits | Approved | "Ready to publish. Nothing is posted until you press Publish." | "Publish to Bluesky" |
    | blocked | preview has a `problem` | Approved | the problem sentence | none; the body explains why |
    | publishing | publication `publishing` | Publishing | publication `status_detail` (fixed sentence) | disabled "Publishing…" |
    | failed | publication `publish_failed` | Not published | `status_detail` (sanitized, at most 200 chars) | "Try again" (only when retry is allowed) |
    | published | publication `published` | Published | "Published to Bluesky <relative time>." | none; link "View the post on Bluesky" |
    | unknown | `publication_unknown` | Approved | "Publishing status could not be read. Refresh to check again." | none |

  - In failed mode, retry is allowed unless the code is one of `ContentHashMismatch`,
    `ManifestMismatch`, `PostTextTooLong`, `AltTextTooLong`, `MediaNotSupported` or `MediaTooLarge`.
    For those codes the hint is "This approved story can't be posted as it is." For the other codes,
    `PUBLISH_FAILURE_HINTS` gives one fixed sentence each:
    - `DestinationAuthFailed`: "Check the Bluesky account settings on the worker, then try again."
    - `DestinationRateLimited`: "Wait a few minutes, then try again."
    - `PublicationMediaMissing` and `ArtifactHashMismatch`: "Restore the approved image file, then
      try again."
    - A failure with `error_code=None` (the run stopped early) uses the generic hint "Try again."
  - `post_href` comes from a new `safe_bluesky_post_url(url) -> str | None`. It accepts only
    `https://bsky.app/profile/<non-empty>/post/<non-empty>` with no query or fragment. Anything else
    gives `None`, and the panel then shows "The post link could not be shown." The panel never
    renders the raw URI or any destination response.
  - `_story_progress` changes:
    - READY with no publication shows "Approved and ready to publish."
    - READY while publishing or failed shows the publication `status_detail`. So the stamp reads
      **Ready** and the line beside it reads "Posting to Bluesky." (see the note under the design
      plan).
    - PUBLISHED shows "Published to Bluesky."
  - The approved body becomes "This is the final package. It is not published yet." when not
    published, and "This is the package that was published." when published.
- Detail polling is on while the status is pending or writing, **or** the panel mode is
  `publishing`.
- `STORY_PUBLISH_NOTICES` (an allow-list; unknown tokens render nothing):
  - `started`: "Publishing started. This page updates when it finishes."
  - `published`: "Published to Bluesky."
  - `failed`: "Publishing failed. The reason is shown next to the story."
  - `stale`: "This story changed after you opened it. Nothing was posted. Check it and try again."
  - `not_ready`: "Only an approved story can be published. Nothing was posted."

`web/templates/partials/story_detail_body.html` (loops and ifs only):

- Replace the `Post text` section with the post proof, which shows in review and ready when
  `view.post_preview` is set.
- In the `ready` rail, keep the approved meta and package code, then render the publish panel. When
  `show_form` is set, render a form that POSTs to `action` with a hidden `content_hash` and a
  `<button class="btn btn--primary" type="submit">`. In publishing mode, render
  `<button class="btn btn--primary" type="button" disabled>` with no form. In published mode, render
  `<a class="btn btn--secondary" href=… target="_blank" rel="noopener noreferrer">` with a visually
  hidden " (opens in a new tab)". Render the body line inside `role="status"` while publishing.
- `story_detail.html` and `stories.html` need no structural change. The new filter pill comes from
  the view model.

### 5. Design plan (frontend-design skill)

This page sits inside the existing neo-brutalist "graph paper and ink" system (036-041), so this task
adds to that system and does not restyle it.

- **Colour.** Reuse every existing token. Add one: `--color-published: #c7b8ff` (postmark violet). It
  is the same in both themes and holds text in `--color-on-fill` (`#15172b`, about 9.9:1). Add
  `--shadow-published: 6px 6px 0 0 var(--color-published)`. The palette is ground `#e3ecdf` /
  `#191c30`, ink `#15172b`, accent `#2448d8`, Ready `#7ddba3`, Published `#c7b8ff`, and failure
  `#ff9a9a` (as a fill with ink text only, never as a thin line or as text).
- **Type.** Use the system stacks only. Rail headings use `--font-display` at `--text-lg`. The post
  proof body uses `--font-body` at `--text-base`, line-height 1.5, `max-width: 62ch`. The count uses
  `font-variant-numeric: tabular-nums` and weight 700. There is no monospace face and no all-caps
  label.
- **Layout.** The proof sits in the 62ch reading column after the caveats. The publish panel lives in
  the existing 17rem rail at `min-width: 48rem`, and stacks under the reading column below that
  width. The rail button is `width: 100%`. Everything is left-aligned.

  ```
  [Ready]  Posting to Bluesky.                 (status line, polls)
  ┌ reading column ───────────────┐ ┌ rail ─────────────┐
  │ story card / body / evidence  │ │ Publishing         │
  │ ┌ Bluesky post   212 of 300 ┐│ │ Posting to Bluesky.│
  │ │ post text…                ││ │ [ Publishing… ]    │
  │ │ Source: example.com       ││ │ Package 1a2b3c4d   │
  │ ├┼┼┼┼┼┼┼▓▓▓▓▓▓▓▓░░░░░░░░░░░┤│ └────────────────────┘
  │ └───────────────────────────┘│
  └───────────────────────────────┘
  ```

- **Principles.** Spend the boldness in one place: the **post proof**, a print-proof slip whose ruler
  echoes the chart frame's ruler ticks. The proof is what actually gets posted, so making it the
  memorable element also makes it honest. The published receipt gets the violet hard shadow as its
  one flourish. Everything else reuses existing components. There is no new motion: publishing
  progress is a sentence, not a spinner.
- **Review against the brief.** My first draft put a coloured "Ready — Publishing…" stamp on the
  badge. That breaks the one-primary-badge rule, and it uses the spaced-em-dash label pattern. I
  revised it: the badge stays **Ready**, and the fixed detail sentence sits beside it, as all other
  detail lines do. This meets the plan's "Ready — Publishing…" requirement in spirit. I also dropped
  a planned violet page wash, because that would have been decoration.

CSS (`web/static/app.css`, one labelled section "Publishing" at the end, tokens only):

- `.badge--published { background: var(--color-published); }`
- `.post-proof`:
  - Container: `margin: var(--space-5) 0`, `background: var(--color-surface)`,
    `border: var(--border-width) solid var(--color-border)`, `border-radius: var(--radius-sm)`,
    `box-shadow: var(--shadow-sm)`, `overflow: hidden`.
  - `.post-proof__head`: flex, `justify-content: space-between`, wraps, `gap: var(--space-2)`,
    `padding: var(--space-3) var(--space-4)`, a bottom border of `var(--border-thin)` ink, and
    `font-size: var(--text-sm)` at weight 800.
  - `.post-proof__text`: `margin: 0`, `padding: var(--space-4)`, `white-space: pre-wrap`,
    `overflow-wrap: anywhere`, `max-width: 62ch`.
  - `.post-proof__link`: `color: var(--color-accent)`, underlined.
- `.post-proof__ruler`:
  - `height: 0.75rem`, a top border of `var(--border-thin)` ink.
  - Ticks: `background: repeating-linear-gradient(90deg, var(--color-border) 0 2px, transparent 2px
    10%)`.
  - A child `span` set to `display: block; height: 100%; width: var(--post-used);
    background: var(--color-info)`. The template sets only `style="--post-used: N%"`, with N from the
    view model.
  - Over the limit: `.post-proof--over .post-proof__ruler span { background: var(--color-danger); }`.
  - The ruler is `aria-hidden="true"`. The visible count text carries the information.
- `.post-proof__problem`: `margin: 0`, `padding: var(--space-3) var(--space-4)`,
  `background: var(--color-danger)`, `color: var(--color-on-fill)`, a top border of
  `var(--border-thin)` ink, weight 700.
- `.publish-panel__failure`: the same fill treatment as `.post-proof__problem`, with an ink
  `var(--border-thin)` border and `margin-bottom: var(--space-3)`.
- `.story-rail--published { box-shadow: var(--shadow-published); }`
- Do not set fixed widths above `20rem`. Every new rule must work at 360px with no horizontal
  scroll. Reduced motion needs no new rule, because nothing new animates (the existing `.btn`
  transition rule already covers the button).

### 6. Docs

- **`README.md`.** Add a new section "Publishing a story". Explain the third explicit click: Ready
  leads to Publish, which leads to Published. Neither candidate approval nor final approval
  publishes. Cover:
  - The six statuses, including **Published**.
  - The Bluesky config: `BLUESKY_HANDLE`, `BLUESKY_APP_PASSWORD` (an app password, never the account
    password) and `BLUESKY_SERVICE`. These are worker-only.
  - The media config: `MEDIA_DIR`, the shared volume, and `MEDIA_PROVIDER` (`story-card` or `fake`).
  - That a GIF is published as its still card on Bluesky.
  - The 48-hour retention, which also applies to published stories.
  - The e2e command `uv run pytest tests/test_story_publishing_e2e.py -q`.
  - A link to `docs/runbooks/publishing.md`.
- **`docs/runbooks/publishing.md` (new)**, with these sections:
  - `## Configure Bluesky`: create an app password, then set the keys in `.env` / `.env.prod`. Explain
    how to check the account and how to rotate the password, with no real handles in examples.
  - `## Media and providers`: `MEDIA_DIR`, the prod `media` volume in worker and web,
    `MEDIA_PROVIDER`, and the `<sha256>.<ext>` filenames. Never edit an artifact.
  - `## Publish a story`: the UI steps, what each state means, and the double-click safety.
  - `## Recover a failed publication`:
    - A table from every `PublicationErrorCode` to its UI sentence, its cause and the operator
      action.
    - Commands: `workflow describe --workflow-id publish-v1-…`, `workflow query --workflow-id
      publish-v1-… --type publication_state`, and the list query
      `WorkflowType = "StoryPublishingWorkflow" AND StoryStatus = "publish_failed"`.
    - Stuck in `publishing`: terminate the run, then press Try again. This is safe because the fixed
      rkey returns the existing post.
    - Never create the post by hand. Package problems (for example, text too long) can't be fixed
      after approval.
    - Use the `dev-temporal()` / `prod-temporal()` helpers.
  - `## Retention`: 48 hours from the original detection. Published stories also leave the UI, and
    histories are not deleted.
  - `## Visual check (manual)`: the checklist in "Visual QA" below.
- **`docs/runbooks/stories.md`.** Add one paragraph after "Two human decisions": publishing is a
  separate third click (link `publishing.md`). Keep the existing "never publishes" wording for final
  approval, since it is still true.
- **`docs/runbooks/search-attributes.md`.** Add a publishing check with the `publishing`,
  `published` and `publish_failed` values. Explain that they reuse the existing `Story*` attributes
  and that no new attribute exists.
- **`docs/runbooks/deploy.md`.** Link `publishing.md` next to the Bluesky keys.
- **`docs/architecture/tech.md`.** Update:
  - The Routes table: the new POST route and the `?publish=` param.
  - Services: the collapse rule, `PublicationLookup`, and `preview_post`.
  - The "Story publishing contracts" paragraph: remove the "For 064" note.
  - Templates/design: the post proof, the publish panel, `--color-published`, and six statuses.
  - The Settings table: add the missing `MEDIA_PROVIDER` row.
  - Development: the new e2e test.
- **`docs/architecture/business.md` and `docs/architecture/README.md`.** Add the Published status,
  the explicit Publish rule, and that a failed publication leaves the story Ready.

### 7. Visual QA (honest about the screenshot gap)

The builder environment's headless Chromium has failed before because `libnspr4` is missing (repo
061). The builder **tries** to take 360px and 1280px screenshots in light and dark of `/stories` and
of `/stories/{id}` in review, available, publishing, failed and published states. If the browser
does not start, the builder does not claim visual checks. Instead:

1. Automated markup and CSS assertions stand in for screenshots (see the tests below).
2. `docs/runbooks/publishing.md` "Visual check (manual)" lists each item for a human:
   - No horizontal scroll at 360px.
   - The rail stacks under the reading column below 48rem.
   - The post text wraps a long URL host.
   - The disabled button is visibly dashed.
   - The focus ring shows on Publish, Try again and View the post.
   - The dark theme keeps ink-on-violet and ink-on-pink legible.
   - Reduced motion shows the still card.
3. The handoff note and the PR body state plainly which screenshots were and were not taken, and ask
   the human to run the checklist before merging.

## Acceptance criteria

- [ ] Review and Ready both show the exact formatted post (body, `Source: <host>`) with a character
      count. An over-limit post shows the `PostTextTooLong` sentence in review, and in Ready the
      panel is `blocked` with no Publish button. Text, static and GIF stories keep the 061 media
      preview, and reduced motion still gets the poster.
- [ ] A Ready story shows **Publish to Bluesky**. POST `/stories/{id}/publish` starts at most one
      workflow: a double POST or a click while publishing returns the existing state without a second
      start. While the run is active, the badge stays **Ready**, the line shows the fixed detail
      sentence, the button is `disabled`, and the region polls.
- [ ] A failed publication shows **Not published** with only the fixed sentence (never raw errors,
      tokens or post text), plus a hint. It shows **Try again** for retryable or operator-fixable
      codes and none for package problems. Try again re-POSTs and can succeed.
- [ ] A published story shows one **Published** badge (violet), "Published to Bluesky <time>" and a
      `View the post on Bluesky` link built only from a safe `https://bsky.app/profile/…/post/…`
      URL. `/stories` has a Published filter. Ready and Published rows never show `publishing` or
      `publish_failed` as a badge, and rows always link to the generation workflow.
- [ ] Retention: a published story is visible in the list and detail at 47:59:59 and gone at
      48:00:00 after the original detection (410 on detail). No history is deleted.
- [ ] The full flow candidate → approval → story and media review → Ready → Publish → Published
      passes through the real web routes, services and Temporal gateways, with a fake PDS
      (`httpx.MockTransport`) and recorded LLM/HTTP fixtures. No live publication happens in CI.
      Docs let a fresh operator configure Bluesky and recover a failed publication. `just lint` and
      `just test` pass, including golden media and replay suites.

## Files to touch

- `src/ai_trend_pipeline/story_status.py`
- `src/ai_trend_pipeline/publishing/formatter.py` (extract `formatted_post`; no behaviour change)
- `src/ai_trend_pipeline/publishing/preview.py` (new)
- `src/ai_trend_pipeline/services/stories.py`
- `src/ai_trend_pipeline/web/dependencies.py`
- `src/ai_trend_pipeline/web/routes/stories.py`
- `src/ai_trend_pipeline/web/view_models.py`
- `src/ai_trend_pipeline/web/templates/partials/story_detail_body.html`
- `src/ai_trend_pipeline/web/static/app.css`
- `tests/test_story_status.py`, `tests/test_story_status_service.py`,
  `tests/test_story_detail_service.py`, `tests/test_web_stories.py`, `tests/test_web_story_detail.py`,
  `tests/test_design_tokens.py`, `tests/test_publishing_formatter.py` (add cases)
- `tests/test_post_preview.py`, `tests/test_web_story_publish.py`,
  `tests/test_story_publishing_e2e.py`, `tests/test_publishing_docs.py` (new)
- `tests/test_story_generation_e2e.py`: extract the `story_e2e` harness into a new
  `tests/story_e2e_harness.py` and add an optional publishing worker and service. Existing tests stay
  unchanged.
- `README.md`, `docs/runbooks/publishing.md` (new), `docs/runbooks/stories.md`,
  `docs/runbooks/search-attributes.md`, `docs/runbooks/deploy.md`, `docs/architecture/tech.md`,
  `docs/architecture/business.md`, `docs/architecture/README.md`

## Tests to write

`tests/test_story_status.py`
- `test_published_maps_to_published_and_internal_publish_states_map_to_ready`

`tests/test_post_preview.py`
- `test_preview_text_matches_format_publication_text_and_facets`
- `test_preview_over_limit_returns_text_and_post_text_too_long`
- `test_preview_text_only_static_and_gif_packages`
- `test_preview_module_imports_no_httpx_clock_or_services` (AST scan)

`tests/test_publishing_formatter.py`
- `test_formatted_post_is_used_by_format_publication_unchanged`

`tests/test_story_status_service.py`
- `test_visibility_query_includes_publishing_branch_with_story_created_at`
- `test_ready_and_published_filter_predicates_fetch_all_publish_states`
- `test_collapse_overlays_publication_on_ready_generation_and_keeps_generation_id`
- `test_collapse_prefers_published_run_then_latest_run`
- `test_collapse_without_generation_row_uses_story_id_for_candidate`
- `test_publishing_row_is_ready_with_detail_and_marked_publishing`
- `test_published_story_visible_at_47_59_59_and_absent_at_48_00_00`

`tests/test_story_detail_service.py`
- `test_review_and_ready_attach_post_preview`
- `test_ready_attaches_publication_and_published_sets_published_status`
- `test_publication_lookup_failure_sets_unknown_and_still_renders`
- `test_published_story_is_expired_at_48_hours`

`tests/test_web_story_publish.py` (fake services through `dependency_overrides`)
- `test_ready_story_shows_publish_form_with_content_hash`
- `test_publish_post_redirects_started_and_calls_service_once_per_post`
- `test_double_post_returns_existing_state_without_second_start`
- `test_publishing_state_shows_ready_badge_detail_disabled_button_and_polling`
- `test_failed_state_shows_sanitized_sentence_hint_and_try_again`
- `test_package_problem_failure_has_no_try_again`
- `test_blocked_preview_has_no_publish_button`
- `test_published_state_shows_single_published_badge_time_and_safe_link`
- `test_unsafe_public_url_renders_no_link`
- `test_stale_not_ready_and_malformed_hash_redirect_with_notices`
- `test_missing_expired_malformed_unavailable_pages`
- `test_publish_requires_current_user_in_cloudflare_mode`
- `test_publish_notice_allow_list_ignores_unknown_tokens`
- `test_failure_html_contains_no_post_text_token_or_raw_error`

`tests/test_web_stories.py`
- `test_filter_bar_has_published_between_ready_and_closed`
- `test_published_row_badge_and_link_to_generation_id`
- `test_list_polls_while_a_row_is_publishing_even_in_ready_filter`

`tests/test_web_story_detail.py`
- `test_post_proof_replaces_post_text_in_review_and_ready`
- `test_over_limit_proof_shows_problem_in_review`
- `test_gif_story_keeps_reduced_motion_poster_with_proof`

`tests/test_design_tokens.py`
- `test_published_fill_contrast_with_on_fill_in_both_themes`
- `test_publishing_css_section_uses_tokens_and_no_fixed_width_over_20rem`
- `test_post_proof_text_wraps_and_ruler_is_aria_hidden` (CSS and markup assertions that stand in
  for the 360px screenshot)

`tests/test_story_publishing_e2e.py` (`start_local`, the real workers including publishing with the
real `publish_story_to_bluesky` against `FakePds`, fake media renderer, routes through `TestClient`)
- `test_candidate_to_published_through_ui_creates_one_post` (static card)
- `test_gif_story_publishes_still_card_and_text_only_story_publishes_text`
- `test_double_click_publish_creates_one_run_and_one_record`
- `test_failed_publication_shows_not_published_then_try_again_succeeds`
- `test_ui_shows_only_six_primary_badges_throughout_and_no_internal_publish_badge`
- `test_published_story_leaves_list_and_detail_at_48_hours` (service `now` override)
- `test_no_live_network_all_bluesky_requests_hit_fake_pds`

`tests/test_publishing_docs.py`
- `test_readme_has_publishing_section_bluesky_and_media_keys_and_e2e_command`
- `test_publishing_runbook_sections_commands_and_every_error_code`
- `test_publishing_runbook_has_manual_visual_checklist`
- `test_tech_lists_publish_route_media_provider_and_no_for_064_note`

## Out of scope

- Changes to `StoryPublishingWorkflow`, `story_publishing.py`, `bluesky_story.py`, publishing
  models/registry, `StoryGenerationWorkflow`, `StoryCandidateWorkflow`, `story_visibility.py`, the
  chart path or `bluesky.py`. The only `publishing/` change is the behaviour-preserving
  `formatted_post` extraction.
- New search attributes, tables, env vars, compose or Dockerfile changes, and new CLI commands.
- Destinations other than Bluesky, editing or regenerating post text or media after approval, a
  "republish" or delete-post action, and any live API call.
- JavaScript beyond the existing htmx, web fonts, and new animation.
- Restyling existing pages or components outside the new "Publishing" CSS section, except the
  `.badge--published` token use and the replaced `Post text` block.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: Wrote the spec for plan row 063 (repo 064) with the frontend-design skill: the post proof with a character ruler is the one bold element, plus a violet `Published` stamp (`#c7b8ff`, about 9.9:1 with ink). The badge stays Ready while publishing, with the fixed detail sentence beside it.
Key choices: publishing rows join the `/stories` query and collapse onto the generation row (never their own link). `StoryDetailService` takes an optional `PublicationLookup`, so detail routes still call one service. `preview_post` is pure and shared with the formatter.
Screenshot gap: try Chromium, otherwise CSS/markup tests plus a manual checklist in `docs/runbooks/publishing.md`, stated in the PR. The task is at the upper bound of one PR. If it overruns, split the docs into a follow-up.
I have no shell: no branch, no commit. The file is uncommitted on `main`. Next: the orchestrator creates `task/064-publishing-ui-e2e-docs`, commits only this file (not `.claude/settings.json`), and approves it (the row is `no`).

**builder**: Built all of 064 in one PR: status/preview/services, publish route and panel, post proof CSS, `tests/story_e2e_harness.py` extraction, 7 e2e tests against a fake PDS, and all docs. `just lint` and `uv run pytest` (1416 tests) pass.
Screenshots NOT taken: headless Chromium fails (libnspr4 missing). Markup/CSS tests stand in; the human must run the "Visual check (manual)" list in `docs/runbooks/publishing.md` before merging.
Beyond the spec: view model shows only fixed publication sentences (never raw `status_detail`); updated `test_story_docs.py` ("six UI badges"), the `test_publishing_service.py` import guard allow-list and one e2e assertion ("It is not published yet.") for the new copy.

**verifier**: Ran `just lint` (ruff check, ruff format --check, pyright strict: 0 errors) and `just test` (1416 passed, 2 pydantic serializer UserWarnings in test_web_story_detail.py, not failures). ANTHROPIC_* vars unset.
All checks pass.

**reviewer**: Approved. Diff matches the spec, layering holds (route calls one service, templates loops/ifs only, no env reads), `.claude/settings.json` is not in the diff.
Notes: (1) The 2 pydantic UserWarnings in `test_web_story_detail.py` are new (absent on main); they come from `safe_preview` hashing intentionally `model_construct`-ed packages with bad URLs, so test-only, not a product issue. (2) Edits to existing tests all follow from spec copy/filter changes; the import-guard allow-list still blocks approval/workflow modules. (3) Screenshot gap is stated honestly; the human must run the manual checklist before merging.

**scribe**: Docs were already current, so no doc edits. Opened PR #73 against main: https://github.com/akonale/ai-trend-pipeline/pull/73 (not merged).
The PR body states that no 360px, dark or light screenshots were taken (libnspr4 missing), that markup/CSS tests stand in, and that the "Visual check (manual)" list in `docs/runbooks/publishing.md` must be run by a human before `just deploy`.
Status set to pr-open. `.claude/settings.json` was not staged.
