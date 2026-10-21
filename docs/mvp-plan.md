# MVP Plan: agentic build

**Goal:** one chart posted to Bluesky every day, automatically, with a human approving each one.
**Done when:** 7 days in a row of charts posted, with engagement recorded for each.

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

## Task sequence

One task at a time, in order. Each one ends in a merged PR.

### Phase 0: Foundation
| # | Task | Done when |
| --- | --- | --- |
| 001 | Project skeleton: uv, Python 3.12, Pydantic, Temporal SDK, Ruff, Pyright strict, pytest, justfile | `just lint` and `just test` pass on an empty project |
| 002 | CLAUDE.md with project rules and the review checklist | Agents can follow it without asking |
| 003 | Local stack: docker-compose Postgres, Temporal dev server, `just dev`, hello workflow + test | Hello workflow visible in the Temporal UI |
| 004 | CI: GitHub Actions runs lint, types, tests on every PR | A PR shows green checks |

### Phase 1: Daily Viz
| # | Task | Done when |
| --- | --- | --- |
| 005 | Activity: fetch Hacker News top stories | Returns typed stories; tested with a fixture |
| 006 | Activity: LLM picks a chartable topic | Returns topic + reason; tested with a recorded response |
| 007 | Activity: fetch a matching Our World in Data dataset | Returns a typed table for a known topic |
| 008 | Activity: LLM writes a Vega-Lite spec + caption; render to PNG | A PNG file is produced from a fixture |
| 009 | Activity: LLM critique, one regenerate on failure | Bad fixture triggers one retry |
| 010 | Workflow: wire 005–009 together + approval signal + CLI to approve/reject | Workflow pauses, CLI approves, workflow continues |
| 011 | Activity: post to Bluesky (test account), idempotent | Approved chart appears on the test account once |
| 012 | Daily Temporal Schedule | Runs on its own at the set time |

### Phase 2: Measure
| # | Task | Done when |
| --- | --- | --- |
| 013 | Feedback workflow: wait 24h, save likes/reposts/replies + prompt version to Postgres | Time-skipping test passes; real post gets its numbers |

### Phase 3: Deploy and go live
| # | Task | Done when |
| --- | --- | --- |
| 014 | Production compose file + prod server from `infra/cloud-init.yaml` | Stack runs on the prod server |
| 015 | Deploy on merge to main + nightly Postgres backup | Merging a PR updates prod |
| 016 | Switch to the real Bluesky account, run 7 days | 7 charts posted with metrics |

## Running a task

In Claude Code on the dev server, inside the repo:

```
/run-task 001 project skeleton
```

The main session only orchestrates: it calls each agent in turn, reads the status line, and stops at
the two human checkpoints (approve the spec, merge the PR). Run the main session on `sonnet`
(`/model sonnet`); it routes work, it doesn't do it.
