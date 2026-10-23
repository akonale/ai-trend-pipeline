# 011: Activity: post to Bluesky (test account), idempotent

Status: pr-open

## Goal

Add a `post_chart_to_bluesky` activity that posts the approved PNG and its caption to the Bluesky test account, and posts it exactly once per input. Call it from `DailyChartWorkflow`'s approved branch so that `just approve <id>` puts the chart on the test account.

## Design (read first)

- **Plain XRPC over httpx, no new dependency.** Use the existing `httpx` against `{service}/xrpc/...`. Do not add the `atproto` SDK. It needs four calls:
  1. `POST com.atproto.server.createSession` with `{"identifier": handle, "password": app_password}`. This returns `accessJwt` and `did`. Every later call sends `Authorization: Bearer <accessJwt>`. Open a new session on each activity attempt and do not cache it.
  2. `GET com.atproto.repo.getRecord?repo=<did>&collection=app.bsky.feed.post&rkey=<rkey>`. A 200 means the post already exists, so return its `uri`/`cid` with `already_posted=True` and skip the remaining calls. A 400 with `error == "RecordNotFound"` means continue. Anything else is an error.
  3. `POST com.atproto.repo.uploadBlob` with the raw PNG bytes and `Content-Type: image/png`. This returns `{"blob": {...}}`.
  4. `POST com.atproto.repo.createRecord` with `{"repo": did, "collection": "app.bsky.feed.post", "rkey": rkey, "record": {"$type": "app.bsky.feed.post", "text": text, "createdAt": created_at_iso, "embed": {"$type": "app.bsky.embed.images", "images": [{"image": blob, "alt": alt_text}]}}}`. This returns `uri` and `cid`.
- **Idempotency by a deterministic record key.** `app.bsky.feed.post` record keys must be TIDs. `make_tid(created_at: datetime, idempotency_key: str) -> str` builds one deterministically: `micros = int(created_at.timestamp() * 1_000_000)`, `clock_id = int.from_bytes(sha256(idempotency_key).digest()[:2]) & 0x3FF`, `value = (micros << 10) | clock_id`. Encode `value` as 13 characters of 5 bits each, most significant first, using the alphabet `234567abcdefghijklmnopqrstuvwxyz`. Retrying with the same input gives the same rkey, so step 2 finds the earlier post and nothing is duplicated.
- **Workflow side, deterministic.** In the approved branch the workflow builds `PostChartInput(png_path=review.png_path, text=review.caption, alt_text=f"Chart: {review.topic}. {review.caption}"[:1000], idempotency_key=workflow.info().workflow_id, created_at=workflow.now())`. It runs the activity with `IO_ACTIVITY_TIMEOUT` and `IO_RETRY_POLICY`, and returns `DailyChartResult(status="approved", ..., post=output)`. Because `workflow.now()` is recorded in history, a replay gives the same input. Rejected and expired runs never call the activity. If posting fails after retries, the workflow fails. There is no fallback.
- **Secrets never enter Temporal history.** The handle and app password are constructor arguments of `BlueskyActivities`. They are never fields of an activity input or output, and they are never logged. `load_bluesky_credentials()` reads `BLUESKY_HANDLE` and `BLUESKY_APP_PASSWORD` (both required) and `BLUESKY_SERVICE` (optional, default `https://bsky.social`). It raises `RuntimeError` naming any missing variable. The worker calls it at startup.
- **Errors.** A 401 from `createSession` raises non-retryable `ApplicationError(type="BlueskyAuthFailed")`. A PNG over `MAX_IMAGE_BYTES = 1_000_000` raises non-retryable `ApplicationError(type="ImageTooLarge")` before any upload. A missing PNG file raises non-retryable `ApplicationError(type="ChartFileMissing")`. Other HTTP errors propagate (`raise_for_status`) so that Temporal retries them.

## Acceptance criteria

