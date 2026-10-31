# 058: Media contracts and StoryMediaWorkflow foundation

Status: built

> Plan row **057** in `docs/mvp-plan.md` ("Media contracts and `StoryMediaWorkflow` foundation"),
> renumbered to **058** because `docs/tasks/057-candidate-visibility-fallback.md` already uses 057.
> When the plan says "058" (static card renderer) or "060" (integration) below, it means those plan
> rows, which will also shift by one when they are specced.

## Goal

Add the bounded media contracts (brief, artifact, manifest, workflow input/result), content-hash
atomic artifact storage on a configured shared media directory, a safe manifest-based artifact
resolver, a static renderer registry with a fake renderer, and a standalone `StoryMediaWorkflow`
child workflow that returns an explicit `gif` / `static` / `static_fallback` / `text_only`
outcome. Nothing starts this workflow yet, and there is no real renderer, web route or UI.

## Design

### Modules

New package `src/ai_trend_pipeline/media/` (pure contracts and helpers, no `services/` or `web/`
imports):

- `media/models.py`: enums, limits, `MediaBrief`, `MediaArtifact`, `MediaManifest`,
  `RenderMediaInput`, `media_brief_hash`, `artifact_id_for`. Must **not** import
  `story_drafting` (it imports this module, so that would be a cycle).
- `media/briefs.py`: `story_content_hash`, `media_workflow_id_for`, `media_brief_for`,
  `StoryMediaInput`, `StoryMediaResult` (these need `StoryPackage` and `FactSet`).
- `media/storage.py`: `write_artifact`, `resolve_artifact_path`, `MediaArtifactMissing`.
- `media/providers.py`: `RenderedMedia`, `MediaRenderer` Protocol, `MediaRenderError`,
  `MediaProviderName`, `MEDIA_RENDERERS`, `renderer_for`, `UnknownMediaProviderError`.
- `media/fake.py`: `FakeMediaRenderer` (registered under `MediaProviderName.FAKE`).
- `media/activities.py`: `StoryMediaActivities(media_root: Path, renderer: MediaRenderer)` with
  `render_static_media` and `render_animated_media`.
- `story_media.py` (top level, next to `story_generation.py`): `StoryMediaWorkflow`,
  `STORY_MEDIA_TASK_QUEUE = "story-media-task-queue"`, activity names and timeouts.
- `services/media.py`: `MediaService.artifact(manifest, artifact_id) -> ServedArtifact`.

### Limits (constants in `media/models.py`)

| Constant | Value |
| --- | --- |
| `MEDIA_SCHEMA_VERSION` | `1` |
| `MIN_MEDIA_DIMENSION_PX` / `MAX_MEDIA_DIMENSION_PX` | `64` / `2048` |
| `MAX_STATIC_BYTES` | `1_000_000` |
| `MAX_ANIMATED_BYTES` | `4_000_000` |
| `MIN_ANIMATION_MS` / `MAX_ANIMATION_MS` | `1_000` / `6_000` |
| `MAX_ALT_TEXT_CHARS` | `1_000` |
| `MAX_HIGHLIGHT_CHARS` | `280` |
| `MAX_SOURCE_LABEL_CHARS` | reuse `story_drafting` value `120` (redeclare locally, same number) |
| `MAX_ANIMATION_BEATS` | `4` |
| `MAX_MANIFEST_JSON_BYTES` | `8_192` |

### Contracts (all `frozen=True, extra="forbid"`)

- `MediaType(StrEnum)`: `PNG="image/png"`, `WEBP="image/webp"`, `GIF="image/gif"` (the MIME
  allowlist). `MEDIA_EXTENSIONS = {PNG: "png", WEBP: "webp", GIF: "gif"}` and
  `MEDIA_SIGNATURES = {PNG: b"\x89PNG\r\n\x1a\n", WEBP: b"RIFF" + 4 bytes + b"WEBP", GIF: b"GIF89a"}`.
