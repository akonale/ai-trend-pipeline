# 065: AI illustration (still image)

Status: planned

## Goal

Give each story an optional cartoon-style still illustration from a hosted text-to-image API,
shown on the review page next to the story card so the human picks which picture goes out with
Approve. The story card stays the fallback that always works: a disabled, blocked, failed or
rejected illustration never blocks review or publishing.

## Provider decision

**Chosen: Google Imagen 4 Fast (`imagen-4.0-fast-generate-001`) via the Gemini API `:predict`
REST endpoint, called with `httpx`.**

- Price: about USD 0.02 per image (Imagen 4 Fast list price; the builder re-checks the current
  price and puts the date next to it in the docs).
- Safety: `personGeneration: "dont_allow"` blocks people at the provider, which is the strongest
  guarantee we can get of "no real people or recognisable likenesses". Built-in safety filters
  return a filtered reason, not an image. Every output carries an invisible SynthID watermark.
- Licensing: under the paid Gemini API terms Google does not claim ownership of outputs, allows
  commercial use, and does not train on paid-tier prompts. Prompts never contain secrets or raw
  evidence.
- Stability: one synchronous POST that returns base64 bytes. No polling, no expiring download
  URLs. The model ID is a setting, so moving to `imagen-4.0-generate-001` or a later model needs
  only a config change.

Rejected:

- **OpenAI `gpt-image-1-mini`**: cheaper at low quality (about USD 0.005-0.01), but it has no hard
  "no people" switch (moderation is only `auto`/`low`), and gpt-image models need organisation
  verification. This is the first fallback if Imagen is withdrawn; it fits the same interface.
- **Black Forest Labs FLUX 1.1 [pro]**: strong style, but about USD 0.04 per image, an async
  submit-and-poll API, and result URLs that expire after minutes. More moving parts, and a
  download from a second host.
- **Stability AI (Stable Image Core / SD3.5)**: credit-based pricing, licence terms that have
  changed several times, and no people-blocking parameter.
- **Replicate / fal aggregators**: an extra intermediary, and the licence depends on the model.
- **Recraft**: very good illustration styles, but more expensive and from a smaller vendor. We
  can revisit it if the house style matters more than cost.
- **Self-hosted Stable Diffusion**: needs a GPU on the box. Out of budget and scope.

Swappable: providers implement the `IllustrationProvider` protocol and are registered in a
static map keyed by `IllustrationProviderName`. Adding OpenAI later takes one module and one enum
value, with no workflow change.

## Design

### Prompt (code only, deterministic, in `media/illustration.py`)

`illustration_prompt_for(package: StoryPackage) -> IllustrationPrompt`. It is a pure function that
the workflow calls.

- Inputs: only `package.headline` and `package.summary`. `StoryPackage` has no topic field, so we
  do not use a topic. We never use evidence, fact text, key points, source labels, notes or URLs.
- Sanitise each input. Apply NFKC, then keep only Unicode letters, spaces and the characters
  `, . ' -`. Remove every digit. Drop all other characters, including quotes, colons, brackets,
  `#`, `@`, `$`, `%` and `/`. Collapse whitespace and strip. Cut on a word boundary: headline to
  120 characters, summary to 240. If the headline is empty after sanitising, use the fixed subject
  `a technology news story`. If the summary is empty, leave it out.
- Prompt text = `STYLE_PREFIX + " Scene idea: " + headline + "." + (" Context: " + summary + ".")
  + " " + NEGATIVE_CONSTRAINTS`. Hard cap: 1,000 characters. These two module constants are
  fixed:
  - `STYLE_PREFIX = "Simple flat cartoon editorial illustration. Bold clean outlines, rounded
    shapes, a small friendly palette of four or five flat colours, plain light background, no shading
    gradients. Calm and playful."`
  - `NEGATIVE_CONSTRAINTS = "Strict rules: no text, letters, words, numbers or digits anywhere; no
    logos, brand marks, product names or flags; no charts, graphs, maps, screens or user
    interfaces; no people, faces, hands or body parts; no real places or landmarks. Show the idea
    with simple objects and symbols only."`
  - Neither constant contains a digit, so the whole prompt is digit-free (tested).
- `IllustrationPrompt` (frozen, `extra="forbid"`) has these fields: `story_id`, `text` (1..1000),
  and `prompt_version: int = 1`. The `request_hash` is sha256 of the canonical JSON of
  `{prompt, provider, model}`, computed in the activity.
