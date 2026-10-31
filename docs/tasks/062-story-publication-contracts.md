# 062: Story-publication contracts and Bluesky story activity

Status: reviewed

> Plan row **061** in `docs/mvp-plan.md` ("Story-publication contracts and Bluesky activity"),
> renumbered to **062** (repo numbers are plan row + 1, see `docs/tasks/058-media-foundation.md`).
> It builds on repo 058-061: `MediaManifest`/`MediaArtifact`, `media/storage.py`, and
> `story_package_hash` / `package_matches_hash` / `story_content_hash` in `media/briefs.py`.
> Plan rows 062-063 are repo **063-064**. The cross-references below use repo numbers.

## Goal

Add pure, bounded story-publication contracts: a destination capability registry and a
deterministic formatter that turns only an approved `StoryPackage` into post text, link facets,
alt text and one media embed. Add a separate Bluesky story activity that checks the content and
artifact hashes, finds an existing record by a deterministic key, uploads the approved artifact
and creates the post at most once. Nothing calls this activity yet. The workflow, worker
registration and UI come in repo 063-064. `src/ai_trend_pipeline/bluesky.py` (the chart publisher)
stays byte-for-byte unchanged.

## Design

### Package layout (new `src/ai_trend_pipeline/publishing/`)

- `models.py`, `registry.py` and `formatter.py` are **pure**. They import only stdlib, Pydantic,
  `media.models`, `media.briefs` and `story_drafting.models`, so the repo 063 workflow can import
  them through `imports_passed_through` and run capability validation in workflow code. No httpx,
  no clock, no file I/O.
- `bluesky_story.py` holds the activity (httpx and file reads). It may import `make_tid`,
  `BlueskyCredentials` and `POST_COLLECTION` from `bluesky.py` but must not change that file.
- Activities stay log-free, like the other story activities (see `story_logging.py`). Repo 063's
  workflow does the logging.

### Models (`publishing/models.py`)

All models are `frozen=True, extra="forbid"`. Timestamps are validated as aware and normalized
with `ensure_aware_utc`.

- `DestinationName(StrEnum)`: `BLUESKY = "bluesky"`.
- `PublicationErrorCode(StrEnum)`. The values are used as `ApplicationError.type`:
  `ContentHashMismatch`, `ManifestMismatch`, `PostTextTooLong`, `AltTextTooLong`,
  `MediaNotSupported`, `MediaTooLarge`, `PublicationMediaMissing`, `ArtifactHashMismatch`,
  `DestinationAuthFailed`, `DestinationRateLimited`, `DestinationUnavailable`,
  `DestinationRejected`, `UnexpectedDestinationResponse`.
  `RETRYABLE_CODES = {DestinationRateLimited, DestinationUnavailable, UnexpectedDestinationResponse}`.
  All other codes are non-retryable.
- `DestinationCapabilities`: `destination`, `max_text_chars: int`, `max_text_bytes: int`,
  `max_images: int (ge=0)`, `static_media_types: tuple[MediaType, ...]`, `max_static_bytes: int`,
  `supports_animation: bool`, `animated_media_types: tuple[MediaType, ...]`,
  `max_animated_bytes: int (ge=0)`, `max_alt_text_chars: int`, `supports_link_facets: bool`.
- `LinkFacet`: `byte_start: int (ge=0)`, `byte_end: int`, `uri: HttpUrl` (max 1024). Validator:
  `byte_end > byte_start`.
- `PublicationMedia`: `artifact: MediaArtifact`, `alt_text: str` (equals `artifact.alt_text`).
- `PublicationPackage` (formatter output): `destination`, `story_id`, `content_hash`
  (SHA-256 pattern), `text: str (min 1)`, `facets: list[LinkFacet] (max 4)`,
  `media: PublicationMedia | None`. Validator: every facet lies inside
  `len(text.encode("utf-8"))` and facets do not overlap.
- `PublicationInput` (activity input): `destination`, `story_id`, `candidate_id`,
  `story_created_at`, `requested_at`, `package: StoryPackage`, `content_hash` (SHA-256 pattern).
  Validator: `package.story_id == story_id`.
- `PublishedMediaKind(StrEnum)`: `NONE`, `STATIC`, `ANIMATED`.
- `PublicationResult`: `destination`, `story_id`, `candidate_id`, `story_created_at`,
  `content_hash`, `idempotency_key`, `uri`, `cid`, `rkey`, `public_url`, `published_at`,
  `media_kind: PublishedMediaKind`, `artifact_id: str | None`, `already_published: bool`.
  It holds no response bodies or tokens.