- `MediaKind(StrEnum)`: `STATIC="static"`, `ANIMATED="animated"`.
- `MediaConcept(StrEnum)`: `HEADLINE_CARD="headline_card"` (only value for now).
- `AnimationBeat(StrEnum)`: `HEADLINE_REVEAL`, `FACT_EMPHASIS`, `SOURCE_FOOTER`.
- `MediaOutcome(StrEnum)`: `GIF="gif"`, `STATIC="static"`, `STATIC_FALLBACK="static_fallback"`,
  `TEXT_ONLY="text_only"`.
- `MediaBrief`: `schema_version`, `story_id` (1..64), `concept`, `headline` (1..160),
  `highlight: str | None` (1..280), `highlight_fact_ids: list[FactIdText]` (max 4, unique),
  `source_label: str | None` (1..120), `animation_beats: list[AnimationBeat]` (1..4, unique,
  `FACT_EMPHASIS` only if `highlight` is set), `alt_text` (1..1000). All text fields: collapse
  control characters and whitespace, strip, then reject URL markers (`http://`, `https://`,
  `www.`) and the characters `<`, `>` and `` ` ``. There is no field for HTML, CSS, scripts,
  paths, URLs, colours or commands; `extra="forbid"` keeps it that way.
- `MediaArtifact`: `artifact_id` (pattern `^art-v1-[0-9a-f]{16}$`), `kind`, `media_type`,
  `width`, `height` (64..2048), `duration_ms: int | None`, `byte_size` (>= 1), `alt_text`
  (1..1000, same cleaning, must be non-blank), `content_hash` (pattern `^[0-9a-f]{64}$`),
  `relative_path`. Validators: `relative_path` must equal exactly
  `f"{content_hash}.{MEDIA_EXTENSIONS[media_type]}"` (so no `/`, `\`, `..`, absolute or foreign
  names can pass); `STATIC` requires `png` or `webp`, `duration_ms is None`, and
  `byte_size <= MAX_STATIC_BYTES`; `ANIMATED` requires `gif`, `duration_ms` in 1000..6000, and
  `byte_size <= MAX_ANIMATED_BYTES`.
- `MediaManifest`: `schema_version`, `story_id`, `story_content_hash` (64 hex), `brief_hash`
  (64 hex), `outcome`, `static_artifact: MediaArtifact | None = None`,
  `animated_artifact: MediaArtifact | None = None`. Invariants: `GIF` needs both (static is the
  poster/fallback, kinds must match their slot, IDs distinct); `STATIC` and `STATIC_FALLBACK` need
  only `static_artifact`; `TEXT_ONLY` needs neither. `model_dump_json()` must be
  `<= MAX_MANIFEST_JSON_BYTES` bytes (UTF-8), else reject. Method
  `artifact(artifact_id) -> MediaArtifact | None` looks up only the two slots.
- `RenderMediaInput`: `brief: MediaBrief`, `kind: MediaKind`.
- `StoryMediaInput` (in `briefs.py`): `story_id`, `package: StoryPackage`, `facts: FactSet`,
  `include_animation: bool = True`. Validators: `package.story_id == story_id == facts.story_id`;
  `package.media is None`.
- `StoryMediaResult`: `manifest: MediaManifest`.

### Hashes and IDs (deterministic, pure)

- Canonical JSON everywhere: `json.dumps(obj, sort_keys=True, separators=(",", ":"),
  ensure_ascii=False).encode("utf-8")`, SHA-256, lowercase hex.
- `story_content_hash(package)`: hash of `package.model_dump(mode="json", exclude={"media"})`
  (full 64 hex). `media_workflow_id_for(package) = f"media-v1-{story_content_hash(package)[:16]}"`.
- `media_brief_hash(brief)`: hash of `brief.model_dump(mode="json")` (64 hex).
- `artifact_id_for(brief, kind, renderer_name, renderer_version)`:
  `"art-v1-" + sha256(canonical {"brief": ..., "kind": ..., "renderer": ..., "version": ...})[:16]`.
  Identical briefs with the same kind and renderer give identical IDs.
- `content_hash` is the SHA-256 of the stored bytes.

### Brief builder (code only, no LLM in this task)

`media_brief_for(package, facts) -> MediaBrief`: headline = `package.headline`; highlight = the
first key point whose `fact_ids` is non-empty and all present in `facts.facts`, with those IDs as
`highlight_fact_ids`; otherwise `package.summary` cut to 280 chars at the last space with a
trailing `…` when cut, and no fact IDs; `source_label` = first `source_links[].label` or `None`;
beats = `[HEADLINE_REVEAL, FACT_EMPHASIS, SOURCE_FOOTER]` (drop `FACT_EMPHASIS` if no highlight);
`alt_text` = `f"Story card: {headline}. {highlight}"` (or just the headline part), cut to 1000.
If cleaning rejects text (for example a key point that is all `<>`), fall back to the summary,
then to headline only. It never raises for a valid `StoryPackage`.

### Renderers

- `RenderedMedia` (frozen dataclass, never crosses Temporal): `data: bytes`, `media_type`,
  `width`, `height`, `duration_ms: int | None`.
- `MediaRenderer` Protocol: `name: str`, `version: str`,
  `render_static(brief) -> RenderedMedia`, `render_animated(brief) -> RenderedMedia`. Renderers
  raise `MediaRenderError` for transient failures.
- `MediaProviderName(StrEnum)`: `FAKE="fake"`. `MEDIA_RENDERERS: Mapping[MediaProviderName,
  Callable[[], MediaRenderer]]`; `renderer_for(name)` raises `UnknownMediaProviderError`.
- `FakeMediaRenderer(fail_static_times=0, fail_animated_times=0, oversize=False,
  wrong_signature=False)`: name `"fake"`, version `"1"`. Static: PNG, 1200x675, bytes = PNG
  signature + `sha256(canonical brief + "static")` digest. Animated: GIF, 1200x675, 4000 ms, GIF89a
  + digest. Raises `MediaRenderError` for the first N calls of each kind (counted per instance),
  `oversize` pads past the byte limit, `wrong_signature` returns GIF bytes labelled PNG. Bytes are
  deterministic but not decodable images; 058 (plan) adds the real card renderer.

### Storage (`media/storage.py`)

- `write_artifact(media_root: Path, data: bytes, media_type: MediaType) -> StoredArtifact`
  (`content_hash`, `relative_path`, `byte_size`). `mkdir(parents=True, exist_ok=True)` on the
  resolved root. If `root / relative_path` already exists as a regular file of the same size, skip
  the write (idempotent). Otherwise write to `tempfile.mkstemp(dir=root, prefix=".media-",
  suffix=".tmp")` and `os.replace` into place; unlink the temp file on any exception (same pattern
  as `render.py`).
- `resolve_artifact_path(media_root: Path, artifact: MediaArtifact) -> Path`: resolve root and
  `root / artifact.relative_path`; raise `MediaArtifactMissing` unless the result
  `is_relative_to(root)`, `is_file()`, and its size equals `artifact.byte_size`. Symlinks that
  resolve outside the root are rejected by the containment check.

### Activities (`media/activities.py`)

`render_static_media(RenderMediaInput) -> MediaArtifact` and
`render_animated_media(RenderMediaInput) -> MediaArtifact` (the `kind` field must match the
method, else non-retryable `MediaRenderInvalid`). Each: call the renderer in
`asyncio.to_thread`, then validate before writing: `MEDIA_SIGNATURES[media_type]` prefix matches,
media type allowed for the kind, and byte size within the kind limit; `MediaRenderError` →
`ApplicationError(type="MediaRenderFailed")` (retryable); bad output →
`ApplicationError(type="MediaRenderInvalid", non_retryable=True)`; too big →
`ApplicationError(type="MediaArtifactTooLarge", non_retryable=True)`. Then `write_artifact` and
return the `MediaArtifact` with `artifact_id_for(...)` and `alt_text = brief.alt_text`. Bytes are
never returned. Running twice with the same input yields an equal artifact and one file.

### Workflow (`story_media.py`)

`StoryMediaWorkflow.run(StoryMediaInput) -> StoryMediaResult`, imports passed through like
`story_generation.py`, activities called by name:

1. `brief = media_brief_for(input.package, input.facts)` (pure, deterministic).
2. Execute `render_static_media` (`start_to_close_timeout=60s`,
   `RetryPolicy(maximum_attempts=3, non_retryable_error_types=["MediaRenderInvalid",
   "MediaArtifactTooLarge"])`). On `ActivityError` (re-raise if cancelled) → `TEXT_ONLY`, no GIF
   attempt.
3. If `include_animation` is false → `STATIC`.
4. Execute `render_animated_media` (`start_to_close_timeout=120s`, same retry policy). On
   `ActivityError` (re-raise if cancelled) → `STATIC_FALLBACK`; else → `GIF`.
5. Return the manifest with `story_content_hash(package)` and `media_brief_hash(brief)`.

Render failures never fail the workflow. No signals, queries, search attributes, timers, clock,
randomness or logging of bytes. The workflow ID convention is `media_workflow_id_for(package)`;
callers (plan row 060) use it.

### Story package field and replay

- Add `media: MediaManifest | None = None` to `StoryPackage` only (not `StoryDraftContent`, so the
  writer tool schema is unchanged).
- In `story_drafting/writing.py` and `story_drafting/critique.py`, every
  `StoryPackage.model_dump(mode="json")` that feeds a prompt uses `exclude={"media"}` so prompts
  stay byte-identical to today.
- Before editing any model, run a `StoryGenerationWorkflow` on `main` code through to `ready`
  (fake activities from `tests/story_generation_fixtures.py`) and save its history as
  `tests/fixtures/histories/story_generation_v1_ready.json`; it must then replay against the new
  code.

### Configuration and deploy

- `Settings.media_dir: str = DEFAULT_MEDIA_DIR` (`"media"`, `min_length=1`), read from `MEDIA_DIR`
  in `load_settings` (blank → default). No other new settings.
- `infra/compose.prod.yaml`: `MEDIA_DIR: /data/media` and volume `media:/data/media` on both
  `worker` and `web`; declare the `media` named volume.
- `Dockerfile`: create and `chown app` `/data/media` next to `/data/charts`.
- `.gitignore`: add `media/`.

### Serving helper (`services/media.py`)

`MediaService(settings)` with `artifact(manifest: MediaManifest, artifact_id: str) ->
ServedArtifact` (`path: Path`, `media_type: str`). Looks the ID up only via
`manifest.artifact(artifact_id)`, re-checks `media_type` is in the `MediaType` allowlist, and calls
`resolve_artifact_path(Path(settings.media_dir), artifact)`; any miss raises
`MediaArtifactNotFound`. It never accepts a path, filename or MIME type from the caller.

## Acceptance criteria

- [ ] `MediaBrief`, `MediaArtifact` and `MediaManifest` reject: unsafe or mismatched
      `relative_path` (`../x.png`, `/etc/passwd`, `a/b.png`, `x.png`, wrong extension, hash
      mismatch), MIME types outside the allowlist and kind/MIME mismatches, blank or oversized alt
      text, dimensions outside 64..2048, duration on static or missing/out-of-range duration on
      animated, oversized byte sizes, inconsistent outcome/slot combinations, and manifests over
      8,192 JSON bytes; briefs reject URLs, `<`, `>`, backticks and unknown fields.
- [ ] Hashes and IDs are deterministic: identical briefs give identical `artifact_id` and
      `brief_hash`; a different brief, kind or renderer version changes the ID;
      `media_workflow_id_for` is `media-v1-<16 hex>` and ignores `package.media`.
- [ ] Storage is content-addressed and atomic: writing the same bytes twice leaves one
      `<sha256>.<ext>` file and no `.media-*.tmp` files, a failed write leaves no temp file, and
      `resolve_artifact_path`/`MediaService.artifact` refuse unknown IDs, missing files, size
      mismatches and symlinks escaping the root.
- [ ] `StoryMediaWorkflow` (time-skipping tests, fake renderer) returns `GIF`, `STATIC`,
      `STATIC_FALLBACK` and `TEXT_ONLY` for the matching cases, retries transient render failures
      up to 3 attempts, does not retry `MediaRenderInvalid`/`MediaArtifactTooLarge`, never fails on
      render errors, and its history contains no PNG/GIF signature bytes.
- [ ] `StoryPackage` has `media: MediaManifest | None = None`; old package JSON without `media`
      validates; writer/critique prompts and the writer tool schema are unchanged; the recorded
      pre-change `StoryGenerationWorkflow` history replays.
- [ ] `MEDIA_DIR` is read only in `config.py`, prod compose mounts the same `media` volume in worker
      and web, and `just lint` and `just test` pass.

## Files to touch

- `src/ai_trend_pipeline/media/__init__.py` (new)
- `src/ai_trend_pipeline/media/models.py` (new)
- `src/ai_trend_pipeline/media/briefs.py` (new)
- `src/ai_trend_pipeline/media/storage.py` (new)
- `src/ai_trend_pipeline/media/providers.py` (new)
- `src/ai_trend_pipeline/media/fake.py` (new)
- `src/ai_trend_pipeline/media/activities.py` (new)
- `src/ai_trend_pipeline/story_media.py` (new)
- `src/ai_trend_pipeline/services/media.py` (new)
- `src/ai_trend_pipeline/story_drafting/models.py` (`StoryPackage.media` field)
- `src/ai_trend_pipeline/story_drafting/writing.py` (prompt dump excludes `media`)
- `src/ai_trend_pipeline/story_drafting/critique.py` (prompt dump excludes `media`)
- `src/ai_trend_pipeline/config.py` (`media_dir`, `MEDIA_DIR`)
- `infra/compose.prod.yaml`, `Dockerfile`, `.gitignore`
- `tests/fixtures/histories/story_generation_v1_ready.json` (new, recorded from `main` code)
- `tests/test_media_models.py`, `tests/test_media_briefs.py`, `tests/test_media_storage.py`,
  `tests/test_media_activities.py`, `tests/test_story_media_workflow.py`,
  `tests/test_media_service.py` (new)
- `tests/test_story_generation_replay.py`, `tests/test_story_drafting_models.py`,
  `tests/test_story_drafting_activities.py`, `tests/test_story_critique_activities.py`,
  `tests/test_config.py` (add cases)
- `docs/architecture/tech.md` (`MEDIA_DIR` config row; short media contracts note) and
  `docs/architecture/README.md` (data table row: media directory, file name `<sha256>.<ext>`)

## Tests to write

`tests/test_media_models.py`
- `test_artifact_rejects_unsafe_relative_paths` - parametrized traversal, absolute, nested, bare,
  wrong-extension and hash-mismatch paths all fail.
- `test_artifact_rejects_disallowed_or_mismatched_mime` - `image/svg+xml`, `text/html`, static GIF
  and animated PNG fail.
- `test_artifact_rejects_missing_or_oversized_alt_text` - empty, whitespace-only and 1,001 chars.
- `test_artifact_rejects_invalid_dimensions_and_duration` - 63/2049 px, static with duration,
  animated without or with 999/6001 ms.
- `test_artifact_rejects_oversized_byte_size_per_kind`.
- `test_manifest_outcome_invariants` - each outcome with wrong slots fails; valid ones pass.
- `test_manifest_rejects_oversized_json` - a manifest over 8,192 bytes fails.
- `test_brief_rejects_urls_markup_and_extra_fields`.
- `test_identical_briefs_give_identical_artifact_ids_and_hashes` and
  `test_artifact_id_changes_with_brief_kind_or_renderer_version`.

`tests/test_media_briefs.py`
- `test_brief_uses_first_supported_key_point` - IDs present in the FactSet.
- `test_brief_falls_back_to_trimmed_summary` - no supported key point; ≤280 chars, ends with `…`.
- `test_brief_handles_unicode_and_markup_like_text_without_raising`.
- `test_story_content_hash_and_workflow_id_ignore_media`.
- `test_story_media_input_rejects_mismatched_ids_or_existing_media`.

`tests/test_media_storage.py`
- `test_write_is_content_addressed_and_idempotent` - same bytes twice, one file, no temp files.
- `test_failed_write_removes_temp_file` - patch `os.replace` to raise.
- `test_resolve_rejects_missing_file_size_mismatch_and_escaping_symlink`.

`tests/test_media_activities.py`
- `test_render_static_stores_file_and_returns_bounded_artifact` - no bytes in the result.
- `test_rerun_with_same_input_is_idempotent`.
- `test_transient_render_error_is_retryable_application_error`.
- `test_wrong_signature_and_oversize_are_non_retryable`.
- `test_kind_mismatch_is_non_retryable`.

`tests/test_story_media_workflow.py` (`WorkflowEnvironment.start_time_skipping()`, fake renderer)
- `test_gif_outcome_has_static_and_animated_artifacts`.
- `test_static_outcome_when_animation_disabled`.
- `test_static_fallback_when_animation_fails_after_retries`.
- `test_text_only_when_static_fails_and_animation_not_attempted`.
- `test_transient_failures_recover_within_three_attempts`.
- `test_non_retryable_render_errors_are_attempted_once`.
- `test_history_contains_no_media_bytes` - fetch history, assert no `\x89PNG`/`GIF89a` in any
  payload.
- `test_same_package_produces_same_manifest` - two runs, equal manifests.

`tests/test_media_service.py`
- `test_artifact_resolves_by_manifest_id`; `test_unknown_id_missing_file_and_text_only_manifest_raise_not_found`.

Existing files
- `tests/test_story_generation_replay.py`: `test_pre_media_story_generation_history_replays`.
- `tests/test_story_drafting_models.py`: `test_story_package_without_media_field_validates` and
  `test_writer_tool_schema_has_no_media_field`.
- `tests/test_story_drafting_activities.py` / `tests/test_story_critique_activities.py`:
  `test_prompt_does_not_include_media` (prompt text identical with `media=None`).
- `tests/test_config.py`: `test_media_dir_default_and_env`.

## Out of scope

- Any real renderer, fonts, design tokens, Pillow or other new dependency (plan rows 058-059).
- Starting `StoryMediaWorkflow` from `StoryGenerationWorkflow`, worker registration, a
  `MEDIA_PROVIDER` setting, review/approval or content-hash changes to the generation flow (plan
  row 060).
- Any web route, template, CSS, view model or UI preview; `MediaService` is not wired into FastAPI
  dependencies yet.
- LLM-produced media briefs, AI illustration, stock/GIF providers, remote assets, scraping.
- Publishing, Bluesky, `PostChartInput` or chart rendering changes.
- Search attributes, queries, signals or `/stories` visibility for the media workflow; cleanup of
  old media files.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: Wrote the spec for plan row 057, renumbered to 058. No UI, so I skipped the
`frontend-design` step. The task stays one PR: contracts, storage, fake renderer and the standalone
child workflow, with no worker wiring or route (those are plan row 060). `StoryPackage.media` is
added now with prompts excluding it, and a pre-change history is recorded for replay. Next: the
orchestrator approves, then the builder records the history fixture on `main` first.

**builder**: Built on `task/058-media-foundation`: media package, workflow, service, config/deploy, docs, tests; history fixture recorded on `main` code before model edits. Full suite and lint pass.
Notes: WEBP signature is `RIFF` prefix plus `WEBP` at byte 8 via `has_media_signature`; `providers.py` builds the fake lazily to avoid an import cycle with `fake.py`; added `tests/media_fixtures.py` (not listed).
