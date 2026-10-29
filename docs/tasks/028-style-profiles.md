# 028: Style profiles

Status: reviewed

## Goal

Each signed-in person saves a style (palette, font, caption tone, hashtags, signature) on a new
`/settings` page. It is stored in Postgres. Every chart they start from the web uses that style in the
chart-spec activity: its prompt (tone, caption budget) and its output (colours, font, hashtags,
signature). Two people get visibly different charts for the same topic.

## Decisions

- **One PR.** The pieces are small and depend on each other (about 850 lines with tests). A split
  into 028a (storage and page) and 028b (prompts) would ship a page that does nothing, so this stays
  one PR.
- **Domain model in a new pure module `src/ai_trend_pipeline/style.py`.** It holds no I/O and no
  imports from `services/` or `web/`, so the workflow and activities can import it (the same way they
  import other models). It contains:
  - `PALETTES: dict[str, tuple[str, ...]]`, a fixed set of named palettes of 6 to 10 `#rrggbb`
    colours each: `classic` (Tableau 10, the Vega default), `ocean`, `sunset`, `okabe-ito`
    (colour-blind safe) and `ink` (dark greys plus one accent).
  - `FONTS: dict[str, str]`, which maps a key to a Vega font stack: `sans` maps to
    `"Liberation Sans, Arial, Helvetica, sans-serif"`, `serif` to
    `"Liberation Serif, Georgia, Times New Roman, serif, Liberation Sans"` and `mono` to
    `"Liberation Mono, Courier New, monospace, Liberation Sans"` (the trailing fallback keeps text
    visible where no system font exists; the Dockerfile installs `fonts-liberation`).
  - `TONES: dict[str, str]`, which maps a key to a one-line instruction. The keys are `neutral`,
    `friendly`, `punchy` and `academic`, and every instruction says the caption stays factual.
  - `class ChartStyle(BaseModel)` (frozen). Its fields are `palette: str = "classic"`,
    `font: str = "sans"`, `tone: str = "neutral"`, `hashtags: list[str] = []` and
    `signature: str = ""`. Validators: palette, font and tone must be keys of the dicts above. There
    are at most `MAX_HASHTAGS = 3` hashtags, each matching `^[A-Za-z0-9_]{1,24}$` and stored without
    `#`. The signature is stripped, at most `MAX_SIGNATURE_CHARS = 40` long and has no newline.
  - `parse_hashtags(text: str) -> list[str]`. It splits on commas and whitespace, strips a leading
    `#` from each tag, drops empty ones and de-duplicates while keeping order. It does not validate;
    `ChartStyle` does.
  - `caption_suffix(style) -> str`. It returns `"#a #b"` and the signature joined by `"\n\n"`, with
    empty parts dropped (`""` when there are neither).
  - `style_prompt(style, caption_budget: int) -> str`. It returns a "Style:" block with the tone
    instruction, "Keep the caption to at most {caption_budget} characters; hashtags and a signature
    are added after it." and "Do not set colours, fonts or a config block; they are applied for you."
  - `apply_style_to_spec(spec, style) -> dict[str, JsonValue]`. It returns a copy. It keeps any other
    `config` keys and sets `config.range.category` to the palette, `config.mark.color` to the first
    palette colour (single-series charts have no colour encoding) and `config.font` to the font stack.
- **Style is applied in the activity, partly in code.** The tone goes into the LLM prompt. Palette,
  font, hashtags and signature are applied in code after the LLM call, so the difference is visible
  every time instead of depending on the model. The caption budget is
  `MAX_CAPTION_CHARS - len(suffix) - 2` (when there is a suffix), and the final caption is
  `caption + "\n\n" + suffix`. If the draft caption is longer than the budget, the activity raises
  the existing non-retryable `InvalidChartDraft`, so the chart loop regenerates once with that
  feedback. `SYSTEM_PROMPT` does not change. The style block goes in the user message.
- **Backwards compatible.** `DailyChartInput.style: ChartStyle | None = None` and
  `WriteChartSpecInput.style: ChartStyle | None = None`. With `None`, the prompt, spec and caption
  are exactly what they are today, so existing fixtures and tests pass unchanged and old histories
  replay (the workflow issues no new commands; it only forwards a field). People who never saved a
  style get `None`, so their charts look the same as today.
