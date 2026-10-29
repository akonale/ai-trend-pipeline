# Ingestion runbook

`SourceIngestionWorkflow` is one long-running Temporal workflow per enabled source. It polls the
source activity, drops stories already in its bounded processed-id set, asks
`select_story_candidates` for a bounded batch, and starts one `StoryCandidateWorkflow` per selected
story. Cursor, processed ids and the per-day candidate budget live only in workflow state. Worker
startup never starts ingestion; the operator command below does.

Read [search-attributes.md](search-attributes.md) first on a fresh stack: candidate starts fail
without the two keyword attributes. The [deploy runbook](deploy.md) covers the stack itself.

## CLI wrappers

Run every command from the repo root. The same wrappers are defined in
[search-attributes.md](search-attributes.md):

```sh
# dev (Temporal container from `just dev`; the worker runs on the host)
dev-temporal()  { docker compose -f infra/compose.dev.yaml exec temporal temporal "$@"; }
# prod
prod-temporal() { env -i PATH="$PATH" HOME="$HOME" docker compose -f infra/compose.prod.yaml \
  --env-file .env.prod exec temporal temporal "$@"; }
```

If the server image has no `temporal` binary, install the Temporal CLI on the host and pass
`--address <host>:7233`; the commands are otherwise identical.

## 1. Ensure the workflows

Run after every deploy and after any source change. It is idempotent: existing workflows are
returned with `USE_EXISTING`, and a stopped or terminated id is started again with
`ALLOW_DUPLICATE`.

```sh
just ingest
# prod
env -i PATH="$PATH" HOME="$HOME" docker compose -f infra/compose.prod.yaml --env-file .env.prod \
  exec worker python -m ai_trend_pipeline.ingestion_starter
```

`just ingest` reads `.env` (the justfile has `dotenv-load`) and prints one workflow id per enabled
source. `SOURCE_ENABLED=none` ensures nothing and exits 0.

Verify:

```sh
dev-temporal workflow describe --workflow-id source-ingestion-v1-hn
dev-temporal workflow describe --workflow-id source-ingestion-v1-fake
dev-temporal workflow list --query 'WorkflowType = "SourceIngestionWorkflow"'
dev-temporal workflow query --workflow-id source-ingestion-v1-hn --type ingestion_state
```

The `ingestion_state` query reports the source, cursor, bounded processed ids, poll counters,
`candidate_budget`, `budget_day`, `candidates_started` and `budget_remaining` (as of the last poll).
The workflow id is `source-ingestion-v1-<source>`; only enabled sources have one.

The budget, poll limit, poll interval and review timeout are baked into the workflow input when
the workflow starts. Changing any of these environment variables does **not** change a running
workflow: stop it, then re-run `just ingest` (see the next section).

## 2. Enable or disable a source

`SOURCE_ENABLED` is a comma-separated list of registered names (`hn`, `fake`); the default is
`hn`. `fake` is a test-only source and must never be put in `.env.prod`.

Enable: set `SOURCE_ENABLED=hn,fake` in `.env`, then run `just ingest`. No worker restart is needed,
because the worker always registers both `poll_hn_source` and `poll_fake_source`.

Disable: remove the name from `SOURCE_ENABLED`, run `just ingest` (it starts nothing new but never
stops a running workflow), then stop the old workflow:

```sh
dev-temporal workflow signal --workflow-id source-ingestion-v1-fake --name stop
```

The workflow returns `SourceIngestionResult(reason="stopped")` on its next loop. If it is stuck in
an activity, terminate it instead:

```sh
dev-temporal workflow terminate --workflow-id source-ingestion-v1-fake --reason "source disabled"
```

Pending candidates are child workflows started with `ParentClosePolicy.ABANDON`; stopping,
terminating or continuing-as-new the source workflow leaves them reviewable and decidable.
Re-enabling and running `just ingest` starts a new run of the same id (`ALLOW_DUPLICATE`).

