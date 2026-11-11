# 071: Strict general-audience AI news selection

Status: planned

## Goal

Make the existing batch editorial selector choose only AI-centered stories with exceptional,
easily understood appeal for ordinary readers. Prefer a small inbox—and no selection when nothing
qualifies—over routine AI industry, enterprise, or technical news.

## Acceptance criteria

- [ ] `SYSTEM_PROMPT` makes AI centrality a hard requirement: the story must be substantially about
      AI, its use, or its effects, not merely mention an AI company, product, or buzzword.
- [ ] The prompt requires both strong human interest and easy comprehension. A selectable story
      should have a concrete human hook, an unusual achievement or use, a clear everyday benefit or
      harm, or an accessible controversy. Significance inside the AI industry alone is insufficient.
- [ ] The default is selective: the model may return zero candidates, must not fill the requested
      cap, and must reject a story when the available event data does not establish its appeal.
      High scores, comments, or other engagement cannot override the editorial requirements.
- [ ] Routine enterprise and industry stories are excluded: databases, data platforms, cloud or
      backend infrastructure, developer tooling, benchmarks, technical research without a tangible
      human outcome, minor product/model updates, and routine acquisitions, funding, partnerships,
      or executive moves. An exception requires an explicit, obvious effect on ordinary people in
      the normalized event data.
- [ ] Positive topics include accessible stories about AI in creativity, books, music, art,
      entertainment, education, health, work, consumer life, scams, safety, relationships, and
      society. These topics are not an automatic pass; the story still needs a compelling hook.
- [ ] The prompt includes the calibration examples: **accept** “370 Books in 20 months: South
      African author defends her use of AI” because it combines a person, a remarkable achievement,
      and an understandable controversy; **reject** ClickHouse/data-platform acquisition news
      because it is enterprise infrastructure without an obvious everyday impact.
- [ ] Optional editorial `instructions` may narrow or refine selection but cannot relax these core
      requirements. Event fields remain untrusted data, and prompt-injection behaviour stays
      unchanged.
- [ ] Selection output remains plain and accessible. `summary`, `proposed_angle`, and
      `selection_reason` must not invent facts beyond the normalized event data; the reason should
      identify the human-interest hook rather than popularity or industry importance.
- [ ] The change stays within the existing batch selector: one LLM call per batch, the same Pydantic
      input/output contracts, deterministic prefiltering, selected-ID validation, selection caps,
      error policy, and no workflow starts from the activity.
- [ ] Recorded-fixture tests cover a mixed batch where the South African author story is selected
      while ClickHouse/data-platform acquisition news, a technical AI benchmark, a routine model
      update, and a non-AI human-interest story are not. Another batch of only routine or technical
      AI news returns zero selections, including when those events have high engagement.
- [ ] Update the story-candidate selection policy in `docs/architecture/business.md` and the
      candidate overview in `README.md`. No live HTTP or LLM calls are added to tests.
- [ ] Ruff, strict Pyright, and the full pytest suite pass; existing candidate ingestion, story
      generation, chart, media, and publishing behaviour is unchanged.

## Design notes

- Implement this as an editorial-policy change in `src/ai_trend_pipeline/story_selection.py`, not a
  keyword blocklist. A rigid keyword prefilter would miss unexpected human stories and could not
  reliably judge whether AI is central or whether the hook is understandable.
- Keep the policy in the fixed system prompt so every batch receives it. The existing optional
  instructions remain a separate user-prompt section and are subordinate to the core policy.
- Preserve `SelectStoryCandidatesInput`, `SelectedStory`, and `SelectStoryCandidatesOutput`; do not
  add score fields, labels, rejection lists, or extra model calls. The inbox already supports zero
  selections.
- Use the two supplied examples directly in the prompt as calibration, while making clear that the
  general rules—not string matching—decide other stories.
- Recorded fixtures specify expected activity outputs and request/response handling; prompt tests
  must separately assert the complete selection rubric so a fixture alone is not treated as proof
  of editorial quality.

## Files to touch

- `src/ai_trend_pipeline/story_selection.py`
- `tests/test_story_selection.py`
- `tests/fixtures/llm_select_story_candidates_general_audience.json` (new)
- `tests/fixtures/llm_select_story_candidates_routine_ai_zero.json` (new)
- `docs/architecture/business.md`
- `README.md`

## Tests to write

- `test_system_prompt_requires_central_ai_and_general_audience_interest`
- `test_system_prompt_requires_a_compelling_human_hook_and_allows_zero`
- `test_system_prompt_rejects_enterprise_technical_and_routine_industry_news`
- `test_system_prompt_calibrates_author_and_clickhouse_examples`
- `test_engagement_and_custom_instructions_cannot_override_core_policy`
- `test_mixed_batch_keeps_only_accessible_human_interest_ai_story`
- `test_routine_or_technical_ai_batch_returns_zero_even_with_high_engagement`
- `test_general_audience_selection_preserves_output_contract_and_id_validation`
- Update the existing prompt-guidance test only where the expanded policy requires it; retain all
  injection, malformed-output, retry, empty-batch, cap, and no-workflow-start coverage.

## Out of scope

- No source connectors, HN polling, source registry, article fetching, or new AI-news sources.
- No deterministic AI keyword filter or popularity threshold.
- No `SourceIngestionWorkflow`, candidate workflow, Temporal gateway, service, visibility, worker,
  configuration, or environment-variable changes.
- No candidate UI, story-generation, research, media, publishing, chart, or Bluesky changes.
- No live-model benchmark, semantic cross-source deduplication, rejection analytics, or new database
  tables.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: Wrote the spec from the requested general-audience filtering policy and accept/reject examples. Next: approve the spec, then builder writes the fixture and prompt tests first.
