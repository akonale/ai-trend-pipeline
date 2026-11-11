# 072: Tune news classification in the UI

Status: planned

## Goal

Add one global News filter control that lets an authenticated operator choose Strict, Balanced, or
Relaxed classification. Persist the choice outside workflow history, apply it to subsequent source
polls without a restart, and leave candidates already in the inbox unchanged.

## Acceptance criteria

- [ ] Reuse 071's bounded `NewsFilterMode` / policy definitions; do not duplicate threshold logic in
      the web layer. The three options have plain-language descriptions: Strict selects only
      exceptional general-audience stories, Balanced admits more clearly relevant stories, and
      Relaxed admits more borderline or industry-adjacent stories while still requiring central AI.
- [ ] Add a `NewsFilterSettings` Pydantic model containing the global mode and bounded audit
      metadata (`updated_at` and optional authenticated `updated_by`). Store one singleton settings
      record in Postgres under `topic_studio`; this is editorial configuration only, not candidate,
      source-event, cursor, inbox, story, or review state.
- [ ] Add a `NewsFilterStore` protocol, a Postgres implementation with lazy idempotent schema/table
      setup, and a thin `NewsFilterService` for get/update. A missing row returns the default
      `strict` policy. Invalid stored data fails closed to a typed store error rather than silently
      relaxing the filter; database/connection errors map to typed unavailable errors.
- [ ] Add `GET /news-filter` and `POST /news-filter` behind the existing authenticated-user path.
      The page uses a labelled radio group or equivalent native controls, explains that changes
      affect future polls only, shows the current selection, saves through PRG, and gives an
      accessible success or error notice. Invalid modes return 400; unavailable storage returns 503;
      Cloudflare-mode requests without a user follow existing 401 behaviour.
- [ ] Add News filter to the shared navigation with the correct active state. Use the existing app
      shell, design tokens, and plain HTML/CSS; no JavaScript framework, build step, arbitrary free
      text, or raw threshold fields. Keyboard operation, visible focus, contrast, reduced motion,
      and 360px/1280px layouts meet existing UI requirements.
- [ ] Add a bounded `load_news_filter_settings` activity on the ingestion worker. For each otherwise
      selectable batch, `SourceIngestionWorkflow` loads the current global policy through that
      activity and passes the mode to Jev classification. Saving in the UI therefore affects the
      next polled batch without stopping/restarting ingestion or running `just ingest`.
- [ ] Policy loading remains activity-only I/O and returns a Pydantic model. A load failure after
      retries emits a fixed sanitized log, starts no candidates, makes no Jev or Anthropic call, and
      leaves the batch unprocessed for a later poll. Never treat a storage/API failure as Relaxed or
      as a negative classification.
- [ ] Gate the store-loading behaviour with a new Temporal patch and keep all new workflow fields
      optional/defaulted. Pre-071 histories, 071 histories using the fixed default, and new
      UI-controlled histories replay correctly; continue-as-new does not reset the persisted global
      setting.
- [ ] Existing pending, approved, rejected, expired, generated, or published items are never
      reclassified, deleted, or reopened by a mode change. Candidate budgets, polling intervals,
      source enablement, review decisions, and publishing stay unchanged.
- [ ] Tests use fake stores/services for routes and recorded fixtures/time-skipping for activities
      and workflows. An integration test saves each mode and proves that the next poll's classifier
      receives it without workflow restart; fixed Jev judgments demonstrate that tightening reduces
      eligibility and relaxing increases it within the 071 rules.
- [ ] Update README, web/Temporal/Postgres architecture, configuration and deployment docs, plus
      ingestion/runbook guidance. Production worker and web receive the required database settings;
      no credentials or real account details are committed. Ruff, strict Pyright, and the full
      pytest suite pass.

## Design notes

- This is one global operator setting because source ingestion is global. Do not attach it to the
  per-person chart style profile or allow different users to believe they own different feeds.
- Postgres is appropriate for this mutable UI configuration; Temporal remains the system of record
  for candidates and stories. The source workflow reads the setting through an activity rather than
  importing services, web code, Postgres drivers, or environment readers.
- Show only the three named modes. Numeric threshold tuning remains code-reviewed configuration in
  071's policy table, preventing the UI from producing untested or contradictory rule combinations.
- The UI changes classification only. It does not approve candidates, trigger generation, or publish
  anything.

## Files to touch

- `src/ai_trend_pipeline/services/news_filter.py` (new)
- `src/ai_trend_pipeline/services/postgres_news_filter_store.py` (new)
- `src/ai_trend_pipeline/source_ingestion.py`
- `src/ai_trend_pipeline/ingestion_logging.py`
- `src/ai_trend_pipeline/worker.py`
- `src/ai_trend_pipeline/web/app.py`
- `src/ai_trend_pipeline/web/dependencies.py`
- `src/ai_trend_pipeline/web/routes/news_filter.py` (new)
- `src/ai_trend_pipeline/web/view_models.py`
- `src/ai_trend_pipeline/web/templates/news_filter.html` (new)
- `src/ai_trend_pipeline/web/templates/base.html`
- `src/ai_trend_pipeline/web/static/app.css`
- `infra/compose.prod.yaml`
- `tests/test_news_filter_service.py` (new)
- `tests/test_news_filter_routes.py` (new)
- `tests/test_source_ingestion.py`
- `tests/test_ingestion_logging.py`
- `tests/story_e2e_harness.py` (only if required by shared route/service wiring)
- `tests/fixtures/histories/source_ingestion_*.json` (new replay fixtures)
- `README.md`
- `docs/architecture/README.md`
- `docs/architecture/business.md`
- `docs/architecture/tech.md`
- `docs/runbooks/deploy.md`
- `docs/runbooks/ingestion.md`

## Tests to write

- `test_missing_settings_row_defaults_to_strict`
- `test_store_round_trips_each_mode_and_audit_metadata`
- `test_invalid_stored_mode_and_database_errors_are_typed`
- `test_get_news_filter_shows_current_mode_and_future_poll_copy`
- `test_post_news_filter_validates_saves_and_redirects`
- `test_news_filter_requires_user_and_handles_store_unavailable`
- `test_news_filter_navigation_accessibility_and_mobile_layout`
- `test_next_poll_loads_saved_mode_without_restart`
- `test_policy_store_failure_leaves_batch_unprocessed_and_calls_no_model`
- `test_tightening_and_relaxing_change_only_future_eligibility`
- `test_mode_change_does_not_reclassify_existing_candidates`
- `test_continue_as_new_preserves_global_ui_policy`
- `test_pre_071_071_and_ui_controlled_histories_replay`
- `test_policy_loading_logs_are_sanitized`

## Out of scope

- No Jev integration, question wording, or threshold-table implementation beyond reusing 071.
- No arbitrary numeric weights/thresholds, custom prompts, per-user modes, or per-source modes.
- No retroactive reclassification, candidate deletion, automatic decisions, or review UI changes.
- No new source connectors, chart settings, story-generation controls, media, or publishing changes.
- No live TypeSafe/Anthropic calls in tests.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: Wrote the global UI policy spec with Postgres persistence, per-poll activity loading, replay gates, and no retroactive candidate changes. Depends on 071; next: approve after 071's contracts are settled, then builder writes tests first.