- `publication_key(story_id, destination, content_hash) -> str` returns
  `f"publish-v1-{story_id}-{destination.value}-{content_hash}"`. It is both the repo 063
  workflow ID and the idempotency key, so one approved content hash maps to one key.

### Registry (`publishing/registry.py`)

- `BLUESKY_CAPABILITIES`: `max_text_chars=300`, `max_text_bytes=3000`, `max_images=4`,
  `static_media_types=(PNG, WEBP)`, `max_static_bytes=1_000_000`, `supports_animation=False`,
  `animated_media_types=()`, `max_animated_bytes=0`, `max_alt_text_chars=2000`,
  `supports_link_facets=True`.
- `DESTINATION_CAPABILITIES: Mapping[DestinationName, DestinationCapabilities]` and
  `capabilities_for(name) -> DestinationCapabilities`.

### Formatter (`publishing/formatter.py`)

`format_publication(package, content_hash, capabilities) -> PublicationPackage`. It raises
`PublicationRejected(code: PublicationErrorCode)`, a `ValueError` subclass with a fixed message
per code that contains no story text. Checks run in this order:

1. `package_matches_hash(package, content_hash)`, else `ContentHashMismatch`.
2. If `package.media` is set: `media.story_id == package.story_id` and
   `media.story_content_hash == story_content_hash(package)`, else `ManifestMismatch`.
3. Body: `package.post_text` if set, else `package.headline`. It is never reworded or truncated.
4. Link line: only when `supports_link_facets` and `package.source_links` is non-empty. The
   anchor is the host of `source_links[0].url` with a leading `www.` removed. The text is
   `f"{body}\n\nSource: {anchor}"`. One `LinkFacet` covers the anchor's UTF-8 bytes, and its
   `uri` is the full source URL.
5. `len(text) > max_text_chars` (code points; this is never fewer than Bluesky's grapheme
   count, so it is a safe bound) or `len(text.encode()) > max_text_bytes` raises
   `PostTextTooLong`. There is no silent truncation and no dropping of the link line.
6. Media: if `media is None` or the outcome is `TEXT_ONLY`, there is no media. For `GIF`, use
   `animated_artifact` only when `supports_animation` and `GIF in animated_media_types`;
   otherwise use the approved `static_artifact` (the capability fallback). For `STATIC` and
   `STATIC_FALLBACK`, use `static_artifact`. If the chosen artifact's type is not allowed, or
   `max_images == 0`, raise `MediaNotSupported`. If it is over the byte limit, raise
   `MediaTooLarge`. If the alt text is over `max_alt_text_chars`, raise `AltTextTooLong`. Nothing
   is re-encoded or regenerated.

### Bluesky story activity (`publishing/bluesky_story.py`)

`BlueskyStoryActivities(client, credentials, media_root: Path, capabilities=BLUESKY_CAPABILITIES,
clock=lambda: datetime.now(UTC))`, with
`@activity.defn async def publish_story_to_bluesky(data: PublicationInput) -> PublicationResult`.

1. **Local checks before any network call:** `format_publication(...)` (`PublicationRejected`
   becomes a non-retryable `ApplicationError` with the same type). For media, resolve the path
   with `resolve_artifact_path`. `MediaArtifactMissing` becomes `PublicationMediaMissing`. Read
   the bytes in `asyncio.to_thread`. `sha256_hex(bytes) != artifact.content_hash`, or a failed
   `has_media_signature`, becomes `ArtifactHashMismatch`.
2. `key = publication_key(...)` and `rkey = make_tid(data.story_created_at, key)`. Because of
   this, a retry or a later re-publish of the same content hash always targets the same rkey.
3. `createSession`. A 401 becomes `DestinationAuthFailed` (non-retryable).
4. `getRecord(repo=did, collection=app.bsky.feed.post, rkey)`. A 200 returns
   `already_published=True` with the found `uri`/`cid`, with no upload and no create.
   `published_at` is `value.createdAt` when it parses, else `data.requested_at`. A 400 with
   `RecordNotFound` continues.
5. If there is media, `uploadBlob` with `Content-Type` set to the artifact MIME type.
   413 or `BlobTooLarge` becomes `MediaTooLarge`.
