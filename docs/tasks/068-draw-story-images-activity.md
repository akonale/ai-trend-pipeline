# 068: `draw_story_images` activity (Anthropic, idempotent, capped)

Status: built

## Goal

Add the activity that asks Claude for 1-4 drawn SVGs for one story, turns the valid ones into
content-addressed PNGs, and returns only bounded metadata. A story is drawn at most once. The
activity respects the kill switch and a per-day call cap. It is not registered in any worker or
workflow yet; task 069 does that.

## Context

- This task builds on 067: `DrawImagesInput`, `DrawnImagesResult`, `DrawnArtifact`,
  `DrawnStatus`, `drawn_artifact_id_for`, `media/drawn/prompt.py`, `render_drawn_svg` and the
  `DRAWN_IMAGES_*` settings.
- Pattern to copy: `story_drafting/writing.py` (the tool param from a Pydantic schema, finding the
  `ToolUseBlock`, and validating with the model), with one change: the tool is **forced**.
  Storage reuses `media/storage.py` (`write_artifact`, `resolve_artifact_path`).
- Test pattern to copy: `tests/test_story_drafting_activities.py`. It uses a real
  `anthropic.AsyncAnthropic` with `httpx.MockTransport` and JSON fixtures in the Messages API
  response shape.
- **Temporal workflow code:** none. This is activity code only.

## Decisions

### Class and constructor (`media/drawn/activities.py`)

```python
class DrawnImageActivities:
    def __init__(
        self,
        client: anthropic.AsyncAnthropic,
        media_root: Path,
        *,
        enabled: bool,
        model: str,
        count: int,
        daily_cap: int,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None: ...

    @activity.defn(name=DRAW_ACTIVITY)  # "draw_story_images"
    async def draw_story_images(self, data: DrawImagesInput) -> DrawnImagesResult: ...
```

Constants:

- `DRAW_ACTIVITY = "draw_story_images"`
- `DRAWING_TRANSIENT_ERROR_TYPE = "DrawingTransient"`
- `DRAWING_FAILED_ERROR_TYPE = "DrawingFailed"`
- `DRAW_REQUEST_TIMEOUT_S = 100.0`
- `MAX_DRAW_TOKENS_PER_IMAGE = 1400`
- `DRAW_TOKENS_OVERHEAD = 400`

### Steps, in order

1. **Kill switch.** If `enabled` is false, return `DrawnImagesResult(status=disabled)`. Touch no
   file and make no HTTP call.
2. **Per-story lock.** Take an exclusive `fcntl.flock` on
   `<media_root>/drawn/<story_content_hash>.lock`, and hold it until return. Blocking lock calls
   run in `asyncio.to_thread`.
3. **Pointer check.** The pointer file is `<media_root>/drawn/<story_content_hash>.json`. If it
   exists:
   - If it parses as `DrawnPointer` and every image resolves through `resolve_artifact_path`,
     return its status and images.
   - Otherwise (corrupt pointer or missing PNG), return `failed`.

   Never call the API when a pointer exists. This is the "never pay twice, never regenerate
   without a human" rule.
4. **Clean the input.** Run `clean_story_text` on the headline (160) and the summary (600). An
   empty headline returns `failed` with no call, no usage and no pointer.
5. **Daily cap.** Under `<media_root>/drawn/usage.lock`, read
   `<media_root>/drawn/usage/<UTC YYYY-MM-DD>.json` (`DrawnUsage`). The date comes from `clock()`.
   - If `calls >= daily_cap`, return `capped`, with no call and no pointer.
   - Otherwise increment and write atomically **before** the call. Every API attempt counts, which
     is conservative.
6. **Call the API.** Use
   `client.with_options(max_retries=0, timeout=DRAW_REQUEST_TIMEOUT_S).messages.create(...)`, so
   that Temporal is the only retry layer. Pass:
   - `model`
   - `max_tokens = MAX_DRAW_TOKENS_PER_IMAGE * count + DRAW_TOKENS_OVERHEAD`
   - `system = SYSTEM_PROMPT`
   - `tools = [draw_tool()]`
   - `tool_choice = {"type": "tool", "name": TOOL_NAME_DRAW}`
   - one user message from `build_user_prompt(headline, summary, requested_roles(count))`
