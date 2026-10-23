# CLAUDE.md

Project rules for every agent (planner, builder, verifier, reviewer, scribe). Keep this file short.

## Stack

- Python 3.12+, managed with `uv`. Temporal (`temporalio`) for workflows. Pydantic v2 for models.
- Ruff (lint + format), Pyright (strict), pytest, `just` as the task runner.
- Source in `src/ai_trend_pipeline/`, tests in `tests/`, specs and handoffs in `docs/tasks/`.
- Plan and agent relay: `docs/mvp-plan.md`. Infra files live in `infra/`.

## Commands

| Command     | Runs (fallback if `just` is not installed)                                  |
| ----------- | --------------------------------------------------------------------------- |
| `just lint` | `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`     |
| `just fmt`  | `uv run ruff format .`, `uv run ruff check --fix .`                         |
| `just test` | `uv run pytest`                                                             |
| `just dev`  | `docker compose -f infra/compose.dev.yaml up -d --wait`, then `uv run python -m ai_trend_pipeline.worker` |
| `just hello`| `uv run python -m ai_trend_pipeline.start_hello`                            |
| `just chart`| `uv run python -m ai_trend_pipeline.cli start` (accepts `--topic`, `--id`; also `just show/approve/reject <id>`) |
| `just down` | `docker compose -f infra/compose.dev.yaml down`                             |

- Add dependencies with `uv add <pkg>` or `uv add --dev <pkg>`. Never use `pip`.
- Single test file: `uv run pytest tests/test_x.py`.

## Code rules

- src layout: all code under `src/ai_trend_pipeline/`.
- All tool config lives in `pyproject.toml`. No separate ruff, pyright or pytest config files.
- Pyright strict. No `# type: ignore` and no `Any` unless a comment explains why.
- Every activity and workflow input and output is a Pydantic model.
- No secrets or real account details in the repo. Read them from environment variables.
- Keep changes small and in scope. No speculative code for things that don't exist yet
  (deploy, Bluesky specifics).

## Temporal rules

- No I/O, randomness, clock or LLM calls in workflow code. Do them in activities, or use
  Temporal's deterministic APIs (`workflow.now()`, `workflow.random()`, `workflow.uuid4()`).
- Activities are idempotent: running one twice with the same input is safe.
- Workflow and activity inputs and outputs are Pydantic models, not bare dicts or tuples.

## Testing rules

- Tests go first: write them from the spec, then the code.
- Tests live under `tests/`.
- LLM and HTTP calls use recorded fixtures under `tests/fixtures/`. No live API calls.
- Test Temporal workflows with the time-skipping test environment
  (`WorkflowEnvironment.start_time_skipping()`).

## Task workflow

Full relay in `docs/mvp-plan.md`. One job per agent, then stop.

- planner: writes the spec in `docs/tasks/NNN-short-name.md`, sets `planned`.
- human: approves the spec (`approved`).
- builder: branch, tests first, then code, commits, sets `built`.
- verifier: runs `just lint` and `just test`, sets `verified` or `needs-fix`.
- reviewer: checks the diff against the checklist below, sets `reviewed` or `needs-fix`.
- scribe: updates docs, pushes, opens the PR, sets `pr-open`.
- human: merges the PR (`merged`).

Rules:

- Status values: `draft`, `planned`, `approved`, `built`, `verified`, `needs-fix`, `reviewed`,
  `pr-open`, `merged`.
- Branch format: `task/NNN-short-name`, created from `main`.
- Handoff notes are at most 5 lines each, appended to the task's handoff log.
- At most 2 fix loops per task, then stop and ask the human.
- Approval follows the "Your approval?" column in `docs/mvp-plan.md`. For `yes` tasks only the human
  approves specs and merges PRs. For `no` tasks the orchestrator does both (CI green first); agents
  never merge.
- A PR is ready to merge only when the CI check is green.

## Review checklist

Tick off each item against `git diff main...HEAD`. Report only real problems.

1. Every acceptance criterion in the spec is met.
2. Every test listed in the spec exists and tests what it says.
3. Nothing outside the spec's scope or "Files to touch" changed.
4. Temporal rules hold: no I/O, randomness, clock or LLM calls in workflow code.
5. Activities are idempotent.
6. Activity and workflow inputs and outputs are Pydantic models.
7. Types: Pyright strict is satisfied, no unexplained `# type: ignore` or `Any`.
8. LLM and HTTP tests use fixtures under `tests/fixtures/`, with no live calls.
9. No secrets, keys or real account details committed.
10. `README.md` (and `CLAUDE.md` if a rule changed) updated when behaviour changed.
11. `just lint` and `just test` still pass.