6. `createRecord` with that rkey. The record has `text`, `createdAt` (`requested_at`, ISO Z in
   milliseconds), `facets` (only if any, as `app.bsky.richtext.facet` with `index.byteStart/byteEnd`
   and a `#link` feature), and for media an `app.bsky.embed.images` embed with one image, `alt` and
   `aspectRatio` from the artifact dimensions. On a 4xx other than 401/413/429, call `getRecord`
   once more. If the record now exists (a concurrent start won), return `already_published=True`.
   Otherwise raise `DestinationRejected`.
7. Return `PublicationResult` with `public_url = f"https://bsky.app/profile/{did}/post/{rkey}"`.

**Error mapping:** 429 anywhere becomes `DestinationRateLimited` (retryable, with
`next_retry_delay` from the `ratelimit-reset` header (epoch seconds) minus `clock()`, clamped
to 1 s-15 min when the header is present). `httpx.TransportError` or 5xx becomes
`DestinationUnavailable`. Any other unexpected status becomes `UnexpectedDestinationResponse`.
Every `ApplicationError` uses a fixed message (at most the HTTP status code), carries no
`details`, and is raised `from None`, so no response body, token, password or httpx text enters
Temporal history.

## Acceptance criteria

- [ ] The models reject a bad content hash pattern, mismatched `story_id`, out-of-range or
      overlapping facets, and naive datetimes. `publication_key` is deterministic and differs
      when the story, destination or content hash differs. `publishing/models.py`,
      `registry.py` and `formatter.py` import no httpx, LLM client, renderer or media activity
      module.
- [ ] The formatter output is deterministic and built only from the approved package. It covers
      text-only, static, GIF on Bluesky (static fallback), GIF with an animation-capable test
      capability (animated artifact), and a link facet with correct UTF-8 byte offsets
      (non-ASCII body). It raises the matching code for a hash mismatch, a manifest mismatch,
      text that is too long, unsupported media, oversize media and alt text that is too long,
      and it never truncates.
- [ ] Against a fake PDS using recorded fixtures, the activity covers text-only, image,
      animation-capability fallback (the static PNG is uploaded, never the GIF), an existing
      record (no upload or create), an upload retry, a create retry, rate limit, auth failure,
      invalid text or media, hash mismatch and a missing artifact. Local check failures make
      zero HTTP requests.
- [ ] Two sequential calls and two concurrent calls (`asyncio.gather`) with the same input
      produce exactly one `createRecord` that succeeds and one stored record. Both return the
      same `uri` and `rkey`.
- [ ] No error message, `details`, or log record contains the app password, access JWT or a
      raw response body. Retryable and non-retryable flags match `RETRYABLE_CODES`.
- [ ] `src/ai_trend_pipeline/bluesky.py` and `tests/test_bluesky.py` are unchanged and still
      pass. No worker, workflow, service, route or config change. `just lint` and `just test`
      pass.

## Files to touch

- `src/ai_trend_pipeline/publishing/__init__.py` (new)
- `src/ai_trend_pipeline/publishing/models.py` (new)
- `src/ai_trend_pipeline/publishing/registry.py` (new)
- `src/ai_trend_pipeline/publishing/formatter.py` (new)
- `src/ai_trend_pipeline/publishing/bluesky_story.py` (new)
- `tests/fixtures/bluesky_story/` (new, hand-recorded JSON: `get_record_found.json`,
  `rate_limited.json`, `create_record_invalid.json`, `upload_blob_too_large.json`,
  `auth_failed.json`). The existing `tests/fixtures/bluesky_*.json` files are reused read-only.
- `tests/test_publishing_models.py`, `tests/test_publishing_formatter.py`,
  `tests/test_bluesky_story.py` (new)
- `docs/architecture/tech.md` (a short "Story publishing contracts" paragraph: pure formatter,
  key format, and that the activity is not registered until repo 063)

## Tests to write

`tests/test_publishing_models.py`
- `test_publication_key_is_deterministic_and_varies_by_story_destination_hash`
- `test_package_rejects_out_of_range_or_overlapping_facets`
- `test_input_rejects_story_id_mismatch_bad_hash_and_naive_datetimes`
- `test_pure_modules_import_no_http_llm_or_renderer` (AST scan of the import statements in
  models, registry and formatter)

