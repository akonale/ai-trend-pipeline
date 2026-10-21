# 002: CLAUDE.md with project rules and the review checklist

Status: pr-open

## Goal

Add a short root `CLAUDE.md` that every agent (builder, verifier, reviewer, scribe, planner) can follow without asking questions. It should cover the stack, commands, code and Temporal rules, testing rules, the task workflow and status values, and a numbered review checklist. Add a small test that fails if the required sections go missing.

## Acceptance criteria

- [x] `CLAUDE.md` exists at the repo root, is at most 150 lines (Claude Code loads it into every session), and uses these `##` headings in this order: `Stack`, `Commands`, `Code rules`, `Temporal rules`, `Testing rules`, `Task workflow`, `Review checklist`.
- [x] `Commands` lists `just lint`, `just fmt`, `just test` exactly as the `justfile` defines them, and gives the `uv run ...` command behind each one as a fallback when `just` is not installed (this happened in task 001). It also says to add dependencies with `uv add` / `uv add --dev`, never with `pip`.
- [x] `Code rules` and `Temporal rules` say: src layout under `src/ai_trend_pipeline/`, all config in `pyproject.toml`, Pyright strict with no `# type: ignore` or `Any` unless a comment explains why, Pydantic models for every activity and workflow input and output, no I/O, randomness, clock or LLM calls in workflow code (use activities or Temporal's deterministic APIs), activities are idempotent, and no secrets or real account details in the repo (read them from env vars).
- [x] `Testing rules` says: tests go first, they live under `tests/`, and LLM and HTTP calls use recorded fixtures under `tests/fixtures/` (no live API calls). Temporal workflows are tested with the time-skipping test environment.
- [x] `Task workflow` describes the relay as one line per agent with a pointer to `docs/mvp-plan.md`. It lists every status value, including `draft` and `needs-fix` (agents use these but the plan's status line does not show them), names the branch format `task/NNN-short-name`, caps handoff notes at 5 lines, allows at most 2 fix loops, and says only the human approves specs and merges PRs.
- [x] `Review checklist` is a numbered list the reviewer can tick off line by line. It covers the acceptance criteria, the listed tests, scope, the Temporal rules, types, fixtures and no live calls, secrets, and docs or README updated when behaviour changed. `just lint` and `just test` still pass.

## Files to touch

- `CLAUDE.md` (new)
- `tests/test_claude_md.py` (new)

## Tests to write

- `test_claude_md_has_required_sections`: reads `CLAUDE.md` from the repo root (found from `__file__`, not the cwd) and checks that all seven `##` headings are present in the required order.
- `test_claude_md_is_short`: `CLAUDE.md` has at most 150 lines.
- `test_claude_md_commands_match_justfile`: each recipe name in `justfile` other than `default` appears in `CLAUDE.md` as `just <name>`.

## Out of scope

- Editing `.claude/agents/*.md`, `.claude/commands/run-task.md` or `docs/mvp-plan.md`. If CLAUDE.md contradicts them, the builder notes it in the handoff log and does not fix it.
- Changes to `justfile`, `pyproject.toml`, `README.md` or `src/`.
- Rules for things that don't exist yet (docker-compose, CI, deploy, Bluesky specifics) beyond a one-line mention.
- Installing `just` or adding any tooling or dependencies.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->
**planner**: Wrote the spec for 002. Status is `draft` (as requested) rather than `planned`. Next: human approves, then builder.
**planner**: "Agents can follow it without asking" is hard to check, so the spec pins it down with fixed sections, a line cap, and a test tying commands to the justfile. The review checklist should be at least as strict as `.claude/agents/reviewer.md` step 3 and must not contradict it.
**planner**: The plan's status line leaves out `draft` and `needs-fix`, which agents already use. CLAUDE.md should list them. The plan file itself stays unchanged.
**builder**: Added `CLAUDE.md` (83 lines, 7 required sections, 11-item checklist) and `tests/test_claude_md.py` (3 tests). Tests written first; they pass, and `just lint` passes.
**builder**: No contradiction with `.claude/agents/*.md` found; checklist covers reviewer step 3. Spec file was untracked on main, so it is committed on this branch too. Next: verifier.
**verifier**: Ran `just lint` (ruff check, ruff format --check, pyright: all pass) and `just test` (5 passed, incl. 3 CLAUDE.md tests). All checks pass.
**reviewer**: Approved. All 6 acceptance criteria and 3 listed tests check out; only CLAUDE.md, the new test and the task file changed.
Note: the recipes and fallback commands match the justfile; Python version in Stack ("3.12+") is slightly looser than pyproject (>=3.12,<3.13), harmless.
**scribe**: Ticked the verified acceptance boxes, opened PR https://github.com/akonale/ai-trend-pipeline/pull/5 against main. Next: human reviews and merges.