- Alt text, code-built: `"Cartoon illustration for the story: {headline}. It shows no text or
  data."` It uses the original, cleaned headline (via `clean_media_text`), not the sanitised one,
  and is cut to `MAX_ALT_TEXT_CHARS`.

### Models (`media/models.py`)

- `IllustrationStatus(StrEnum)`: `generated`, `blocked`, `failed`, `disabled`.
- `MediaSelection(StrEnum)`: `illustration`, `card`.
- `MediaManifest` gains three optional fields, all defaulting to `None`:
  `illustration_artifact: MediaArtifact | None`, `illustration_status: IllustrationStatus | None`,
  and `selected: MediaSelection | None`. Validation rules:
  - `illustration_artifact` must have kind static and type PNG or WEBP.
  - `illustration_artifact` is allowed only when `static_artifact` is set. The card must always
    exist as the fallback.
  - `illustration_status == generated` if and only if `illustration_artifact` is set.
  - `selected == illustration` requires an artifact.
  - The `illustration_artifact` ID must differ from the other artifact IDs.
  - `artifact()` also returns the illustration.
- **Hash compatibility**: these three keys are left out of `model_dump` when they are `None`
  (for example with a wrap `model_serializer`). That keeps `story_content_hash`,
  `story_package_hash` and Temporal payloads of pre-065 packages byte-identical. A golden test
  pins this.
- New activity I/O models: `IllustrateInput(prompt: IllustrationPrompt, alt_text: str)` and
  `IllustrationResult(status: IllustrationStatus, artifact: MediaArtifact | None)`. The artifact
  is present only when the status is `generated`.

### Provider and activity

- `media/illustration.py` holds the following:
  - `IllustrationProvider` protocol: `name`, `version`, and
    `illustrate(prompt: IllustrationPrompt) -> Illustration`. The current `MediaBrief`-based
    signature is unused, so replace it.
  - Errors: `IllustrationBlocked` (a safety refusal or an empty result, never retried),
    `IllustrationTransient` (timeout, 429 or 5xx, retried), and `IllustrationInvalid` (other 4xx,
    malformed JSON, bad bytes, never retried).
  - `IllustrationProviderName` with values `off`, `fake` and `imagen`, plus the registry.
- `media/imagen.py`, `ImagenIllustrationProvider(api_key, base_url, model, client)`:
  - Request: POST `{base_url}/models/{model}:predict`. The key goes in the `x-goog-api-key`
    header, never in the URL. Body:
    `{"instances":[{"prompt": text}],"parameters":{"sampleCount":1,"aspectRatio":"1:1","personGeneration":"dont_allow","includeRaiReason":true}}`.
  - httpx timeouts: connect 10 s, read 60 s. Read at most 8 MB of response; above that, raise
    `IllustrationInvalid`.
  - Parse `predictions[0].bytesBase64Encoded` and `mimeType`. No predictions, or a `raiFilteredReason`,
    raises `IllustrationBlocked`.
  - The builder checks parameter names against the current Gemini API Imagen docs. If one is not
    accepted, drop it and say so in the handoff note.
  - Never log the request body, the headers, the response body or the key. Logs contain only the
    status code and the error class.
- `media/normalize.py`, `normalize_illustration(data: bytes, declared_mime: str) -> RenderedMedia`:
  1. Allow only declared MIME types `image/png`, `image/jpeg` and `image/webp`, and check the magic
     bytes.
  2. Set `Image.MAX_IMAGE_PIXELS = 2048*2048`. Reject images with a side over 2048 or under 256.
  3. Convert to RGB, which drops alpha, EXIF and ICC metadata.
  4. Downscale with LANCZOS so the long side is at most 1024.
  5. Encode WebP (`quality=82, method=6`). If the result exceeds `MAX_STATIC_BYTES`, retry at
     quality 72 and then 62. If it is still too big, raise `IllustrationInvalid`.

  Output is deterministic for the same input bytes.
