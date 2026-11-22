# 073: Image backend seam

Status: merged

## Goal

Put the Claude call in `DrawnImageActivities` behind a small `ImageBackend` protocol and a registry
in `media/images/`, so a later fal backend (074) is a new registry entry rather than a change to the
activity. Today's drawings become the `anthropic-svg` backend. `DRAWN_IMAGES_PROVIDER` picks the
backend, and the drawn pointer records which one ran. This is a pure refactor: nothing a reader sees
changes, nothing in the workflow changes, and there is no fal code.

## Design

**New package `src/ai_trend_pipeline/media/images/`** (it follows the `media/providers.py` pattern):

- `models.py` holds provider-neutral contracts. These are frozen Pydantic models with
  `extra="forbid"`. They never cross a Temporal boundary.
  - `ImageBrief`: `headline: str`, `summary: str` (both already cleaned with `clean_story_text`),
    `roles: tuple[DrawnRole, ...]` (1 to `MAX_DRAWN_IMAGES`, from `requested_roles(count)`).
  - `GeneratedImage`: `role: DrawnRole`, `png: bytes`, `width: int`, `height: int`.
  - `ImageBatch`: `images: tuple[GeneratedImage, ...] = ()`, `rejects: dict[str, int] = {}`
    (reason value to count), `input_tokens: int = 0`, `output_tokens: int = 0`. An empty `images`
    means "nothing usable" and covers a max_tokens reply, no tool call, a malformed reply and every
    drawing rejected.
- `backends.py` holds the protocol, the errors and the registry.
  - `class ImageBackendName(StrEnum)`: `ANTHROPIC_SVG = "anthropic-svg"`, `FAKE = "fake"`.
  - `class ImageBackend(Protocol)`: `name: ImageBackendName` and
    `async def generate(self, brief: ImageBrief) -> ImageBatch`.
  - Errors: `ImageBackendTransient` (retryable) and `ImageBackendRefused` (never retried). Both are
    raised `from None` and carry fixed messages, never model or story text.
    `UnknownImageBackendError` is raised for an unknown name.
  - `@dataclass(frozen=True) class ImageBackendDeps`: `anthropic_client: anthropic.AsyncAnthropic`,
    `model: str`.
  - `IMAGE_BACKENDS: Mapping[ImageBackendName, Callable[[ImageBackendDeps], ImageBackend]]`. Each
    factory imports its backend module lazily, the same way `providers.py` does.
  - `image_backend_for(name: ImageBackendName | str, deps: ImageBackendDeps) -> ImageBackend` maps
    an invalid name to `UnknownImageBackendError` with the message
    `Unknown image backend {name!r}`.
- `anthropic_svg.py` defines `AnthropicSvgBackend(client, model)`. It moves `_call_api`,
  `_parse_reply` and the render loop of `_keep_images` (role filtering against `brief.roles`, dedupe
  by role, stop at `len(brief.roles)`, `render_drawn_svg`, counting `DrawingRejected` reasons) out
  of the activity unchanged. `max_tokens = MAX_DRAW_TOKENS_PER_IMAGE * len(brief.roles) +
  DRAW_TOKENS_OVERHEAD`, which equals today's value. The constants `DRAW_REQUEST_TIMEOUT_S`,
  `MAX_DRAW_TOKENS_PER_IMAGE`, `DRAW_TOKENS_OVERHEAD` and `TRANSIENT_STATUSES` move here. The
  activity calls the backend outside any `except` block, as today, so no SDK exception is chained.
  Transient SDK errors raise `ImageBackendTransient`. Other `APIStatusError` and `APIError` raise
  `ImageBackendRefused`. The status mapping stays exactly as it is now.
- `fake.py` defines `FakeImageBackend`. It is deterministic and makes no network calls. By default
  it returns one small valid 1200x900 PNG per requested role. The PNG is generated with Pillow and
  stays under `MAX_STATIC_BYTES`. Constructor options make it return an empty batch, raise
  transient, or raise refused. It records every brief it receives in `self.briefs`.

**`media/drawn/activities.py`**:

- The constructor keeps its current signature, so existing tests construct it unchanged. It gains
  one keyword argument, `backend: ImageBackend | None = None`. When the argument is `None`, the
  activity uses `AnthropicSvgBackend(client, model)`.
- `_draw_locked` keeps its order: pointer check, clean text, empty-headline check, cap, then
  `backend.generate(ImageBrief(...))`. It then writes artifacts, builds the
  `MediaArtifact`/`DrawnArtifact` with the same `drawn_artifact_id_for(...)` inputs (provider is
  **not** added to the artifact id), sorts by `DRAWN_ROLE_ORDER`, writes the pointer and logs.
  `ImageBackendTransient` and `ImageBackendRefused` map to today's `ApplicationError`s with the
  same messages and types (`DrawingTransient`, and `DrawingFailed` with non_retryable). They are
  raised outside the `except` block.
