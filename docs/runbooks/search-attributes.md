# Search attributes runbook

The candidate inbox reads two Temporal visibility keyword search attributes:

- `CandidateStatus` (`awaiting_review`, `approved`, `rejected`, `expired`)
- `CandidateSource` (`hn`, `fake`)

`SourceIngestionWorkflow` sets both on every candidate it starts, and `StoryCandidateWorkflow`
upserts the final status when it ends. The pending inbox is the visibility query
`WorkflowType = 'StoryCandidateWorkflow' AND CandidateStatus = 'awaiting_review'`. If either
attribute is missing, candidate starts fail and the web inbox shows its unavailable state. Review
decisions on already-running candidates are unaffected.

The worker registers both attributes at startup through
`temporal_client.ensure_candidate_search_attributes(client)`; an `ALREADY_EXISTS` response is
ignored, so the normal path is simply to keep the worker running after the Temporal server is up.

## CLI wrappers

Run every command from the repo root. These wrappers are defined once here and reused by the
[ingestion runbook](ingestion.md):

```sh
# dev (Temporal container from `just dev`; the worker runs on the host)
dev-temporal()  { docker compose -f infra/compose.dev.yaml exec temporal temporal "$@"; }
# prod
prod-temporal() { env -i PATH="$PATH" HOME="$HOME" docker compose -f infra/compose.prod.yaml \
  --env-file .env.prod exec temporal temporal "$@"; }
```

If the server image has no `temporal` binary, install the Temporal CLI on the host and pass
`--address <host>:7233`; the commands are otherwise identical.

## 1. Create the attributes

Run this once against a server that does not have them yet (a fresh prod stack, or a dev server
whose in-memory state was cleared):

```sh
dev-temporal operator search-attribute create --name CandidateStatus --type Keyword
dev-temporal operator search-attribute create --name CandidateSource --type Keyword
```

Use `prod-temporal` in prod. `ALREADY_EXISTS` means the attribute is already there; that is safe.

If the worker cannot start because a candidate start failed on a missing attribute, restart the
worker after registering them; the worker is the normal registration path and will not crash for
an existing attribute.

## 2. Verify

```sh
dev-temporal operator search-attribute list
```

Expect both names with type `Keyword`:

```
CandidateSource  Keyword
CandidateStatus  Keyword
```

## 3. Dev is in memory

The dev server (`temporalio/temporal:1.9.1` `start-dev`) keeps everything in memory, so both
attributes disappear with `just down`. The worker recreates them on the next `just dev`; you do not
need to run the create commands by hand for dev unless you are repairing a running stack.

## 4. Tests

Tests pre-register the attributes and never call the operator RPC (the time-skipping server has no
operator service):

```python
WorkflowEnvironment.start_local(
    data_converter=pydantic_data_converter,
    search_attributes=[CANDIDATE_STATUS_ATTRIBUTE, CANDIDATE_SOURCE_ATTRIBUTE],
)
```

`ensure_candidate_search_attributes` is still called after connecting, exactly as the worker does.