- `media/illustration_activities.py` defines `IllustrationActivities(media_root, provider |
  None)` with the activity `illustrate_story`. The steps:
  1. If `provider is None`, return `IllustrationResult(status=disabled)`. No network.
  2. Look up the pointer file `media_root/illustrations/<request_hash>.json`, which holds a
     serialised `MediaArtifact`. If it exists and `resolve_artifact_path` succeeds, return
     `generated` with that artifact and make **no API call**. This makes the activity idempotent
     and caps cost at one billed image per story content hash.
  3. Call the provider in `asyncio.to_thread`, then normalise and `write_artifact`.
  4. Build the `MediaArtifact`. `artifact_id_for` uses a brief-equivalent payload of the prompt
     plus provider name and version, so the ID differs from the card's.
  5. Write the pointer atomically (tempfile plus `os.replace`). Return `generated`.

  Error mapping:
  - `IllustrationBlocked` returns `IllustrationResult(status=blocked)`, not an error.
  - `IllustrationTransient` raises a retryable `ApplicationError` of type `IllustrationFailed`.
  - `IllustrationInvalid` raises a non-retryable `IllustrationInvalid` error.

### Workflow (`story_media.py`)

- Add `STORY_ILLUSTRATION_PATCH = "story-illustration-v1"`. The illustration step runs only when
  `workflow.patched(...)` is true and the static card succeeded. With no card, the outcome stays
  `text_only` and there is no illustration call.
- Build the prompt and alt text in workflow code; both are pure.
- When animation is included, start the illustration activity with `workflow.start_activity` at
  the same time as the animated render, then await both. This keeps the worst-case time inside
  `MEDIA_CHILD_TIMEOUT` (6 min).
- Activity settings:
  - `start_to_close_timeout`: 75 s.
  - `RetryPolicy`: `maximum_attempts=2`, `initial_interval=2s`, and non-retryable
    `["IllustrationInvalid"]`.
- If the activity raises `ActivityError` (and is not cancelled), set `illustration_status=failed`
  with no artifact. Cancellation re-raises. Otherwise copy the result's status and artifact into
  the manifest. The card outcome (`gif`/`static`/`static_fallback`) is unaffected. `selected`
  stays `None` until approval.

### Approval choice (`story_generation.py`, services, route)

- `FinalStoryDecision` gains `media_choice: MediaSelection | None = None`.
- After an approve decision, the workflow applies the choice. This happens under
  `workflow.patched("story-media-choice-v1")`, and only when the manifest has an
  `illustration_artifact`:
  - It sets `selected` to `illustration` only if the choice is `illustration`. Otherwise it sets
    `card`. A missing choice (old form, CLI) means `card`: no image goes out unless a human picked
    it.
  - It then recomputes `self._content_hash` from the updated package before `_finish`.
  - Packages without an illustration are untouched, so their hash is unchanged.
- The stale check still compares the hash the human reviewed with the review-time hash.
- `DecideStoryRequest` gains `media_choice`. The route reads a `media_choice` form field and
  allow-lists it: `"illustration"`, `"card"`, or empty (empty means `None`). Any other value
  re-renders the page with HTTP 400 and the existing `INVALID_DECISION_ERROR`.

### Publishing (`publishing/formatter.py`)

`_choose_artifact`:

- If `manifest.selected is illustration` and `illustration_artifact` is set, return the
  illustration. It is a still image, even when the destination supports GIF, because the GIF is
  the card's animation.
- Otherwise keep the current behaviour (GIF when supported, else the card still).

Existing capability checks are unchanged. Bluesky allows PNG/WebP up to 1,000,000 bytes, and the
normalised illustration is always WebP within `MAX_STATIC_BYTES`. Alt text comes from the
artifact.

### Config (`config.py`, env read only here)

| Env var | Default | Rule |
| --- | --- | --- |
| `ILLUSTRATION_PROVIDER` | `off` | `off`, `fake` or `imagen`. Unknown values raise a config error that lists the valid names. This is the kill switch. |
| `ILLUSTRATION_API_KEY` | unset | Stored as `SecretStr`. Required when the provider is `imagen`; if missing, raise a config error that names the variable, never its value. |
| `ILLUSTRATION_BASE_URL` | `https://generativelanguage.googleapis.com/v1beta` | Must be `https://`, with no query string and no userinfo. |
| `ILLUSTRATION_MODEL` | `imagen-4.0-fast-generate-001` | Must match `^[a-z0-9][a-z0-9.-]{0,63}$`. |

`worker.py` builds the provider from `Settings` (`off` gives `None`) and registers
`illustrate_story` on the media task queue.

## UI design (frontend-design skill applied)

