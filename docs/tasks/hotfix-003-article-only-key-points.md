# Hotfix 003: Article-only key points may use approved numbers

Status: built

Goal: stop article-only stories (no facts) failing validation just because a key point contains a number from the title or summary.
Acceptance: with `facts.status == EMPTY`, an uncited key point whose numbers all come from the approved context (title, summary, angle, dates) passes.
Acceptance: with `facts.status == EMPTY`, an uncited key point with a number outside the approved context is blocked as `numerical_fidelity`.
Acceptance: with available facts, uncited key point numbers still block as `numerical_fidelity` and `missing_citation`.
Files: `story_drafting/validation.py`, `tests/test_story_drafting_validation.py`.

## Handoff

- Found in prod: `story-v1-efa73c72ea16bbef` failed because "Postgres 19" in a key point had no fact to cite.
