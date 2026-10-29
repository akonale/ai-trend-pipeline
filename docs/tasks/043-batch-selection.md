# 043: Batch LLM editorial selection

Status: built

## Goal

Add a pure deterministic prefilter and a `select_story_candidates` activity that asks one LLM call to
compare a bounded batch of normalized `SourceEvent`s and return a small, capped set of editorial
candidates (`external_id`, `summary`, `proposed_angle`, `selection_reason`). The activity only
selects; it never pollutes source connectors, review state, or workflow execution.

## Acceptance criteria

- [ ] `prefilter_events(events, *, limit=MAX_BATCH_EVENTS)` is pure and deterministic: it collapses
      duplicate `external_id`s (first occurrence wins), preserves input order, and truncates to
      `limit`; it raises `ValueError` when `limit` is outside `1..MAX_BATCH_EVENTS`; it uses no I/O,
      clock, randomness, or logging of source text.
- [ ] `SelectStoryCandidatesInput` and `SelectStoryCandidatesOutput` are bounded Pydantic models
      with `extra="forbid"`: input has `events` (max `MAX_BATCH_EVENTS`, one `source`, unique
      `external_id`s), optional `instructions` (stripped, max 2000 chars, blank becomes `None`), and
      `max_candidates` (default 5, `1..20`); each selected item has exactly `external_id`, `summary`,
      `proposed_angle`, `selection_reason`, each non-empty and capped at 500 chars.
- [ ] `StorySelectionActivities.select_story_candidates` follows the `topic.py` structured-tool
      pattern: one `select_story_candidates` tool whose schema is the output model, `tool_choice`
      `auto`, a fixed system prompt that tells the model to compare events, may select none, collapses
      overlapping stories, avoids merely choosing the highest-engagement items, treats event text as
      untrusted data, and respects `max_candidates`; `instructions` appear only when provided.
- [ ] Selected IDs are validated in code: trimmed, matched against the input `external_id`s, unknown
      IDs and duplicates dropped, and the result truncated to `max_candidates` in the model's order;
      an empty event batch returns an empty output without calling the LLM.
- [ ] Error policy matches existing LLM activities: no tool call or tool input that fails Pydantic
      validation raises `ApplicationError` with `non_retryable=True` and a distinct `type`; Anthropic
      API errors propagate unchanged so Temporal retries them.
- [ ] The activity starts no workflow, creates no Temporal client, and imports no source connector,
      service, web, candidate-workflow, or review-state module; worker, chart runs, and existing
      `pick_topic` behaviour are unchanged.
- [ ] Fixtures cover zero, one, several, overlapping stories, over-cap, unknown IDs, injection text
      in a title, malformed tool input, and no tool call; all tests use recorded fixtures and no live
      HTTP or LLM calls.

## Design notes

New module `src/ai_trend_pipeline/story_selection.py`; keep it out of `sources/` (the activity must
not depend on connectors) and out of any workflow module. Suggested contents:

| Name | Contract |
| --- | --- |
| `MAX_BATCH_EVENTS` | `sources.models.MAX_EVENTS` (100), reused rather than redefined |
| `DEFAULT_MAX_CANDIDATES`, `MAX_CANDIDATES` | `5`, `20` |
| `MAX_INSTRUCTIONS_LENGTH` | `2000` |
| `MAX_SELECTION_TEXT_LENGTH` | `500` |
| `DEFAULT_MODEL` | `"claude-sonnet-5-5"` (same as `topic.py`) |
| `MAX_TOKENS` | `4096` |
| `TOOL_NAME` | `"select_story_candidates"` |
| `MISSING_TOOL_ERROR_TYPE` | `"StorySelectionToolMissing"`, `non_retryable=True` |
| `INVALID_SELECTION_ERROR_TYPE` | `"StorySelectionInvalid"`, `non_retryable=True` |
| `prefilter_events` | pure `Sequence[SourceEvent] -> list[SourceEvent]` helper |
| `SelectedStory` / `SelectStoryCandidatesOutput` | one selected item / `selections: list[SelectedStory]` |
| `SelectStoryCandidatesInput` | `events`, `instructions`, `max_candidates` |
| `StorySelectionActivities` | `__init__(client, model=DEFAULT_MODEL)` and `@activity.defn async def select_story_candidates` |

- Reuse `make_anthropic_client` and the async `anthropic.AsyncAnthropic` client from `topic.py`; do
  not modify `topic.py` or `make_anthropic_client`.