- **Storage location: the default `postgres` database, in a dedicated schema `topic_studio`.**
  Temporal's auto-setup owns the `temporal` and `temporal_visibility` databases, and app tables must
  stay out of them. `CREATE DATABASE` has no `IF NOT EXISTS` and cannot run in a transaction.
  `CREATE SCHEMA IF NOT EXISTS` plus `CREATE TABLE IF NOT EXISTS` are idempotent and work on the
  already initialised prod volume (initdb scripts never run there). The table is
  `topic_studio.style_profiles(email text PRIMARY KEY, profile jsonb NOT NULL, updated_at timestamptz NOT NULL DEFAULT now())`.
  Storing the profile as JSONB validated by `ChartStyle` means fields added later (029) only need
  Pydantic defaults and no migration.
- **Driver: `psycopg[binary]` (psycopg 3, async), no pool.** It is the maintained Postgres driver
  with native asyncio and typed APIs. The `binary` extra ships libpq, so the slim Docker image needs
  no apt packages. Two people generate a handful of requests a day, so one short connection per call
  (`await psycopg.AsyncConnection.connect(url)`) is enough. That avoids `psycopg_pool` and its
  open/close lifecycle. Run `uv add "psycopg[binary]"`.
- **Schema creation happens once per process, lazily, on first real use.**
  `get_style_store` builds and caches `PostgresStyleStore` on `app.state.style_store` without any I/O.
  The store's own `get`/`save` run `ensure_schema()` once under an `asyncio.Lock`; a failure is not
  cached and is retried on the next call, and driver errors become `StyleStoreUnavailable`. Routes
  that do not read or write styles (run page, PNG, decide, health, home) never touch Postgres. A
  failed lookup on `POST /runs` re-renders the home page with a friendly 503 and starts no run;
  `/settings` shows the same message with 503. A signature must be `str.isprintable()`.
- **Connection string: one setting, no new secret.** `Settings.database_url: SecretStr`, read from
  `DATABASE_URL` in `config.py`. When unset or empty it defaults to
  `postgresql://postgres:postgres@localhost:5432/postgres`, the dev credentials and port from
  `compose.dev.yaml` (no dev compose change). Prod compose builds it on `web` from the existing
  password in libpq key/value form, so a generated password with `/ + =` needs no URL encoding:
  `DATABASE_URL: "host=postgres port=5432 dbname=postgres user=postgres password=${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in .env.prod}"`.
  `web` also gets `depends_on` on `temporal` (started) and `postgres` (`service_healthy`). The worker
  gets no `DATABASE_URL`, because activities do no DB access.
- **Layering.**
  - `services/styles.py` holds `StyleStore` (Protocol: `async get(email) -> ChartStyle | None`,
    `async save(email, style) -> None`) and `StyleService(store)`. `StyleService.get(email)` returns
    the stored style or `ChartStyle()`. `StyleService.save(email, style)` saves it.
  - `services/postgres_style_store.py` holds `PostgresStyleStore(database_url)` with
    `ensure_schema`, `get` and `save`. `save` is an upsert:
    `INSERT ... ON CONFLICT (email) DO UPDATE SET profile = EXCLUDED.profile, updated_at = now()`
    with `Jsonb(style.model_dump())`. If a stored row fails validation, `get` logs a warning and
    returns `None`.
  - `ChartService.__init__` gains `styles: StyleStore | None = None`. In `start`, after the cap check
    and only when `requested_by` is set and `styles` is not None, it calls
    `style = await styles.get(requested_by)` and puts it on `DailyChartInput`. The CLI builds
    `ChartService` without a store, so the CLI needs no database. Workflows and activities never import
    `services` or `web`.

## Acceptance criteria

- [ ] **Model and helpers.** `style.py` is as described in Decisions. `ChartStyle` rejects an unknown
      palette, font or tone, more than 3 hashtags, a bad hashtag and a signature over 40 characters or
      containing a newline. `parse_hashtags("#ai, data  #ai")` returns `["ai", "data"]`.
- [ ] **Activity.** `WriteChartSpecInput` has `style: ChartStyle | None = None`. With a style,
      `write_chart_spec` adds `style_prompt(...)` to the user message, returns a spec whose `config`
      carries the palette, mark colour and font, and returns a caption ending with
      `caption_suffix(style)`. A draft caption over the budget raises the non-retryable
      `InvalidChartDraft`. With `style=None`, the request body, spec and caption are byte-for-byte
      what they are today.
