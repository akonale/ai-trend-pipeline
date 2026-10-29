# MVP Plan: agentic build

**Goal:** one chart posted to Bluesky every day, automatically, with a human approving each one.
**Done when:** 7 days in a row of charts posted, with engagement recorded for each.
(MVP completed, see docs/history.md)

## How the work flows

Every task goes through the same relay. Each agent does **one job**, writes a short handoff note,
and stops. The next agent picks up from the note, not from a long conversation.

```
 planner ──▶ YOU approve ──▶ builder ──▶ verifier ──▶ reviewer ──▶ scribe ──▶ YOU merge
 (opus)       the spec       (sonnet)    (haiku)      (sonnet)     (haiku)
                                ▲           │ fail        │ changes needed
                                └───────────┴─────────────┘   (max 2 loops, then ask YOU)
```

| Agent | Model | Its one job | Can edit code? |
| --- | --- | --- | --- |
| planner | opus | Turn a task title into a spec: goal, acceptance criteria, files to touch, test list | No (writes the task file only) |
| builder | sonnet | Implement the spec on a branch, tests first | Yes |
| verifier | haiku | Run lint, type check, tests; report only failures | No |
| reviewer | sonnet | Read the diff against the spec and the checklist in CLAUDE.md | No |
| scribe | haiku | Update docs, commit, push, open the PR | Docs only |

**Why these models:** planning mistakes are the most expensive to undo, so the planner gets the
strongest model, and its output is short. Building and reviewing need solid coding judgment, so they
use the mid-tier model. Running commands and writing commit messages are mechanical, so they use
the cheapest one.

## The handoff file

Each task has one file: `docs/tasks/NNN-short-name.md`. It is the only shared memory between agents.

- **Spec** (planner): goal, acceptance criteria, files to touch, tests to write.
- **Handoff log**: each agent appends at most 5 lines: what it did, what's next, anything odd.
- **Status** line at the top: `planned → approved → built → verified → reviewed → pr-open → merged`.

## Token-saving rules (all agents)

1. Read the task file first, then only the files it lists. No whole-repo exploration.
2. The verifier reports failures only, trimmed to the relevant lines.
3. The reviewer reads `git diff main...HEAD`, not whole files.
4. Tests use recorded fixtures for LLM and HTTP calls. No live API calls in the loop.
5. At most 2 fix loops per task; after that, stop and ask the human.

## Tasks

| # | Task | Done when | Your approval? |
| --- | --- | --- | --- |

No open tasks. Add new rows here; approval follows the last column.

## Web architecture (rules for every Topic Studio task)

Moved to the "Web app" section of [docs/architecture/tech.md](architecture/tech.md).

## Running a task

In Claude Code on the dev server, inside the repo:

```
/run-task 036 short task name
```

The main session only orchestrates: it calls each agent in turn, reads the status line, and stops at
the two human checkpoints (approve the spec, merge the PR) for tasks marked `yes` in the
**Your approval?** column. Run the main session on `sonnet`
(`/model sonnet`); it routes work, it doesn't do it.

Finished specs are removed from `docs/tasks/` after merge and summarised in `docs/history.md`.