**Design plan.** We keep the shipped neo-brutalist tokens (sage ground `#e3ecdf`, ink `#15172b`,
cobalt accent `#2448d8`, highlight yellow `#ffc93c`, surface `#ffffff`, and their dark-theme
pairs) with no new colours. The one bold move: **the picture that will be posted is the only
thing on the page with the yellow hard shadow**. Today the chart preview already uses that shadow
(`--shadow-chart`) to mean "this is the artefact", so here it means "this goes in the post". The
shadow carries information, not decoration. Everything else stays quiet: the alternative tile
has a thin border, no shadow and a smaller size. We checked this against the generic SaaS-card
default (two identical rounded cards with the same soft shadow) and rejected that look. The tiles
differ in size (2fr/1fr), and only the chosen tile gets the shadow.

**Layout.** In review state with `illustration_status == generated`, a picker replaces the
single `figure.story-media` at the top of the reading column:

```
desktop (>= 40rem)                          360px (stacked, illustration first)
+--------------------------------+--------+   +----------------------+
| (o) Illustration               | ( ) St.|   | (o) Illustration     |
| [ 1:1 cartoon image .......... ]| card  |   | [ image ........... ]|
| Cartoon made by Imagen from... | [card]|   | caption, alt text    |
| Alt text: ...                  | caption|   +----------------------+
+--------------------------------+--------+   | ( ) Story card       |
   ^ chosen tile: 3px ink border +              | [ card ]             |
     6px 6px 0 0 #ffc93c shadow                +----------------------+
```

- Markup: `<fieldset class="pick">` with `<legend>` "Picture for the post", styled like an h2
  (`--font-display`, `--text-lg`, weight 800). Inside, two
  `<label class="pick__tile pick__tile--illustration|--card">` elements. Each wraps
  `<input type="radio" name="media_choice" value="illustration|card" form="story-approve">`, the
  image (`<picture>` for the card so the existing GIF/reduced-motion source stays), a tile name, a
  one-line hint and the alt-text line. The approve `<form>` in the rail gets `id="story-approve"`,
  so the radios submit with Approve and no JavaScript is needed. The reject form ignores them.
- The illustration radio is `checked` by default; the view model sets `checked` and the template
  only prints it.
- Grid: `display:grid; grid-template-columns: 2fr 1fr; gap: var(--space-4); align-items: start;`
  Below `40rem`, use one column. Images are `width:100%; height:auto` with real `width`/`height`
  attributes. There is no horizontal scroll at 360px.
- Tile tokens:
  - Base tile: `background: var(--color-surface)`, `border: var(--border-thin) solid
    var(--color-border)`, `border-radius: var(--radius-sm)`, `box-shadow: var(--shadow-none)`,
    and `padding: 0 0 var(--space-3)`.
  - The image has `border-bottom: var(--border-thin) solid var(--color-border)`.
  - Chosen tile (`.pick__tile:has(input:checked)`): `border-width: var(--border-width)` and
    `box-shadow: 6px 6px 0 0 var(--color-highlight)`. Add a new token
    `--shadow-picked: 6px 6px 0 0 var(--color-highlight)` in `:root`, used in both themes.
  - Focus (`.pick__tile:has(input:focus-visible)`): `outline: var(--border-width) solid
    var(--color-focus); outline-offset: 3px`.
  - The radio stays visible at the existing 1.25rem size with `accent-color`. A checked radio
    plus the border-weight change carry the state for browsers without `:has` and for people who
    cannot see colour. The yellow shadow is extra, never the only signal.
- Inside the tile: the name uses `--font-display`, weight 800, `--text-base`, set as
  `display:flex; gap: var(--space-2); align-items:center` together with the radio. The hint and alt
  text use `--text-sm`, line-height 1.5, `max-width: 62ch`, and `--color-muted` for the alt text.
  The tile padding inline is `var(--space-3)`.
- Use sentence case and no all-caps labels. Add no motion; the existing reduced-motion rules still
  apply to the card's GIF source.
- Rail: under the Approve button, add one `.meta` line, "Approve sends the picture you picked
  with the story."

**Copy (constants in `view_models.py`):**

| Constant | Text |
| --- | --- |
| Legend | `Picture for the post` |
| Illustration tile name | `Illustration` |
| Illustration tile hint | `Cartoon drawn by an image service from the headline. It has no text or numbers.` |
| Card tile name | `Story card` |
| Card tile hint | `Headline and key fact drawn by this app.` (the existing GIF kind text is appended when animated) |
| Rail note | `Approve sends the picture you picked with the story.` |
| Blocked note (fixed sentence) | `The image service declined to draw this story, so the story card is used.` |
| Failed note (fixed sentence) | `The illustration could not be made, so the story card is used.` |
| Ready kind text, illustration | `Illustration. This picture goes in the post.` |
| Ready kind text, card | Existing card kind text plus ` This picture goes in the post.` |

