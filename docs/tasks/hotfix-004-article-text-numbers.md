# Hotfix 004: Article-only stories may use numbers from the article text

Status: built

Goal: article-only stories (no facts) failed `numerical_fidelity` when the draft quoted numbers from the fetched article that are not in the title, summary or angle.
Acceptance: with `facts.status == EMPTY`, numbers in the article context title or text are allowed in prose and key points.
Acceptance: with `facts.status == EMPTY`, numbers absent from the article text and the approved context still block.
Acceptance: with available facts, article text numbers do not widen the allowed set.
Files: `story_drafting/validation.py`, `tests/test_story_drafting_validation.py`.

## Handoff

- Found in prod: `story-v1-4827d2c34d318d98` failed `numerical_fidelity` after hotfix 003.
