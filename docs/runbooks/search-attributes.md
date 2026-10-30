# Search attributes runbook

The candidate inbox reads two candidate keyword attributes, and the shared story list metadata uses
seven more. Both workflow types write them:

Candidate-specific:

- `CandidateStatus` (`awaiting_review`, `approved`, `rejected`, `expired`)
- `CandidateSource` (`hn`, `fake`)

Shared story metadata (candidate and generation rows):

- `StoryStatus` (the internal candidate or story state, never the five UI badges)
- `StoryTitle` (candidate title)
- `StorySource` (`hn`, `fake`)
- `StoryCandidateId` (the candidate workflow id)
- `StoryCreatedAt` (`Datetime`, the original candidate detection time, inherited by the story)
- `StoryUpdatedAt` (`Datetime`, every transition)
- `StoryStatusDetail` (optional sanitized terminal reason, at most 200 characters)

`SourceIngestionWorkflow` and `TemporalCandidateGateway` set all nine on every candidate they start,
and `StoryCandidateWorkflow` upserts the terminal candidate status plus the shared metadata when it
ends. `StoryGenerationWorkflow` writes the shared metadata at its child start and every transition.
The pending inbox is the visibility query `WorkflowType = 'StoryCandidateWorkflow' AND
CandidateStatus = 'awaiting_review'`; tasks 054-055 query by `StoryCandidateId` and
`StoryCreatedAt`. If any attribute is missing, candidate or story starts fail and the web pages show
their unavailable state. Review decisions on already-running candidates are unaffected.

The worker registers all nine attributes at startup through
`temporal_client.ensure_story_search_attributes(client)`; an `ALREADY_EXISTS` response is ignored, so
the normal path is simply to keep the worker running after the Temporal server is up.
`ensure_candidate_search_attributes` remains as a thin compatibility wrapper.

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
dev-temporal operator search-attribute create --name StoryStatus --type Keyword
dev-temporal operator search-attribute create --name StoryTitle --type Keyword
dev-temporal operator search-attribute create --name StorySource --type Keyword
dev-temporal operator search-attribute create --name StoryCandidateId --type Keyword
dev-temporal operator search-attribute create --name StoryCreatedAt --type Datetime
dev-temporal operator search-attribute create --name StoryUpdatedAt --type Datetime
dev-temporal operator search-attribute create --name StoryStatusDetail --type Keyword
```

Use `prod-temporal` in prod. `ALREADY_EXISTS` means the attribute is already there; that is safe.
The operator API accepts the batch even when some names already exist, so a stack with only the two
candidate attributes gains the seven story attributes on the next worker start.

If the worker cannot start because a candidate start failed on a missing attribute, restart the
worker after registering them; the worker is the normal registration path and will not crash for
an existing attribute.

## 2. Verify

```sh
dev-temporal operator search-attribute list
```

Expect all nine names, with `Keyword` for the status/title/source/candidate-id/detail attributes and
`Datetime` for the two timestamps:

```
CandidateSource     Keyword
CandidateStatus     Keyword
StoryCandidateId    Keyword
StoryCreatedAt      Datetime
StorySource         Keyword
StoryStatus         Keyword
StoryStatusDetail   Keyword
StoryTitle          Keyword
StoryUpdatedAt      Datetime
```

## 3. Dev is in memory

The dev server (`temporalio/temporal:1.9.1` `start-dev`) keeps everything in memory, so the
attributes disappear with `just down`. The worker recreates them on the next `just dev`; you do not
need to run the create commands by hand for dev unless you are repairing a running stack.

## 4. Tests

Tests pre-register the attributes and never call the operator RPC in the time-skipping server:

```python
WorkflowEnvironment.start_local(
    data_converter=pydantic_data_converter,
    search_attributes=[
        CANDIDATE_STATUS_ATTRIBUTE,
        CANDIDATE_SOURCE_ATTRIBUTE,
        STORY_STATUS_ATTRIBUTE,
        STORY_TITLE_ATTRIBUTE,
        STORY_SOURCE_ATTRIBUTE,
        STORY_CANDIDATE_ID_ATTRIBUTE,
        STORY_CREATED_AT_ATTRIBUTE,
        STORY_UPDATED_AT_ATTRIBUTE,
        STORY_STATUS_DETAIL_ATTRIBUTE,
    ],
)
```

`ensure_story_search_attributes` is still called after connecting, exactly as the worker does;
`ensure_candidate_search_attributes` is a compatibility wrapper that registers the same set.