**Other states:**

- Status `blocked` or `failed`: show the existing card figure unchanged, plus the fixed note as
  `<p class="story-media__note">` in the figcaption (`--text-sm`, weight 700).
- Status `disabled` or `None` (old stories): render exactly as today.
- Ready state: show only the selected picture in the existing `figure.story-media`. The
  illustration displays when `selected == illustration`; otherwise the card shows with its
  current GIF behaviour.

The view model has these fields: `StoryPickView` with `legend`, `tiles: list[PickTileView]` (`value`,
`name`, `hint`, `src`, `animated_src`, `width`, `height`, `alt_text`, `checked`,
`modifier`), `form_id`. Add `illustration_note` to `StoryMediaView`.

## Acceptance criteria

- [ ] With `ILLUSTRATION_PROVIDER=imagen` and recorded fixtures, a story in review shows the
      illustration preselected and the story card as the alternative. Approve with either choice
      gives a Ready story whose `content_hash` covers `selected`, and the Bluesky formatter uploads
      exactly that still with its alt text.
- [ ] The prompt is built only from the sanitised headline and summary with the fixed style prefix
      and negative constraints. It contains no digits, URLs, quotes or evidence text, and is
      identical for identical packages.
- [ ] A safety block, empty result, failure after 2 attempts, oversized or wrong-MIME output, or
      `ILLUSTRATION_PROVIDER=off` all leave the card outcome unchanged. They show the fixed
      sentence (blocked/failed) or nothing (off), and the story stays approvable and publishable.
- [ ] Re-running `illustrate_story` with the same input makes no second API call. Stored bytes are
      Pillow-normalised WebP, at most 1024 px and within `MAX_STATIC_BYTES`, written atomically by
      content hash.
- [ ] Pre-065 `StoryGenerationWorkflow` and `StoryMediaWorkflow` histories replay. A pre-065
      package's `story_package_hash` is unchanged (golden value).
- [ ] The API key is read only in `config.py`, never appears in logs, URLs, errors or the repo,
      and `just lint` and `just test` pass with no live network call.

## Files to touch

- `src/ai_trend_pipeline/media/models.py`
- `src/ai_trend_pipeline/media/illustration.py`
- `src/ai_trend_pipeline/media/imagen.py` (new)
- `src/ai_trend_pipeline/media/normalize.py` (new)
- `src/ai_trend_pipeline/media/illustration_activities.py` (new)
- `src/ai_trend_pipeline/media/fake.py` (add `FakeIllustrationProvider` with a fixed small PNG and
  a switchable block/fail mode)
- `src/ai_trend_pipeline/story_media.py`
- `src/ai_trend_pipeline/story_generation.py`
- `src/ai_trend_pipeline/publishing/formatter.py`
- `src/ai_trend_pipeline/config.py`
- `src/ai_trend_pipeline/worker.py`
- `src/ai_trend_pipeline/services/stories.py`
- `src/ai_trend_pipeline/web/routes/stories.py`
- `src/ai_trend_pipeline/web/view_models.py`
- `src/ai_trend_pipeline/web/templates/partials/story_detail_body.html`
- `src/ai_trend_pipeline/web/static/app.css`
- `infra/compose.prod.yaml` (pass through the four `ILLUSTRATION_*` vars)
- `tests/fixtures/illustration/` (new): `imagen_ok.json` (tiny 512x512 PNG in base64),
  `imagen_jpeg_ok.json`, `imagen_blocked.json` (`raiFilteredReason`), `imagen_empty.json`,
  `imagen_bad_mime.json`, `imagen_huge.json` (2,600 px image)
- Tests listed below. Docs: `README.md`, `docs/architecture/tech.md`, `docs/runbooks/publishing.md`

## Tests to write

- `tests/test_illustration_prompt.py`: same package gives the same prompt; digits, quotes, `#@$%/`
  and injection text are stripped; caps are 120/240/1000; the empty-headline fallback is used;
  the alt text is correct.
- `tests/test_media_models.py` (extend): manifest rules for the illustration fields; a pre-065
  manifest dumps without the new keys; golden `story_package_hash` for a pre-065 package.
- `tests/test_imagen_provider.py`: uses an `httpx.MockTransport` that replays the fixtures. Covers
  the request shape (header key, `personGeneration`, no key in the URL), the ok/blocked/empty
  responses, and how 429/500/timeout (transient), 400/403 and malformed JSON (invalid) and the
  8 MB cap map to errors. A captured-logs check shows no key or body in the logs.