- The log line format stays byte-identical: `draw_story_images status=%s kept=%d rejects=%s
  input_tokens=%d output_tokens=%d`.
- Re-export `MAX_DRAW_TOKENS_PER_IMAGE` and `DRAW_TOKENS_OVERHEAD` from `drawn/activities.py`,
  because `tests/test_drawn_activities.py` imports them from there.
- Any existing pointer still short-circuits, whatever provider it records.

**`media/drawn/store.py`**: add `provider: str = Field(default="anthropic-svg", min_length=1,
max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")` to `DrawnPointer`. Keep `schema_version` at
`Literal[1]`, so pointers already on disk (without `provider`) still read and resolve as
`anthropic-svg`. The activity writes `provider=backend.name.value`. `store.py` must not import
from `media/images/`. Use the literal default and let a test pin it equal to
`ImageBackendName.ANTHROPIC_SVG`.

**Config and wiring**:

- In `config.py`, add `drawn_images_provider: ImageBackendName = ImageBackendName.ANTHROPIC_SVG`
  and `_parse_drawn_images_provider(raw)`. Parsing works like `_parse_media_provider`: strip,
  lowercase, empty means the default, and an unknown value raises `ValueError` with
  `Unknown drawn images provider {token!r}. Registered providers: anthropic-svg, fake`. The
  variable is read from `DRAWN_IMAGES_PROVIDER`.
- In `worker.py`, `media_activities` builds the backend with
  `image_backend_for(settings.drawn_images_provider, ImageBackendDeps(anthropic_client,
  settings.drawn_images_model))` and passes `backend=` to `DrawnImageActivities`.
- In `.env.example`, add `# DRAWN_IMAGES_PROVIDER=anthropic-svg` under the other `DRAWN_IMAGES_*`
  lines. In `infra/compose.prod.yaml`, add `DRAWN_IMAGES_PROVIDER: ${DRAWN_IMAGES_PROVIDER:-}` next
  to the other `DRAWN_IMAGES_*` entries.

## Acceptance criteria

- [ ] `tests/test_drawn_activities.py`, `tests/test_drawn_store.py`, `tests/test_drawn_prompt.py`,
      `tests/test_drawn_pipeline.py`, `tests/test_story_media_workflow.py` (including the
      `story_media_v3_drawn.json` replay) and `tests/test_worker.py` pass. Their existing test
      functions and fixtures are not edited. New test functions may be appended.
- [ ] `media/images/` provides `ImageBackend`, `ImageBrief`, `ImageBatch`, `GeneratedImage`,
      `ImageBackendName`, `IMAGE_BACKENDS`, `image_backend_for`, `AnthropicSvgBackend` and
      `FakeImageBackend` as described above. `drawn/activities.py` no longer imports `anthropic`
      types other than the client type in its constructor, and makes no Messages API call itself.
- [ ] `DRAWN_IMAGES_PROVIDER` defaults to `anthropic-svg`, accepts `fake`, and rejects unknown
      values with an error that names the registered providers. It is read only in `config.py`.
      It is listed in `.env.example` and passed through in `infra/compose.prod.yaml`.
- [ ] Every newly written drawn pointer records `provider`. A pointer file written before this
      task (without `provider`) still reads, resolves to `anthropic-svg` and short-circuits.
- [ ] No workflow file, activity name, activity input/output model, artifact id formula or log
      line changes. `just lint` and `just test` pass.

## Files to touch

- `src/ai_trend_pipeline/media/images/__init__.py` (new)
- `src/ai_trend_pipeline/media/images/models.py` (new)
- `src/ai_trend_pipeline/media/images/backends.py` (new)
- `src/ai_trend_pipeline/media/images/anthropic_svg.py` (new)
- `src/ai_trend_pipeline/media/images/fake.py` (new)
- `src/ai_trend_pipeline/media/drawn/activities.py`
- `src/ai_trend_pipeline/media/drawn/store.py`
- `src/ai_trend_pipeline/config.py`
- `src/ai_trend_pipeline/worker.py`
- `.env.example`
- `infra/compose.prod.yaml`
- `tests/test_image_backends.py` (new)
- `tests/test_drawn_activities_backend.py` (new)
- `tests/test_drawn_store.py` (append only)
- `tests/test_config.py` (append only)
- Scribe: `README.md` (settings paragraph) and `docs/architecture/tech.md` (settings table, media
  section)