- [ ] **Workflow and service.** `DailyChartInput.style` defaults to `None`, and the workflow passes it
      into `WriteChartSpecInput` (the regenerate-with-feedback path keeps it). `ChartService.start`
      looks up the requester's style as described and sets it on the input. A run whose requester has
      no saved style has `style=None`.
- [ ] **Storage.** `PostgresStyleStore.ensure_schema()` is idempotent. Running it twice against a
      fresh Postgres causes no error. `save` followed by `get` round-trips, and a second `save` for
      the same email overwrites the first. The tables live in the `topic_studio` schema of the
      `postgres` database. Settings, compose and dependencies are as in Decisions.
- [ ] **Settings page.**
      - `GET /settings` (requires `current_user`) shows the person's email and a form prefilled with
        their style. The form has palette radio buttons with colour swatches, a font `<select>`, a
        tone `<select>`, a hashtags text input ("up to 3, separated by spaces or commas") and a
        signature text input with `maxlength="40"`. It uses the task 035 tokens and classes (`.card`,
        `.btn btn--primary`, `.error`), plus a few new rules in `app.css` for `.settings-form`,
        `.swatches`/`.swatch`, `.notice` (success colour) and `.site-nav`. Only tokens are used, no
        raw colours other than the swatch colours, which come from the view model.
      - `POST /settings` parses the form with `parse_hashtags` and `ChartStyle`. When valid it calls
        `StyleService.save` and redirects 303 to `/settings?saved=1`, which shows "Saved. Your next
        chart will use this style." in a `role="status"` `.notice`. When invalid it returns 400, keeps
        the submitted values and shows a friendly `role="alert"` message that names the field and its
        limit (built in `view_models.py`). Each route calls exactly one service method.
      - `base.html` gets a small header nav link "Your style" to `/settings`. There is no new
        JavaScript.
- [ ] **Docs and checks.** `README.md` gets a short "Your style" paragraph (the settings page, what is
      applied, that `DATABASE_URL` defaults to the dev Postgres from `just dev`, and the
      `topic_studio` schema created at first use). `docs/runbooks/deploy.md` gets a line saying
      `DATABASE_URL` is assembled in compose from `POSTGRES_PASSWORD`, needs no new `.env.prod` key,
      and requires a password without spaces, quotes or backslashes. `just lint` and `just test`
      pass.

## Files to touch

- `pyproject.toml`, `uv.lock` (through `uv add "psycopg[binary]"` only)
- `src/ai_trend_pipeline/style.py` (new)
- `src/ai_trend_pipeline/chart_spec.py`
- `src/ai_trend_pipeline/daily_chart.py` (one field, plus `style=data.style` when building `WriteChartSpecInput`)
- `src/ai_trend_pipeline/config.py`
- `src/ai_trend_pipeline/services/styles.py` (new), `src/ai_trend_pipeline/services/postgres_style_store.py` (new)
- `src/ai_trend_pipeline/services/charts.py`
- `src/ai_trend_pipeline/web/app.py` (router), `web/dependencies.py` (`get_style_store`, `get_style_service`, and `get_chart_service` passes the store)
- `src/ai_trend_pipeline/web/routes/settings.py` (new), `web/view_models.py`
- `src/ai_trend_pipeline/web/templates/settings.html` (new), `web/templates/base.html`, `web/static/app.css`
- `infra/compose.prod.yaml` (`web` env plus `depends_on` only)
- `tests/style_fakes.py` (new, `InMemoryStyleStore`), `tests/test_style.py` (new), `tests/test_style_store.py` (new)
- `tests/test_chart_spec.py`, `tests/test_chart_loop.py`, `tests/test_daily_chart.py`, `tests/test_chart_service.py`, `tests/test_web.py` (or new `tests/test_web_settings.py`), `tests/test_config.py`, `tests/test_compose_prod.py`
- `README.md`, `docs/runbooks/deploy.md`

## Tests to write

No live Postgres, Temporal or LLM. Use `InMemoryStyleStore` and the recorded
`llm_chart_spec_response.json` fixture (add a fixture only if a long-caption case needs one).

- Style: the defaults validate. Every `PALETTES` value is 6 to 10 `#rrggbb` colours, and every
  `FONTS` and `TONES` key validates.