7. **Error mapping.** Every error is raised `from None`, with a fixed message and no response
   text:
   - `anthropic.APIConnectionError` (which includes `APITimeoutError`), and `APIStatusError` with
     status 408, 409, 429 or 500 and above (including 529 overloaded), raise
     `ApplicationError("drawing request failed, will retry", type=DrawingTransient)`. This one is
     retryable.
   - Any other `anthropic.APIError` raises
     `ApplicationError("drawing request was refused", type=DrawingFailed, non_retryable=True)`.
   - No pointer is written in either case.
8. **Paid failures.** These write a `failed` pointer and return `failed`:
   - `stop_reason == "max_tokens"`
   - no `report_drawings` `ToolUseBlock`
   - `DrawnImagesReply.model_validate` fails (catch `ValidationError`, never log it or chain it)
9. **Keep the valid images.** Walk `reply.images` in order and keep at most `count`:
   - Skip a role that was not requested, or a duplicate role.
   - Run `render_drawn_svg` in `asyncio.to_thread`. `DrawingRejected` counts the reason and skips
     the image.
   - For a valid image, call `write_artifact` (in `to_thread`) and build `DrawnArtifact`:
     - `MediaArtifact(kind=STATIC, media_type=PNG, 1200x900)`
     - `alt_text = drawn_alt_text(clean_headline, role)`
     - `artifact_id = drawn_artifact_id_for(story_content_hash, role, stored.content_hash,
       drawn_prompt_hash(), model)`
10. **Result and pointer.** The status is `drawn` when at least 1 image is kept, otherwise
    `failed`. Write the pointer atomically (temp file, then `os.replace`) **after** the PNGs.
    Return `DrawnImagesResult`, sorted by `DRAWN_ROLE_ORDER`.
11. **One log line.** Write one `activity.logger.info` line with only these values: the status,
    the kept count, the reject reason counts, and `response.usage` input and output tokens (for
    cost). Never log SVG, tool input, headline, summary or exception text.

### Store (`media/drawn/store.py`)

- `DrawnPointer` (frozen, forbid) has these fields:
  - `schema_version = 1`
  - `story_content_hash`, matching `SHA256_PATTERN`
  - `model`
  - `prompt_hash`, matching `SHA256_PATTERN`
  - `status: Literal[drawn, failed]`
  - `images: tuple[DrawnArtifact, ...]`

  It uses the same status-image rule as `DrawnImagesResult`. It holds **metadata only, with no
  SVG**.
- `DrawnUsage` (frozen, forbid) has `day: date` and `calls: int >= 0`.
- Provide these helpers:
  - `pointer_path`
  - `read_pointer`, which returns `None` when the file is missing and raises `PointerCorrupt`
    when it is unreadable
  - `write_pointer`, which is atomic
  - `reserve_call(media_root, today, cap) -> bool`, which is atomic and locked
  - `story_lock(media_root, story_content_hash)`, a context manager
- Paths are built only from a validated hash or a `date`, so they are safe from traversal. The
  `drawn/` folder is never served, because the media route only resolves
  `<sha256>.<ext>` manifest paths.

## Acceptance criteria

- [ ] With `enabled=False` the activity returns `disabled`, sends no HTTP request and writes no
  file.
- [ ] A clean fixture gives `drawn` with 3 PNG artifacts, sorted by role. The PNGs exist under
  `media_root` as `<sha256>.png`. The request is forced to `report_drawings`, uses the configured
  model, the max-token formula and the system prompt, and exactly one HTTP request is made
  (SDK retries off).
- [ ] A second call for the same `story_content_hash` makes **zero** HTTP requests and returns the
  same result. This holds on a new activity instance, and also when the first result was a paid
  `failed`. A corrupt pointer or a missing PNG returns `failed` with no request.
