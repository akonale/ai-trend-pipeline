---
description: Run one task from docs/mvp-plan.md through the planner → builder → verifier → reviewer → scribe relay
argument-hint: <task number> <short name>
---

Run task **$ARGUMENTS** from `docs/mvp-plan.md` through the agent relay described there.

You are the orchestrator. Do not write code, specs, or reviews yourself; delegate each step to the
named subagent and read only the task file's status line and newest handoff note between steps.

1. If `docs/tasks/<NNN>-*.md` does not exist, call the **planner**. Then show the human the spec
   and **stop until they approve it**. When they do, set `Status: approved`.
2. Call the **builder**.
3. Call the **verifier**. If status is `needs-fix`, go back to step 2.
4. Call the **reviewer**. If status is `needs-fix`, go back to step 2.
5. Call the **scribe**.
6. Tell the human the PR link in one line. Stop; the human merges.

Count fix loops (returns to step 2). After 2, stop and ask the human what to do, quoting the latest
handoff note.

If a subagent says the spec is wrong or unclear, stop and ask the human.