- Style: unknown palette, font or tone, 4 hashtags, `"a-b"`, a 25-character tag, a 41-character
  signature and a signature with a newline each raise `ValidationError`.
- Style: `parse_hashtags` handles `#`, commas, extra whitespace and duplicates.
- Style: `caption_suffix` gives `""` with no hashtags and no signature, the tags alone, the signature
  alone, and both joined by a blank line.
- Style: `apply_style_to_spec` sets range, mark colour and font, keeps the other existing `config`
  keys, and does not change its input.
- Chart spec: with a `punchy`/`ocean`/`serif`/hashtags/signature style, the recorded request's user
  message contains the tone instruction and the caption budget, the result spec's `config` has the
  ocean palette and the serif stack, and the caption ends with the suffix.
- Chart spec: with `style=None`, the request body and result equal those of today's test (regression).
- Chart spec: a draft caption that fits 280 but not the budget raises a non-retryable
  `InvalidChartDraft`.
- Chart spec, "visibly different": the same fixture run with two different styles gives different
  `config.range.category`, different `config.font` and different captions. Rendering both through
  `render_chart_png` into `tmp_path` gives different `spec_sha256`.
- Chart loop: on regenerate with feedback, the second `write_chart_spec` input still carries the style.
- Daily chart (time-skipping): `DailyChartInput(style=...)` reaches the fake `write_chart_spec`
  unchanged. `DailyChartInput.model_validate` of an old payload without `style` gives `None`.
- Service: with a store holding a style for `a@`, `start(requested_by="a@")` puts that style on the
  input. `b@` (nothing saved) gets `None`. With no `requested_by` or no store, the store is not
  called. When the cap is reached, the store is not called.
- Store (pure): the DDL strings contain `CREATE SCHEMA IF NOT EXISTS topic_studio` and
  `CREATE TABLE IF NOT EXISTS topic_studio.style_profiles`, and the upsert uses `ON CONFLICT (email)`.
- Web: `GET /settings` shows the email and the default selections. After a save it shows the saved
  values. The header link to `/settings` is on the home page.
- Web: a valid `POST /settings` returns 303 to `/settings?saved=1`, and the fake store holds the
  style. `GET /settings?saved=1` shows the `role="status"` message.
- Web: each invalid `POST` (bad palette, 4 hashtags, long signature) returns 400 with a
  `role="alert"` message and the submitted values kept, and nothing is saved.
- Web: in cloudflare mode, a missing header gives 401 on GET and POST. Two different header emails
  each see their own saved style.
- Config: unset or empty `DATABASE_URL` gives the dev URL, a set value is used, and `repr(settings)`
  does not show the password.
- Compose: `web` has the key/value `DATABASE_URL` with host `postgres` and a
  `${POSTGRES_PASSWORD:?...}` interpolation (no literal password), and depends on `postgres` with
  `service_healthy`. `worker` has no `DATABASE_URL`. The existing six-service and secrets tests still
  pass.

**Manual check (builder, once, not in CI).** Start a throwaway container on a spare port, for example
`docker run --rm -d --name style-check -e POSTGRES_PASSWORD=check -p 127.0.0.1:15432:5432 postgres:16.15`.
Against it, call `ensure_schema()` twice, then save, get, save again and get through
`PostgresStyleStore` with `DATABASE_URL=postgresql://postgres:check@localhost:15432/postgres`. Then
remove the container. Also render one spec with each of the three fonts through `vl_convert` locally
and note in the handoff whether serif and mono look different from sans. If one does not, say so
(the palette still makes the charts differ). Do not touch the dev or prod Postgres.

## Out of scope

- Per-post options on the home page, chart type, time range, countries or free-text instructions
  (029).
- Custom hex palettes or uploaded fonts.
- Clickable Bluesky hashtag facets. Hashtags are posted as plain text.
- Applying the style to `pick_topic` or the critique prompt, or changing the critique. Any change to
  `bluesky.py`, `render.py`, `worker.py`, `cli.py` or `compose.dev.yaml`.
- A connection pool, an ORM, a migration tool, a lifespan hook, or a separate database or Postgres
  role.
- Showing a run's style on the run page, a runs list, or admin editing of other people's styles.
- CSRF tokens (no existing form has them; Cloudflare Access fronts the app).

