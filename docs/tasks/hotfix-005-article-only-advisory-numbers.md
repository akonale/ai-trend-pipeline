# Hotfix 005: Article-only unsupported numbers are advisory

Status: built

Goal: article-only stories (no facts) kept failing `numerical_fidelity` (story-v1-4827d2c34d318d98); there is nothing to verify numbers against, so flag them for the reviewer instead of failing the story.
Acceptance: with `facts.status == EMPTY`, numerical-fidelity findings are `advisory`, so `draft_story` no longer raises and the critique step still runs.
Acceptance: with available facts, numerical-fidelity findings stay `blocking`.
Acceptance: other deterministic checks (fact IDs, source links, URLs in prose) stay blocking in every case.
Files: `story_drafting/validation.py`, `tests/test_story_drafting_validation.py`.

## Handoff

- Follows hotfix 003 and 004; a human still approves every story before it publishes.