## Tests to write

- `test_image_backends.py`: `image_backend_for("anthropic-svg", deps)` returns an
  `AnthropicSvgBackend` and `image_backend_for("fake", deps)` returns a `FakeImageBackend`. Each
  has the matching `name`.
- `test_image_backends.py`: `image_backend_for("nope", deps)` and `image_backend_for("", deps)`
  raise `UnknownImageBackendError`.
- `test_image_backends.py`: every `ImageBackendName` has a registry entry and the registry has no
  extras.
- `test_image_backends.py`: `FakeImageBackend` returns one valid PNG per requested role, in role
  order, each within `MAX_STATIC_BYTES`. It records the brief. The empty, transient and refused
  modes behave as configured.
- `test_image_backends.py`: `AnthropicSvgBackend` against the existing `llm_draw_story_images_*`
  fixtures over `httpx.MockTransport` returns 3 images for clean, fewer with rejects for partial,
  and an empty batch for all-rejected, malformed, no-tool and max_tokens. A 429/500/connect error
  raises `ImageBackendTransient`. A 400 raises `ImageBackendRefused`, and neither error's text or
  `__cause__`/`__context__` contains the response body.
- `test_drawn_activities_backend.py`: with `backend=FakeImageBackend()`, the activity makes no
  HTTP request, returns `DRAWN` with images sorted by role, and writes a pointer with
  `provider == "fake"`.
- `test_drawn_activities_backend.py`: the default construction (no `backend=`) writes a pointer
  with `provider == "anthropic-svg"`.
- `test_drawn_activities_backend.py`: a fake empty batch writes a `failed` pointer and returns
  `FAILED`. Fake transient raises `ApplicationError` of type `DrawingTransient` (retryable). Fake
  refused raises `DrawingFailed` with `non_retryable=True`.
- `test_drawn_activities_backend.py`: an existing pointer short-circuits before the backend is
  called (`fake.briefs == []`). When disabled or capped, the backend is not called.
- `test_drawn_store.py`: a pointer JSON without `provider` reads back with
  `provider == "anthropic-svg"`, which equals `ImageBackendName.ANTHROPIC_SVG.value`.
- `test_config.py`: `DRAWN_IMAGES_PROVIDER` unset or empty gives `anthropic-svg`. `fake` and
  ` Fake ` give `fake`. `dall-e` raises a `ValueError` whose message contains
  "Unknown drawn images provider" and `anthropic-svg, fake`.

## Out of scope

- Any fal code, `fal-client` dependency, `FAL_*` settings, pending pointers or request ids, or a
  two-phase `submit`/`collect` API. Task 074 extends the protocol for async backends.
- Changes to `StoryMediaWorkflow`, workflow patches, `MediaManifest`, `DrawImagesInput`,
  `DrawnImagesResult`, activity names or timeouts.
- Changes to the prompt, the SVG pipeline, artifact ids, the daily cap semantics, the log line or
  the review UI.
- Carousel models (`CarouselPlan`, critic, templates). Those belong to tasks 075-077.
- Editing or re-recording existing tests or fixtures, including `story_media_v3_drawn.json`.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: Wrote spec for a pure refactor. A single-method `ImageBackend.generate(brief) ->
ImageBatch` is defined now; 074 adds submit/collect for fal. The pointer gains a defaulted
`provider` (schema stays v1, so old pointers read). The activity constructor stays
backward-compatible, so existing tests run unedited. No UI. Next: orchestrator approves (`no`
task), then builder.

**builder**: Added `media/images/` (models, backends registry, anthropic_svg, fake); activity now calls `backend.generate` and writes pointer `provider`; config/worker/.env.example/compose wired. New tests in test_image_backends.py, test_drawn_activities_backend.py, plus appends to store/config tests. Targeted tests, ruff and pyright pass. Next: verifier (full suite).

**verifier**: Ran `just lint` (ruff check, ruff format, pyright strict: all pass) and `env -u ANTHROPIC_BASE_URL -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_API_KEY uv run pytest`: 1972 passed, 0 failed. Only 2 pydantic serializer warnings, pre-existing. All checks pass. Next: reviewer.

**reviewer**: Approved. Diff matches spec; existing tests/fixtures untouched, workflow and log line unchanged, errors raised outside except blocks. Notes: scribe still owes README and tech.md updates; activity keeps unused-looking `self._model` only for artifact ids (fine).

**scribe/orchestrator**: Docs updated (README, tech.md). PR opened https://github.com/akonale/ai-trend-pipeline/pull/91 (scribe hit expired gh auth).