- [x] **`src/ai_trend_pipeline/bluesky.py`** (new) defines:
  - `PostChartInput(png_path: str = Field(min_length=1), text: str = Field(min_length=1, max_length=300), alt_text: str = Field(default="", max_length=1000), idempotency_key: str = Field(min_length=1), created_at: datetime)`. The `created_at` field must be timezone-aware. Add a validator that rejects naive datetimes.
  - `PostChartOutput(uri: str, cid: str, rkey: str, already_posted: bool)`.
  - `BlueskyCredentials(handle: str, app_password: str, service: str)` as a Pydantic model with `app_password` typed as `SecretStr`, so it never appears in reprs. Also `load_bluesky_credentials() -> BlueskyCredentials`, `make_bluesky_client(service: str) -> httpx.AsyncClient` (`base_url=f"{service}/xrpc/"`, timeout 30 s) and `make_tid(created_at, idempotency_key) -> str`.
  - `class BlueskyActivities.__init__(self, client: httpx.AsyncClient, credentials: BlueskyCredentials)` with `@activity.defn async def post_chart_to_bluesky(self, data: PostChartInput) -> PostChartOutput`. It implements the four-call flow and the error rules above, and reads the PNG with `asyncio.to_thread`. `createdAt` is sent as UTC ISO-8601 with a `Z` suffix and millisecond precision.
- [x] **`src/ai_trend_pipeline/daily_chart.py`** (edit). `DailyChartResult` gains `post: PostChartOutput | None = None`. The approved branch calls the activity as described above. The query state is set to `approved` only after the post succeeds. The rejected, expired and signal logic is unchanged.
- [x] **`src/ai_trend_pipeline/worker.py`** (edit). `daily_chart_activities(...)` gains a `bluesky_client: httpx.AsyncClient, bluesky_credentials: BlueskyCredentials` parameter pair and also returns `post_chart_to_bluesky` (7 names). `main()` loads the credentials and opens `make_bluesky_client(...)` in the same `async with`.
- [x] **`src/ai_trend_pipeline/cli.py`** (edit). After an approval, when `result.post` is set, `approve` prints `posted: <uri>`, or `already posted: <uri>` if `already_posted` was true.
- [ ] **Done-when (automated part).** Calling the activity twice with the same input against a stateful mock transport makes exactly one `uploadBlob` call and one `createRecord` call. The second call returns `already_posted=True` with the same `uri` and `rkey`. **Done-when (manual part, human):** with `.env` holding the test account, run `just dev`, then `just chart` and `just approve <id>`. The chart appears once on the test account, and running `just approve <id>` again does not post again (it returns 1 because the run is no longer awaiting a decision).
- [x] `README.md`: the "Daily chart" section says the worker now also needs `BLUESKY_HANDLE`, `BLUESKY_APP_PASSWORD` (use an app password from the **test** account) and optionally `BLUESKY_SERVICE`, and that approving posts the chart. `just lint` and `just test` pass with no live HTTP calls.

## Files to touch

- `src/ai_trend_pipeline/bluesky.py` (new)
- `src/ai_trend_pipeline/daily_chart.py` (edit)
- `src/ai_trend_pipeline/worker.py` (edit)
- `src/ai_trend_pipeline/cli.py` (edit)
- `tests/test_bluesky.py` (new)
- `tests/fixtures/bluesky_create_session.json`, `tests/fixtures/bluesky_upload_blob.json`, `tests/fixtures/bluesky_create_record.json`, `tests/fixtures/bluesky_record_not_found.json` (new; hand-written, with fake DIDs, JWTs and CIDs only)
- `tests/test_daily_chart.py`, `tests/test_cli.py`, `tests/test_worker.py` (edit: add the fake `post_chart_to_bluesky` and update assertions)
- `README.md`

## Tests to write

`tests/test_bluesky.py` (uses `httpx.MockTransport` with a small stateful fake PDS that records requests and stores records by rkey, plus a tiny PNG written to `tmp_path`):
- `test_make_tid_is_deterministic_and_valid`: same inputs give the same 13-char TID, the first char is in `234567abcdefghij`, and a different `idempotency_key` or `created_at` gives a different TID.
- `test_post_creates_record_with_image_embed`: the createRecord body has the rkey from `make_tid`, the text, `createdAt` ending in `Z`, an `app.bsky.embed.images` embed with the uploaded blob and alt text, and a bearer token from the session fixture.
- `test_post_twice_posts_once`: the done-when check (one upload and one createRecord; the second result has `already_posted=True` with the same uri).
- `test_existing_record_skips_upload`: when the fake PDS already holds the rkey, no uploadBlob or createRecord call is made.
- `test_auth_failure_is_non_retryable`: a 401 from createSession raises `ApplicationError` with `type == "BlueskyAuthFailed"` and `non_retryable`.
- `test_image_too_large_is_non_retryable`: a file over `MAX_IMAGE_BYTES` raises `ImageTooLarge`, and no upload is made.
- `test_missing_png_is_non_retryable`: a missing path raises `ChartFileMissing`.
- `test_load_credentials_missing_env_raises`: with `BLUESKY_APP_PASSWORD` unset, it raises `RuntimeError` naming the variable, and the password never shows up in `repr(credentials)`.
- `test_post_input_rejects_naive_datetime`.