- [ ] Transient errors (429, 529, 500, connection error) raise a retryable `DrawingTransient`.
  Errors 400 and 401 raise a non-retryable `DrawingFailed`. Neither writes a pointer. With the cap
  reached, the activity returns `capped` with no request, and the next UTC day (an injected clock)
  is allowed again.
- [ ] Partial success keeps the valid images and drops the hostile, unrequested and duplicate ones.
  All rejected, malformed, no-tool and `max_tokens` responses give `failed`. No SVG, headline or
  model text appears in the result JSON, the pointer file, exception messages and causes, or the
  captured logs.
- [ ] The activity is not registered in `worker.py` and not referenced by any workflow.
  `just lint` and `just test` pass with no network.

## Files to touch

- `src/ai_trend_pipeline/media/drawn/activities.py` (new)
- `src/ai_trend_pipeline/media/drawn/store.py` (new)
- Messages API response fixtures in `tests/fixtures/`, hand-written in the same shape as
  `llm_draft_story_clean.json`, with SVGs built from the 067 fixtures:
  - `llm_draw_story_images_clean.json` (3 valid)
  - `llm_draw_story_images_partial.json` (2 valid, 1 with `<script>`, 1 with an unrequested
    duplicate role)
  - `llm_draw_story_images_all_rejected.json`
  - `llm_draw_story_images_malformed.json` (an image missing `svg`)
  - `llm_draw_story_images_no_tool.json` (text only)
  - `llm_draw_story_images_max_tokens.json` (`stop_reason: max_tokens`)
- `tests/test_drawn_activities.py` (new)
- `tests/test_drawn_store.py` (new)

## Tests to write

- `test_drawn_activities.py` runs the activity through `temporalio.testing.ActivityEnvironment`,
  with a real `AsyncAnthropic` on `httpx.MockTransport`:
  - disabled: no request and no files
  - clean: request body assertions, 3 artifacts, the files exist, roles are sorted, and
    `"<svg"` does not appear in `result.model_dump_json()`
  - pointer reuse with zero requests on a fresh instance, including after a paid `failed`
  - a corrupt pointer gives `failed` with no request
  - a deleted PNG gives `failed` with no request
  - 429, 529 and 500 and `httpx.ConnectError` raise `ApplicationError(type="DrawingTransient",
    non_retryable=False)`, and 400 and 401 raise `DrawingFailed` with `non_retryable=True`.
    There is no pointer afterwards, and exactly one request is made per call.
  - cap: with `daily_cap=1`, story A is drawn and story B is `capped` with no request. B is
    allowed after the clock moves to the next UTC day. A transient failure still uses up a call.
  - partial, all rejected, malformed, no tool, and `max_tokens`
  - an empty-after-cleaning headline (only a URL) gives `failed` with no request
  - an injection headline reaches the request only inside the JSON section, cleaned
  - leak check: plant a sentinel string in the fixture's SVG and the headline, then assert that it
    is absent from the result JSON, the pointer file, `str(exc)` and its causes, and the `caplog`
    text
- `test_drawn_store.py`:
  - pointer round-trip
  - an atomic write leaves no `.tmp` file
  - `read_pointer` returns `None` for a missing file and raises `PointerCorrupt` for garbage
  - `reserve_call` counts up to the cap and resets per date
  - `DrawnPointer` rejects `capped` and `disabled`, and rejects `drawn` with no images

## Out of scope

- Registering the activity in `worker.py`, any `StoryMediaWorkflow` or manifest change, any UI or
  docs. Those are tasks 069 and 070.
- A "redraw" or regenerate action, a usage-file cleanup job, and cost dashboards.
- Streaming, extended thinking, prompt caching, other providers, and an OpenAI-compatible or
  provider-neutral layer.
- Changes to `make_anthropic_client`, the existing LLM activities, the sanitiser, the rasteriser or
  storage.
- Bluesky and publishing.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: wrote spec

**builder**: added drawn/store.py and drawn/activities.py, 6 fixtures, 2 test files. lint and full suite pass (1834). Not registered in worker. Next: verifier.