## What 029 builds on

029 adds a per-post options model on the home form. `ChartService.start` merges it over the stored
`ChartStyle` (blank fields fall back to the profile) before putting it on `DailyChartInput`. It
extends `style_prompt` (or a sibling prompt helper in `style.py`) for chart type, time range,
countries and instructions. Because the stored profile is JSONB validated by `ChartStyle`, new
optional fields need no migration.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: Spec for style profiles as one PR (a split would ship a dead page). Pure `style.py` model; tone goes into the chart-spec prompt, while palette, font, hashtags and signature are applied in code in `write_chart_spec`. `style=None` keeps today's behaviour and replay.
Storage: psycopg 3 async (binary extra, no pool), table in schema `topic_studio` of the default `postgres` DB, created lazily once per process with IF NOT EXISTS. `DATABASE_URL` has a dev default, and prod compose builds it from `POSTGRES_PASSWORD` (key/value form, no new secret).
`StyleStore` protocol and `StyleService` live in `services/`. `ChartService.start` resolves the style, and the CLI has no store. Routes use an in-memory fake. Builder does one manual check on a throwaway Postgres on port 15432.
Next: orchestrator sets `approved` (task is `no`), then builder on `task/028-style-profiles`.

**builder**: Built as specced; `just lint` and `just test` pass (273+ tests). Manual Postgres check (container `ai-trend-scratch-pg`, port 15432, removed): `ensure_schema` twice OK, table in `topic_studio`, save/get round trip and overwrite OK.
Fonts: vl_convert bundles only Liberation Sans and the slim image has no system fonts, so the spec's serif and mono stacks rendered charts with NO text (title, axes). Deviation: serif and mono stacks now end with `, Liberation Sans` (text shows, but all three fonts look identical until fonts are installed). Decide whether to install fonts (out of scope here).
Settings page: screenshots light/dark at 360 and 1280 px show no horizontal overflow; no CSS fixes needed. `get_chart_service` now needs the style store, so every run route needs Postgres (as specced).

**builder (fix 1)**: Dockerfile now installs `fonts-liberation` (apt, no-recommends, lists removed). Throwaway build rendered sans/serif/mono PNGs with different sha256 (a4b477e43669, 8a394be82855, 7cd6d4a98d37) and they visibly differ; vl_convert picks up system fonts, so no code change. Scratch image removed.

**verifier**: `just lint` passes (ruff check and format, pyright 0 errors). `just test` passes (274 passed). No ai-trend-scratch containers or images left. ai-trend-prod containers still up and untouched. Diff vs main: pyproject adds only psycopg[binary]; uv.lock adds psycopg, psycopg-binary and tzdata (transitive) and removes nothing. Next: reviewer.

Reviewer: needs-fix. get_chart_service depends on get_style_store, which runs ensure_schema (connects to Postgres) on first use,
so every runs.py route (GET /runs/{id}, chart png, decide, refine, list) fails when Postgres is down. Fix: make the store lazy
(connect/ensure_schema inside get/save, under the lock, only on first real call) and add a test with a failing store on GET /runs/{id}.
Everything else checked OK (layering, SQL parameterised, escaping, per-user scoping, config/compose, Dockerfile).

**builder (fix 2)**: Store is now lazy (`PostgresStyleStore` ensures schema on first get/save, retries after failure, raises `StyleStoreUnavailable`); `get_style_store` does no I/O. POST /runs and /settings give a friendly 503; signature uses `isprintable`.
Tests added: broken-store read routes stay 200, schema retry, 503 paths, control-char signature. No list/refine routes exist yet. Removed the lock pyright ignore with the lock.

**verifier (re-check after fix 2)**: `just lint` passes (ruff check and format, pyright 0 errors). `just test` passes (281 passed).
No ai-trend-scratch containers or ai-trend-scratch-app images left. ai-trend-prod (6 containers) still up, untouched.
`git status` clean apart from this status change.
All checks pass.

Reviewer (re-review after fix 2): Approved. Outage bug fixed: get_style_store/get_chart_service do no I/O, store connects lazily under its own lock, failed schema retried (tested), only psycopg.Error/OSError converted, 503 paths and printable signature check present; lint and 281 tests pass.
Note: spec line 151 still says "(router, lock)" for web/app.py; the lock is gone, so that wording is stale.