`tests/test_daily_chart.py` (edit):
- `test_approve_posts_once_with_review_data`: the fake post activity is called once, with `png_path`/`text` from the review, `idempotency_key` equal to the workflow id and an aware `created_at`. `result.post` is the fake output.
- `test_reject_and_expire_do_not_post`: the fake post activity is never called on rejected or expired runs.
- `test_post_failure_fails_workflow`: the fake raises non-retryable `ApplicationError`, so `result()` raises `WorkflowFailureError`.

`tests/test_cli.py` (edit):
- `test_cli_approve_prints_post_uri`: the output contains `posted: <fake uri>`.

`tests/test_worker.py` (edit):
- `test_daily_chart_activities_names`: the names are now the seven activity names, including `post_chart_to_bluesky`.

## Out of scope

- The real (non-test) Bluesky account (016), schedules (012), engagement metrics, Postgres and prompt versions (013).
- Adding the `atproto` package or any other dependency. Session caching or refresh tokens. Facets, links, hashtags, threads or reply handling.
- Compressing or resizing PNGs that exceed the size limit (just fail with `ImageTooLarge`). Changes to `render.py`, `chart_spec.py`, `chart_loop.py` or other activity modules, `justfile`, `CLAUDE.md`, `infra/`, CI and `docs/mvp-plan.md`.
- Reading or committing `.env`. Any live Bluesky call in tests.
- Deleting or editing posts. A CLI command to post outside the workflow.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->
**planner**: Wrote the 011 spec and set it to `planned`. It uses httpx XRPC with no new dependency. Idempotency comes from a deterministic TID rkey (built from `workflow.now()` plus the workflow id) with a getRecord check before upload. Credentials stay in the activity constructor, never in history.
**planner**: Risks: the PDS may reject a hand-built TID (if so, check `make_tid` against the atproto TID spec), and 2x-scale PNGs may go over 1 MB (that fails as `ImageTooLarge`; resizing is a follow-up). The worker now needs the Bluesky env vars at startup.
**planner**: If this is too big, split it into 011a (`bluesky.py` + `test_bluesky.py` + fixtures) and 011b (workflow/worker/CLI wiring + README). This is a `yes` task, so the human approves the spec and does the manual done-when check. Next: the builder uses branch `task/011-bluesky-post`.
**builder**: Built bluesky.py (httpx XRPC, TID rkey, getRecord check), wired workflow approved branch, worker, CLI, README run guide (human-approved addition), tests and fixtures. Targeted tests, ruff and pyright pass.
**builder**: Unsure: PDS acceptance of the hand-built TID and PNG size vs 1 MB are only checked in the manual done-when. Next: verifier.
**verifier**: `just lint` passed (ruff check, ruff format --check, pyright 0 errors). `just test` passed (100 passed, incl. tests/test_bluesky.py). No live HTTP calls were made. Acceptance boxes left unticked: I ran only the automated checks, and the manual done-when still needs the human.
All checks pass.
**reviewer**: Approved. Diff matches spec; all listed tests exist; workflow code is deterministic (workflow.now/workflow_id); secrets stay in activity constructor; fixtures are fake.
Notes: done-when box left unticked for the human manual check. Watch the hand-built TID acceptance by the PDS and PNG size vs 1 MB during that check.
**scribe**: README already has the Bluesky env vars and approve-posts note, so no doc edits were needed. Pushed `task/011-bluesky-post` and opened the PR against `main`.
Next: the human runs the manual done-when check against the test account, then merges the PR.
**scribe**: PR: https://github.com/akonale/ai-trend-pipeline/pull/17