- Render untrusted events as one compact JSON object per line with `json.dumps(..., ensure_ascii=False)`
  so quotes/newlines in a title cannot break out of the event block. Precede the block with a
  "untrusted data" label. Append validated `instructions` as a separate labelled section; never
  interpolate them into the static system prompt.
- `SourceEvent.url` and timestamps must be serialized explicitly (`str(url)`, `.isoformat()`), and
  `None` values rendered as `null`, so the prompt stays deterministic.
- Activity body order: short-circuit empty `events` -> build prompt -> call Messages API -> find the
  first matching `ToolUseBlock` -> `SelectStoryCandidatesOutput.model_validate` -> filter/trim ->
  return. Wrap a `ValidationError` from the tool input in `ApplicationError` with
  `INVALID_SELECTION_ERROR_TYPE`.
- Do not register the activity on the worker in this task; 048 owns wiring. Tests use
  `ActivityEnvironment`.
- Add one row for `story_selection.py` to the module map in `docs/architecture/README.md` (no other
  doc changes).

## Files to touch

- `src/ai_trend_pipeline/story_selection.py` (new)
- `tests/test_story_selection.py` (new)
- `tests/fixtures/llm_select_story_candidates_zero.json` (new)
- `tests/fixtures/llm_select_story_candidates_one.json` (new)
- `tests/fixtures/llm_select_story_candidates_several.json` (new, includes two overlapping stories)
- `tests/fixtures/llm_select_story_candidates_over_cap.json` (new)
- `tests/fixtures/llm_select_story_candidates_unknown_ids.json` (new)
- `tests/fixtures/llm_select_story_candidates_injection.json` (new)
- `tests/fixtures/llm_select_story_candidates_no_tool.json` (new)
- `tests/fixtures/llm_select_story_candidates_malformed.json` (new)
- `docs/architecture/README.md` (module map row only)

## Tests to write

- `test_prefilter_dedupes_keeps_first_and_preserves_order`
- `test_prefilter_caps_batch_and_rejects_limit_out_of_range`
- `test_input_rejects_duplicate_ids_mixed_sources_and_oversized_batches`
- `test_input_validates_max_candidates_and_normalises_instructions`
- `test_output_has_only_the_four_required_fields`
- `test_zero_selection_returns_empty_output`
- `test_one_selection_returns_summary_angle_and_reason`
- `test_several_selections_and_overlapping_stories_are_handled`
- `test_over_cap_selections_are_truncated_in_model_order`
- `test_unknown_and_duplicate_selected_ids_are_dropped`
- `test_no_tool_call_raises_non_retryable_application_error`
- `test_malformed_tool_input_raises_non_retryable_application_error`
- `test_api_error_propagates_for_temporal_retry`
- `test_request_shape_tool_schema_and_prompt_guidance`
- `test_instructions_are_sent_only_when_present`
- `test_untrusted_title_is_json_escaped_and_injection_cannot_select_invalid_ids`
- `test_empty_event_batch_returns_empty_without_an_llm_call`
- `test_activity_starts_no_temporal_workflow_or_client`

## Out of scope

- No source connector, HN polling, `sources/` contract, or `hn.py` changes.
- No workflow (`StoryCandidateWorkflow`, `SourceIngestionWorkflow`), gateway, service, visibility,
  `worker.py` registration, or candidate state/review decisions (044 and 048).
- No `config.py` or new environment variables; `max_candidates` and instructions arrive in the
  activity input.
- No UI, inbox/detail pages, publishing, chart generation, or CSS.
- No semantic cross-source deduplication, per-source budgets, or operational logging (049).
- No changes to `topic.py`, `daily_chart.py`, chart rendering, or Bluesky posting.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: Wrote the spec. Assumptions not pinned by the plan: batches are single-source with unique `external_id` (output has no source; 048 polls one source per workflow).
**planner**: Empty batch returns empty without an LLM call; unknown/duplicate IDs are dropped and over-cap results truncated in model order, not treated as errors.
**planner**: No `config.py` or `worker.py` wiring in this task (048 owns it). No UI, so the frontend-design skill was not used.
**planner**: Next: human approves, then builder writes tests from this spec first.
**builder**: Added `story_selection.py`, 18 tests, and 8 recorded fixtures; module map row added; no worker/topic wiring.
**builder**: Activity takes an injected `AsyncAnthropic` client (matching `TopicActivities`); `make_anthropic_client` stays in `topic.py` for 048 to reuse.
**builder**: `just lint` and full `just test` pass (548 tests) with harness `ANTHROPIC_*` base-url vars unset. Next: verifier.