`tests/test_publishing_formatter.py`
- `test_text_only_uses_post_text_and_source_link_facet`
- `test_headline_used_when_post_text_missing`
- `test_facet_byte_offsets_with_non_ascii_body`
- `test_static_and_static_fallback_select_static_artifact`
- `test_gif_falls_back_to_static_on_bluesky`
- `test_gif_uses_animated_artifact_when_capability_allows`
- `test_text_only_manifest_and_no_manifest_give_no_media`
- `test_rejects_content_hash_mismatch` (the alt text was changed after approval)
- `test_rejects_manifest_story_content_hash_mismatch`
- `test_too_long_text_raises_and_never_truncates`
- `test_unsupported_type_oversize_media_and_long_alt_text_raise`
- `test_output_is_deterministic`

`tests/test_bluesky_story.py` (stateful fake PDS on `httpx.MockTransport`; artifacts written with
`write_artifact` into `tmp_path`)
- `test_text_only_post_has_facets_and_no_embed`
- `test_image_post_uploads_png_with_alt_and_aspect_ratio`
- `test_gif_story_uploads_static_fallback_not_gif`
- `test_existing_record_returns_already_published_without_upload_or_create`
- `test_upload_failure_is_retryable_then_second_call_posts_once`
- `test_create_failure_after_server_created_record_recovers_as_already_published`
- `test_sequential_and_concurrent_duplicate_calls_create_one_record`
- `test_rate_limit_is_retryable_with_bounded_next_retry_delay`
- `test_auth_failure_is_non_retryable`
- `test_destination_rejected_post_is_non_retryable`
- `test_hash_mismatch_and_bad_media_make_no_http_requests`
- `test_missing_or_tampered_artifact_raises_before_network`
- `test_errors_and_logs_contain_no_secrets_or_raw_responses` (caplog plus failure messages)

## Out of scope

- `StoryPublishingWorkflow`, the gateway, worker registration, query and visibility state, and
  retry policies (repo 063). The Publish button, `published` badge and docs runbook (repo 064).
- Any change to `bluesky.py`, `PostChartInput`, the chart workflow or `tests/test_bluesky.py`.
- Config or env vars (reuse the existing `BLUESKY_*` settings later), compose or Dockerfile.
- Destinations other than Bluesky, video or GIF upload to Bluesky, re-encoding, resizing,
  regenerating or re-selecting media, LLM calls, and rewording or truncating post text.
- Live API calls of any kind.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: Wrote the spec for plan row 061 (repo 062). There is no UI, so the design step was skipped. The formatter is pure so the repo 063 workflow can call it, and the activity is log-free and not registered yet.
Key choices: key = workflow ID `publish-v1-<story>-<dest>-<hash>`; rkey = `make_tid(story_created_at, key)`, so re-publishing the same hash never double-posts. Bluesky has no animation, so the static fallback is used. Text over 300 code points fails (`PostTextTooLong`) and is never truncated.
Risk: a `post_text` near 300 characters plus the "Source:" line will fail. Repo 064 should preview the formatted text in review.
I have no shell, so I did not create the branch or commit. The spec is uncommitted in the main checkout. Next: the orchestrator creates `task/062-story-publication-contracts`, commits only this file, and approves (row is `no`).

**builder**: Built `publishing/` (models, registry, formatter, `bluesky_story.py`), 5 fixtures, 3 test files plus a shared `tests/publishing_fixtures.py` helper, and the tech.md paragraph. `just lint` clean; full `just test` 1324 passed. `bluesky.py` and `test_bluesky.py` untouched.
Checked: installed temporalio `ApplicationError` accepts `next_retry_delay: timedelta | None`, as the spec assumes.
Extras: formatter drops the link line when `source_links` is empty; added tests for create 5xx retry and unexpected responses. Two `Any` uses are commented; `_json` helpers use `cast` for untyped PDS JSON.

**verifier**: Ran `just lint` (ruff check, ruff format --check, pyright strict: 0 errors) and `just test` (1324 passed), with ANTHROPIC_* unset.
All checks pass.

**reviewer**: Approved. Diff matches spec; hash and artifact checks run before any network call; fixed rkey plus getRecord and recheck gives idempotency; errors are fixed-message and `from None`. Pure modules import no I/O; `bluesky.py`, `test_bluesky.py` and `.claude/` are not in the diff.
Notes: `tests/publishing_fixtures.py` is a small shared helper outside "Files to touch" and is acceptable. Other 4xx from getRecord or uploadBlob map to retryable UnexpectedDestinationResponse. Repo 063 should bound retry attempts.