Changing `SOURCE_POLL_LIMIT`, `SOURCE_POLL_INTERVAL_S`, `CANDIDATE_REVIEW_TIMEOUT_S` or
`SOURCE_CANDIDATE_BUDGET` also requires stop plus `just ingest`, because settings are baked into
the workflow input. The budget is per source per UTC day (default 20, 1-500); each enabled source
gets its own full budget.

## 3. Recover a failed or silent source

Work through this order.

- Check `describe`/`show` and the `ingestion_state` query. `FAILED`, `TERMINATED` or `CANCELED`:
  run `just ingest`; with `ALLOW_DUPLICATE` it starts a fresh run.
- `RUNNING` but `last_poll_at` is stale: confirm the worker is up and polling
  `source-ingestion-task-queue`; check the Temporal UI; restart the worker.
- `budget_remaining: 0`: not a failure. Wait for the UTC-day reset or raise the budget with the
  stop plus ensure procedure in sections 1 and 2.
- Repeated `candidate_ingestion.poll_failed`: the poll activity retries three times and the loop
  continues; check the source service and worker logs. No restart is needed.
- `candidate_ingestion.selection_failed`: check `ANTHROPIC_API_KEY` and quota. The failed batch is
  marked processed by design, so new stories continue on later polls.
- Search-attribute or inbox-unavailable errors: follow the
  [search-attributes runbook](search-attributes.md). Review decisions on existing candidates are
  unaffected.

Logs are structured `candidate_ingestion.*` events. Dev worker output is the `just dev` terminal
(or its redirected file); prod is:

```sh
env -i PATH="$PATH" HOME="$HOME" docker compose -f infra/compose.prod.yaml --env-file .env.prod \
  logs -f worker
```

Filter with `grep candidate_ingestion`. The events carry counts, ids, error type names and no
source text.

## 4. Replay and continue-as-new upgrades

Rules for changing the workflow:

- Add input and state fields only with defaults; 049's `candidate_budget`, `budget_day` and
  `candidates_started` all have defaults, so 048-era histories replay.
- Keep activity names and loop order unchanged.
- Bump `SOURCE_INGESTION_WORKFLOW_ID_PREFIX` to `source-ingestion-v2-` for an incompatible loop
  change. That has not been done in 049.

Continue-as-new is automatic after `max_polls_per_run` polls (default 50) or when the server
suggests it, and carries the cursor, processed ids, counters and the budget day/count. No operator
action is needed. The new run keeps the same day's `candidates_started`.

If a deploy produces a non-determinism `WorkflowTaskFailed`:

1. Roll the worker back first; the workflow recovers with the matching code.
2. Otherwise stop or terminate the v1 chain and `just ingest` a new one. Terminating loses the
   cursor, processed ids and budget-day state, so stories may be re-evaluated; deterministic
   candidate ids still prevent duplicate inbox rows.

Diagnose with:

```sh
dev-temporal workflow show --workflow-id source-ingestion-v1-hn
```

## 5. Two-source local demo

In `.env`:

```sh
SOURCE_ENABLED=hn,fake
SOURCE_CANDIDATE_BUDGET=5
```

Then:

```sh
just dev      # terminal 1: stack and worker
just ingest   # terminal 2: ensure both source workflows
just web      # terminal 3: http://127.0.0.1:8000/candidates
```

Both source workflows appear (`source-ingestion-v1-hn` and `source-ingestion-v1-fake`), the inbox
labels fake stories `Test source`, and Approve/Reject behave exactly as for HN. The fake source has
two fixed stories and goes quiet once they are processed; duplicate polls can never create a second
inbox row. The demo uses the live LLM selector, so it may select zero fake stories. Even when it
selects none, the `ingestion_state` query and the `poll`/`selection` events show the fake source
polled. The deterministic proof is the committed `start_local` two-source test; do not treat an
empty demo inbox as a source failure.