- `tests/test_illustration_normalize.py`: PNG/JPEG/WebP inputs become WebP at most 1024 px and
  within the byte cap, and output is deterministic. The tests also cover a bad MIME, a magic-byte
  mismatch, a side over 2048 or under 256, and a decompression bomb guard.
- `tests/test_illustration_activities.py`: disabled returns `disabled` with no call; a cache hit
  makes zero provider calls; blocked returns `blocked`; errors map to the right `ApplicationError`
  types; the pointer and artifact are written atomically.
- `tests/test_story_media_workflow.py` (extend, time-skipping): generated, blocked, failed after
  2 attempts, and card-failed (no illustration call) paths; runs concurrently with animation; the
  worst-case retry budget is less than `MEDIA_CHILD_TIMEOUT`.
- `tests/test_story_generation_replay.py` (extend): pre-065 histories of both workflows replay
  with the new code.
- `tests/test_story_generation_media.py` (extend): approve with `illustration`, `card` or no choice
  sets `selected` and changes the final hash; a story without an illustration keeps its review hash.
- `tests/test_publishing_formatter.py` (or the existing formatter test file, extend): the selected
  illustration is uploaded even when the destination supports GIF; `card` or `None` keeps the
  current behaviour.
- `tests/test_config.py` (extend): the default is off; unknown provider names fail; imagen without
  a key fails; a non-https base URL fails; a bad model ID fails; the key never appears in
  `repr(settings)`.
- `tests/test_web_story_media.py` (extend): the picker renders with the illustration checked and
  `form="story-approve"`; the route passes `media_choice` and rejects bad values with 400;
  blocked/failed notes render; the ready state shows only the selected picture; the 360px layout
  class is present; `off` and old stories render unchanged.

## Out of scope

- Animation or motion of illustrations, Remotion, Node or Chromium. Motion is a later task.
- A "regenerate illustration" button or any other re-roll. The only way to get a new image is a
  new story content hash (redraft).
- A second hosted provider. Only Imagen and the fake ship; OpenAI is documented as the fallback.
- Changing the card renderer, the GIF template, the chart publisher or the Bluesky activity
  (beyond the formatter's artifact choice).
- Daily spend caps or billing dashboards (see the cost note).
- Using candidate topics, evidence or fact text in prompts.

**Split point if the PR grows too large:** 065a covers models, provider, normalise, activity,
workflow, config, worker and docs. Approval then always freezes `card`, so 065a ships safely
with no visible change except the fixed notes. 065b covers the review picker, `media_choice`
through the route, service and workflow, and the formatter selection.

## Docs (builder updates README, tech.md and the publishing runbook with this)

- **Env vars:** see the Config table. Add them to the README configuration table and the
  `infra/compose.prod.yaml` environment. `.env.prod` holds the key on the server only.
- **Cost note:** Imagen 4 Fast costs about USD 0.02 per image (re-check the price and write the
  date). We make one billed image per story content hash, because the pointer cache stops
  re-calls and Temporal retries are capped at 2 attempts. For example, 20 stories a day costs
  about USD 0.40 a day, or about USD 12 a month. Blocked prompts may still be billed. Set a budget
  alert in Google Cloud billing.
- **Disable / rollback:** set `ILLUSTRATION_PROVIDER=off` and run `just deploy`. New stories then
  get the card only, and in-flight and approved stories keep their frozen manifest. A full code
  rollback is safe because the new manifest fields are optional and dropped when `None`; stories
  already approved with an illustration still publish it from the stored artifact. If you suspect
  a key leak, rotate it in Google AI Studio and update `.env.prod`.
- **Safety summary:**
  - The prompt is code-built from the headline and summary only.
  - People are blocked at the provider.
  - There is no text or data in the image.
  - A human must approve the picture, and the card is always available.
  - Imagen outputs carry a SynthID watermark.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: Wrote spec 065 (Imagen 4 Fast via the Gemini API behind `IllustrationProvider`, off by default) and added plan row 065 (`yes`). frontend-design skill was in context; the picker design is above.
I had no shell, so the branch `task/065-ai-illustration` was not created and nothing was committed. The spec and plan edit are uncommitted on `main`.
Risks: confirm Imagen parameter names and the price against the current docs; the hash-compat serializer is load-bearing (golden test). Split at 065a/065b if needed.
Next: the human reviews and approves the spec.
