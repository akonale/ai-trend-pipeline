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

Approval follows the last column. Numbers continue from [history.md](history.md); 024 and 030-033
were dropped and are not reused.

### UI upgrade: neo-brutalist flow

**Goal:** make the web app feel like a guided flow, not a form page: **1 Pick topic → 2 Adjust
style → 3 Review → 4 Done**, in a neo-brutalist look. No change to what the product does, except
the two small backend additions in 039 and 040.

**Read first:** [architecture/README.md](architecture/README.md) and the "Web app" section of
[architecture/tech.md](architecture/tech.md). Today there are four pages: `/` (topic box,
per-post options, chips), `/runs/{id}` (status, review, approve/reject), `/settings` (style
profile), `/health`. Templates are in `web/templates/`, the only stylesheet is `web/static/app.css`.

**Design direction (applies to 036-041):**
- Neo-brutalism: thick near-black borders (about 3px), hard offset shadows with no blur, flat
  saturated colour blocks (one bold accent plus two supporting colours; the planner picks the
  ground and palette with the `frontend-design` skill and must not reuse the current cream and
  terracotta look), oversized heavy headings, chunky buttons that "press" (shadow collapses) on active,
  square or barely rounded corners, visible grid and generous spacing. Playful, not messy.
- Keep light and dark themes (`prefers-color-scheme`), driven by CSS custom tokens in `:root`.
- Accessibility is a hard requirement: text contrast at least 4.5:1, borders, focus rings and
  controls at least 3:1, a thick visible focus outline, `prefers-reduced-motion` respected, works
  at 360px with no horizontal scroll, keyboard-only use works.
- No JavaScript framework and no build step; htmx and plain CSS only. No CDN or external assets.
  Fonts: system stacks, or one vendored open-licence font file committed under `web/static/` with
  its licence text, only if the planner shows a clear gain.
- Templates stay logic-free (loops and ifs only); all labels, step states and URLs come from
  `web/view_models.py`.

**Design skill:** the planner and builder agents load the `frontend-design` skill (the `skills:`
field in `.claude/agents/planner.md` and `builder.md`) and use it on UI tasks. The planner writes
the design decisions into each spec; the task row and the constraints above win over the skill's
defaults.

**Layout decision:** a top **header** (brand, main nav, signed-in user) plus the **stepper**
beneath it, and a slim **footer**. No sidebar for now: the only thing it would hold is a list of
recent runs, which does not exist (task 024 was dropped), and it would cost width on phones.
Revisit when a runs list exists.

**Order:** 036 → 037 → 038 → 039 → 040 → 041. Each task must keep existing route tests passing
(change them only where visible text changes) and update `docs/architecture/tech.md` where it
describes templates, routes or design.

| # | Task | Done when | Your approval? |
| --- | --- | --- | --- |
| 036 | Neo-brutalist design system: rewrite the tokens and components in `web/static/app.css` (colour, type scale, spacing, border width, hard shadows, buttons with a pressed state, inputs, selects, textareas, cards, chips, badges, alerts, tables, chart frame, focus ring; light and dark). Class names only, no layout changes. The human approves the look (palette, type, example screenshots in the spec) before building | Home, run, settings, health and not-found pages all use the new look; `tests/test_web*.py` pass unchanged; every token pair used for text, borders and focus meets WCAG AA (a small script or test checks the pairs and is kept in the repo); screenshots at 360px and 1280px, light and dark, show no horizontal overflow | no |
| 037 | App shell: shared header (brand, nav "New chart" and "Your style", signed-in email chip) and slim footer (product tagline, a "dev" tag when `AUTH_MODE=local`), built from a `shell` view model; add an optional-user dependency so GET pages can show the email without ever returning 401 (POST routes keep `current_user`); nav wraps cleanly on phones with no JavaScript | Every page renders the same header and footer; the email shows in cloudflare mode when the header is present and the dev user in local mode; GET routes never return 401 when the header is missing; route tests cover both modes; no sideways scroll at 360px | no |
| 038 | Flow steps 1 and 2 plus the stepper: a stepper component (1 Topic, 2 Style, 3 Review, 4 Done with done, current, upcoming states from the view model) on every flow page; step 1 at `/` picks the topic (box, example chips, Next); step 2 at `/new/style?topic=...` holds the per-chart options (chart type, time range, countries, caption tone, instructions) and a read-only summary of the saved style with a link to edit defaults; Generate posts to the existing `POST /runs` (contract unchanged) | Step 2 without a topic redirects to step 1; typed values survive Back and validation errors; the stepper shows the right state on step 1, step 2 and the run page; existing `POST /runs` route tests pass unchanged; works at 360px | no |
| 039 | Per-chart style overrides: extend `PostOptions` with palette, font, hashtags and signature (precedence per-chart option, then saved profile, then default; applied in code as today, prompts unchanged); step 2 gets an editable style panel prefilled from the saved profile and a "Save as my default" checkbox that saves through `StyleService` with the same validation as `/settings`. New fields optional with defaults so old runs replay | Changing palette or font in step 2 changes that chart only; with the checkbox ticked the profile is saved; blank fields fall back to the profile; invalid values return 400 with field messages and keep what was typed; a time-skipping workflow test replays an old payload without the new fields; the daily cap and the style-store 503 behaviour are unchanged | no |
| 040 | Review and Done steps: restyle the run page as step 3 (generating: progress that shows the current stage; review: chart card, caption, critique, options used, Approve and Reject buttons) and step 4 (approved: success state with a link to the Bluesky post; rejected and expired: clear end states with a "Make another chart" button). To link the post, add an optional `post_uri` to `ReviewState` (set after posting; the view model turns the `at://` URI into a `bsky.app` profile and post link); polling and the decision flow are unchanged | An approved run shows the posted link; rejected and expired runs show their end state; runs without `post_uri` still render; existing run-page tests pass (updated only where visible text changed); a time-skipping workflow test covers the new state field | no |
| 041 | UI QA and docs: screenshot pass (360px, 768px, 1280px, light and dark) over every page and state (home, step 2, generating, review, approved, rejected, expired, settings, not-found, store unavailable), a keyboard-only walk-through, fixes for what it finds, removal of unused CSS, and updates to `docs/architecture/tech.md` (templates, routes, design tokens), `README.md` and `docs/history.md` | No horizontal overflow in any screenshot; focus is visible everywhere; no unused old-style rules remain; the architecture and history docs match the new UI | no |

### Real-time story candidates

**Goal:** continuously turn source events into a small, reviewable inbox of story candidates. HN is
the first real connector; the source contract must allow Google Trends, RSS, or another source to be
added later without changing the candidate workflow or UI.

**Done when:** an enabled source polls near real time, a bounded batch of new events is evaluated by
an LLM editorial selector, only selected stories appear in a polished but minimal web inbox, and
Approve/Reject decisions are stored entirely in Temporal. Within tasks 042-049, approval means only
"this candidate is approved"; it does not yet generate a story, media, or chart, or publish anything.
Tasks 050-056 below add the subsequent story-generation handoff.

**Order:** 042 → 043 → 044 → 045 → 046 → 047 → 048 → 049. Tasks 042 and 043 may be built in
parallel, but 043 must not be wired into a workflow until 044 and 048. The UI tasks are sequential:
045 builds the inbox, 046 adds candidate detail and decisions, and 047 integrates and polishes them.

**Read first:** [architecture/README.md](architecture/README.md),
[architecture/business.md](architecture/business.md), the Temporal and web sections of
[architecture/tech.md](architecture/tech.md), `src/ai_trend_pipeline/hn.py`,
`src/ai_trend_pipeline/topic.py`, `src/ai_trend_pipeline/daily_chart.py`,
`src/ai_trend_pipeline/services/temporal_gateway.py`, `src/ai_trend_pipeline/worker.py`,
`src/ai_trend_pipeline/config.py`, `src/ai_trend_pipeline/web/routes/`,
`src/ai_trend_pipeline/web/view_models.py`, and `src/ai_trend_pipeline/web/templates/`.
Each planner lists only the subset its task needs.

**Target flow:**

```
SourceIngestionWorkflow (one long-running workflow per enabled source)
  → poll source activity
  → normalize and deterministically prefilter a bounded batch
  → LLM select_story_candidates activity
  → start StoryCandidateWorkflow only for selected events

StoryCandidateWorkflow (one per selected story)
  → awaiting_review → approved / rejected / expired

Web → Temporal visibility (list) → workflow query (detail) → signal (decision)
```

**Rules for 042-049:**
- Temporal is the candidate system of record. Do not add a candidates, source-events, cursors, or
  inbox table to application Postgres. Source cursor and bounded processed-ID state live in the
  source workflow; candidate state lives in the candidate workflow.
- All HTTP, LLM, Temporal-client, and visibility I/O stays in activities or services, never in
  deterministic workflow code. All activity and workflow boundaries use Pydantic models.
- The LLM performs editorial selection on a batch, not one call per raw event. It may return zero
  selections. It never controls cursors, workflow IDs, review state, or publishing.
- Candidate workflow IDs are deterministic from `source + external_id`, versioned as
  `candidate-v1-<hash>`. Expected already-started conflicts suppress duplicates. Do not attempt
  semantic cross-source deduplication in this milestone.
- Keep raw payloads compact and normalized before they enter workflow history. Source titles and
  other text are untrusted data, not instructions. Accept only `http`/`https` source URLs.
- Source credentials and model settings are read only in `config.py`; secrets never enter workflow
  inputs, history, logs, or templates.
- Leave `DailyChartWorkflow`, its HN activity, chart rendering, Bluesky publishing, and existing
  chart routes behaviourally unchanged. New workflow types need their own worker registration,
  tests, timeouts, and replay-safe fields.
- The candidate UI gets dedicated inbox and detail pages plus clear navigation, responsive states,
  and accessible decision feedback, but remains editorial rather than analytical. No charts,
  analytics dashboard, media preview, publishing controls, complex filters, sidebar, or JavaScript
  build step.
- HTTP and LLM tests use recorded fixtures. Workflow tests use the time-skipping environment. Run
  Ruff, strict Pyright, and the full test suite before each task is marked verified.

| # | Task | Done when | Your approval? |
| --- | --- | --- | --- |
| 042 | Source contracts and HN polling adapter: add a `sources` package with frozen, bounded Pydantic models `SourceEvent`, `SourcePollInput`, and `SourcePollOutput`; a static source registry; a fake connector for tests; and a new HN polling activity that normalizes live stories into source events. Reuse the existing HN HTTP implementation where sensible, but do not change or remove `HNActivities.fetch_hn_top_stories`. Include source name, external ID, title, optional URL, published/observed timestamps, and source metrics; keep optional source-specific data small. Add non-secret source enablement, batch-size, and timeout settings through `config.py` | Contract tests run the same fixture cases against fake and HN connectors; dead/deleted/title-less stories are excluded; duplicate IDs within one response are collapsed; source payloads and batch sizes are bounded; invalid URL schemes are rejected; recorded HN fixtures require no network; the existing chart workflow and HN tests pass unchanged | no |
| 043 | Batch LLM editorial selection: add pure deterministic prefiltering and a `select_story_candidates` activity following the structured-tool pattern in `topic.py`. Its input contains a bounded batch of normalized events, optional validated editorial instructions, and `max_candidates`; its output contains only `external_id`, `summary`, `proposed_angle`, and `selection_reason`. The selector compares events, may select none, and is asked to avoid merely choosing the highest-engagement items. Validate selected IDs against the input and enforce the selection cap in code. Keep the activity separate from all source connectors and from review-state management | Fixtures cover zero, one, and several selections plus overlapping stories; unknown IDs, malformed tool output, over-cap selections, missing events, and prompt-instruction text embedded in a source title cannot create an invalid candidate; retries and non-retryable validation failures follow existing LLM activity policy; tests prove that no candidate workflow is started by the activity itself | no |
| 044 | `StoryCandidateWorkflow` and Temporal gateway: add compact input/result/review models, deterministic candidate-ID derivation, a `review_state` query, Approve/Reject signals, a configurable review timeout, and states `awaiting_review → approved / rejected / expired`. Index `CandidateStatus` and `CandidateSource` through Temporal visibility. Extend the gateway/service boundary to start a candidate idempotently, list pending candidates with pagination, read an open candidate, and signal a decision. Register the workflow with the worker; no source polling or UI in this task | Time-skipping tests cover approval, rejection, expiration, invalid signals, and first-terminal-decision-wins races; starting the same source/external ID twice cannot create two candidates; visibility attributes change with state; compact payloads survive serialization; old `DailyChartWorkflow` replay tests pass; the gateway maps missing/already-closed candidates to typed service errors | no |
| 045 | Candidate inbox page: add `GET /candidates`, the list portion of `CandidateService`, inbox view models, and a new Jinja template using the existing app shell and CSS tokens. Show a clear "Story candidates" heading and a compact list of pending candidates. Each row/card contains the title, source badge, relative detection time, one-line proposed angle, and a whole-row/detail link. Add server-side pagination, a friendly empty state, and a recoverable Temporal-unavailable state. Do not add detail/decision routes in this task | Fake-gateway route tests cover multiple candidates, long titles and angles, pagination, empty state, service failure, missing optional fields, and escaping untrusted source text; the page renders useful content without JavaScript; existing routes remain unchanged; 360px and 1280px screenshots show scannable rows, no horizontal overflow, and no clipped actions | no |
| 046 | Candidate detail and decision page: add `GET /candidates/{id}` and POST Approve/Reject routes, the corresponding service methods and view models, and a dedicated detail template. Present the title and safe source link first, then summary, proposed angle, selection reason, and a small definition list or chip group for relevant source metrics. Use a simple stacked layout on mobile and an optional two-column reading/actions layout on desktop. Approve and Reject use explicit, visually distinct buttons and post/redirect/get back to the inbox with a success message; missing or already-decided candidates produce a clear not-available page or inbox notice | Route tests cover detail, both decisions, missing candidate, expired/already-closed workflow, repeated decision, invalid decision, source-link safety, and success/error feedback; untrusted HTML is escaped; keyboard focus lands somewhere useful after redirect; no decision can invoke chart generation or publishing; detail and end-state screenshots pass at 360px and 1280px | no |
| 047 | Candidate UI navigation and visual polish: integrate Candidates into the shared header as the primary destination with an active state, retain New chart and Your style, and add a small pending-count badge only when it can be obtained without an extra per-row workflow query. Add reusable candidate-specific CSS for list rows/cards, source badges, metric chips, detail sections, decision buttons, empty/error states, and flash messages using existing design tokens. Add a Back to candidates link and sensible page titles. Keep normal page loads and POST redirects; no auto-refresh, framework, or build step. Update web architecture docs and README screenshots or route descriptions | Every page has working navigation and the correct active state; inbox/detail/empty/error/success screens share one coherent visual language; light/dark contrast, focus order, keyboard operation, reduced motion, and 360px/768px/1280px layouts pass; no unused duplicate candidate CSS remains; existing chart route tests pass and the planner documents any intentional visible-text changes | no |
| 048 | Continuous HN ingestion: add one long-running `SourceIngestionWorkflow` per enabled source. It polls through the 042 activity, keeps cursor and bounded processed-ID state, prefilters new events, calls the 043 batch selector, and starts 044 candidate workflows only for selected events. Sleep with Temporal timers, use workflow time, and continue-as-new after a bounded number of polls or before history grows too large. Expected candidate already-started conflicts are ignored. Add worker startup or an explicit idempotent operator command to ensure configured source workflows exist; document the chosen mechanism in the task spec | Time-skipping tests cover multiple polls, empty selections, repeated top-story IDs, no re-evaluation of bounded processed events, duplicate candidate suppression, selection caps, activity retry, cursor/state preservation across continue-as-new, and shutdown/cancellation; no I/O or wall-clock calls occur in workflow code; HN fixtures produce an end-to-end pending candidate visible through the 045-047 UI service boundary without live HTTP or LLM calls; existing chart runs are not counted as or confused with candidate workflows | no |
| 049 | Candidate-inbox hardening and docs: add a per-source candidate budget and structured operational logs for polls, normalized events, LLM selections, duplicates, candidate starts, and failures without logging secrets or full source text. Add runbooks for registering Temporal search attributes, enabling/disabling sources, ensuring ingestion workflows, recovering a failed source, and handling replay/continue-as-new upgrades. Prove pluggability by running the fake second source through the complete poll → select → inbox → review flow with unchanged candidate workflow and UI. Update `README.md`, architecture docs, and history; remove the outdated statement that nothing starts runs automatically only where candidate ingestion is concerned | The full local stack demonstrates two sources feeding the same minimal inbox, source disablement stops new polling without affecting pending reviews, budgets and failure logs are tested, duplicate polls create no duplicate inbox rows, the UI remains usable when only some sources fail, Ruff/Pyright/full pytest pass, and a fresh operator can configure and operate the feature using only the committed docs. Google Trends remains a follow-up connector until its API/provider access is chosen | no |

### Evidence-backed story generation and status tracking

**Goal:** turn an approved candidate into a review-ready, evidence-backed textual story, while giving
the user one minimal page to track candidates and stories through every stage. This milestone does
not add infographics, animation, video, chart generation, or publishing.

**Done when:** approving a candidate starts exactly one `StoryGenerationWorkflow`; the UI groups
internal progress into five primary statuses; a second human approval produces a `ready`
StoryPackage; and every user-facing story list is a rolling 48-hour view. Unsupported and failed
stories stop with useful explanations. All story state and list metadata remain in Temporal.

**Order:** 050 → 051 → 052 → 053 → 054 → 055 → 056. Tasks 050-052 build standalone activities and
contracts without changing candidate approval behaviour. Task 053 enables the automatic handoff.
Tasks 054 and 055 add status tracking and final story review; 056 verifies the complete flow.

**Target flow:**

```
StoryCandidateWorkflow
  → approved
  → start story-v1-<candidate-hash> without waiting for completion
  → candidate result includes the story workflow ID

StoryGenerationWorkflow
  → researching: gather bounded source context and supporting data
  → analyzing: calculate facts and preserve evidence lineage
  → writing: draft a structured story from context and facts
  → checking: verify claims and allow at most one bounded revision
  → review: wait for final human Approve/Reject
  → ready / rejected

Alternative terminal states: unsupported / failed / expired
```

**User-facing status contract:** Temporal keeps the detailed internal states above, but list and
detail pages expose only these five primary badges:

| UI status | Internal states and meaning |
| --- | --- |
| `pending` | `awaiting_review`: the candidate awaits the first human decision |
| `writing` | `approved`, queued, `researching`, `analyzing`, `writing`, and `checking`; candidate approval moves directly into this status |
| `review` | The generated story awaits final human approval |
| `ready` | The exact StoryPackage is approved; nothing is published |
| `closed` | `rejected`, `unsupported`, `failed`, or `expired`; the detail page gives the sanitized reason |

`approved` is an internal transition and audit event, not a separate UI badge. The detail page may
show one short progress explanation such as "Checking supporting facts," but it must not expose the
full internal state machine as additional badges or a lifecycle diagram.

**48-hour UI retention:**
- `/stories`, `/candidates`, status filters, pagination, and pending counts include only stories
  whose original candidate detection time is less than 48 hours old. This applies to active and
  terminal stories alike; at the 48-hour boundary they vanish from the UI.
- Add bounded `StoryCreatedAt` visibility metadata from the original candidate detection time. The
  generation workflow inherits it unchanged, so the candidate-to-story handoff never resets the
  clock. `StoryUpdatedAt` remains sorting/progress metadata and is not used for retention.
- Candidate and final-review deadlines must not extend beyond the story's 48-hour UI cutoff, so an
  item cannot remain awaiting a hidden decision. The web service computes the cutoff; workflows do
  not need cleanup timers for UI retention.
- This is presentation retention, not deletion. Do not delete workflow histories or add a cleanup
  database/workflow; Temporal history remains subject to the namespace's normal retention policy.

**Rules for 050-056:**
- Temporal remains the system of record; do not add a story, research, fact, draft, or status table.
  Open workflow state comes from queries, closed results come from workflow results, and list rows
  come from Temporal visibility.
- Add shared, bounded visibility attributes to candidate and story workflows: `StoryStatus`,
  `StoryTitle`, `StorySource`, `StoryCandidateId`, `StoryCreatedAt`, `StoryUpdatedAt`, and an
  optional short sanitized `StoryStatusDetail`. Full story text and raw evidence stay out of search
  attributes.
- The status service de-duplicates the candidate-to-story handoff by `StoryCandidateId`, preferring
  the generation workflow once it exists. An approved candidate maps to `writing` while generation
  starts; a generation-start failure maps to `closed` with a sanitized failure reason.
- New candidate workflow fields are optional with defaults. Version-gate new commands and visibility
  upserts where required so histories created by tasks 042-049 still replay.
- A story workflow ID is deterministic and versioned as `story-v1-<candidate-hash>`. The candidate
  workflow starts it once after approval and does not wait for it; duplicate approvals cannot create
  duplicate stories.
- Research and writing I/O is activity-only. Bound fetched bytes, context length, evidence items,
  facts, draft length, redirects, and processing time. Validate HTTP URLs, redirects, content types,
  and destination networks so source links cannot reach local or internal services.
- Facts used for quantitative claims are calculated in deterministic code and carry stable fact IDs,
  source references, observation dates, and calculation lineage. The LLM may select and explain
  facts but cannot create new fact IDs or unsupported numerical claims.
- The story writer returns a versioned StoryPackage containing headline, summary, body, key points
  with fact IDs, source links, caveats, and optional post text. Validate all IDs and URLs in code.
  Source and research text remain untrusted data, never instructions.
- Evidence-insufficient stories end as `unsupported` before writing. Critique failures end as
  `failed` after at most one revision; do not loop indefinitely. Final Approve/Reject is separate
  from candidate approval, and only the first final decision wins.
- UI status labels and colours come from one view-model mapping over the five primary statuses, not
  workflow-specific conditionals scattered through templates. Show sanitized status detail, never
  stack traces, prompts, secrets, or raw LLM responses. No status automatically means published.
- HTTP and LLM tests use recorded fixtures. Workflow tests use time-skipping and replay checks.
  Existing `DailyChartWorkflow`, candidate histories, chart rendering, and publishing stay unchanged.

| # | Task | Done when | Your approval? |
| --- | --- | --- | --- |
| 050 | Story research and evidence contracts: add bounded Pydantic models for StoryInput, StoryContext, EvidenceSource, ResearchInput/Output, and research failures. Implement activities that gather the original source context, optionally retrieve additional approved context, match supporting data, and return explicit `supported` / `unsupported` outcomes. Reuse OWID access where it fits, but do not force every candidate into the catalog. Add safe HTTP fetching and compact normalization for article/context text; keep credentials and model/provider configuration in `config.py` | Fixtures cover a supported story, missing article, irrelevant dataset, insufficient evidence, malformed content, oversized response, unsafe URL/redirect, and source prompt injection; internal-network requests are blocked; outputs stay within limits; no workflow behaviour changes yet; Ruff/Pyright/tests pass | no |
| 051 | Deterministic fact foundation: add versioned Fact and FactSet models plus a pure analysis library and activity that calculate changes, comparisons, first/latest/min/max, rankings, and relevant outliers from the 050 evidence. Every fact includes a stable ID, source references, observation dates, calculation lineage, and caveats. Omit unsupported calculations rather than guessing. Add compact per-fact display values for later writing and critique | Unit tests verify every calculation and lineage field against fixed datasets; missing data, duplicate entities, partial series, and empty results are handled; serialization is bounded and deterministic; an LLM-free fixture produces identical FactSets across runs; unsupported stories cannot produce fabricated fallback facts | no |
| 052 | Story drafting and critique activities: define the versioned StoryPackage, DraftInput/Output, CritiqueInput/Output, and bounded critique result. Implement a structured-tool LLM writer that receives only the approved candidate, compact research context, and FactSet, then returns headline, summary, body, key points with fact IDs, source links, caveats, and optional post text. Implement a separate checker for unsupported claims, numerical fidelity, headline overstatement, missing caveats, and source/date fidelity. Validate all returned fact IDs and URLs in code | Recorded LLM fixtures cover a clean draft, one useful critique, unsupported numbers, invented fact IDs, malformed output, missing citations, and injected instructions in research text; invalid outputs fail closed; the activities do not start workflows or publish; retry and non-retryable behaviour matches existing LLM policy | no |
| 053 | `StoryGenerationWorkflow` and approval handoff: orchestrate 050-052 through `researching → analyzing → writing → checking → review`, with one bounded revision and terminal `ready / rejected / unsupported / failed / expired` handling. Add story review queries and final Approve/Reject signals, activity retry policies, worker registration, and deterministic story IDs. After candidate approval, start the story workflow once without waiting and include its ID in the candidate result. Add the shared visibility attributes and five-status mapping to both workflow types, inherit the original detection timestamp, cap review deadlines at the 48-hour cutoff, and preserve old candidate replay | Time-skipping tests cover every internal state and all five UI mappings, unsupported before writing, critique revision, exhausted failure, final approve/reject, review expiry at the UI cutoff, first-decision-wins, duplicate candidate approval, duplicate story start, and handoff de-duplication metadata; old candidate and chart histories replay; exactly one story starts per approved candidate; nothing publishes | no |
| 054 | Unified `/stories` status page with 48-hour retention: add a new page and `StoryStatusService` that lists candidate and generation workflows through shared visibility attributes. Each row shows title, source, one of the five primary status badges, an optional short status detail, updated time, and a detail link. Provide simple filters for All, Pending, Writing, Review, Ready, and Closed. Make Stories the primary nav destination; keep `/candidates` as the pending-action inbox. Apply the same rolling 48-hour cutoff to both pages, all filters, pagination, and pending counts. Add empty/error states and a light htmx fragment refresh only while visible rows are active | Fake-clock/gateway tests cover all internal-to-UI mappings, handoff de-duplication, generation-start failure, filters, pagination, stale metadata, unsafe text, and service failure; an item is visible at 47:59:59 and absent at 48:00:00 for active and terminal states; generation never resets `StoryCreatedAt`; old items leave pending counts; no per-row query renders the list; active polling stops for terminal-only pages; responsive light/dark screenshots pass | no |
| 055 | Story detail and final review UI: add `/stories/{id}` for candidate or generation workflows. While active, show the primary status and one short progress explanation; at `review`, render the StoryPackage with headline, summary, body, key points, clickable fact/evidence references, caveats, and final Approve/Reject actions; at `ready`, show the approved package read-only. Show one `closed` badge with clear rejected, unsupported, failed, or expired reason text. Open workflows use queries and closed workflows use typed results through the service; no arbitrary history parsing or detailed lifecycle graphic in templates | Route tests cover all five primary states and each closed reason, missing, already-decided, repeated decision, malformed result, source-link safety, and the 48-hour cutoff; final approval does not publish; active detail updates via htmx without a JavaScript build; keyboard/focus behaviour, long body text, citations, mobile/desktop layouts, and existing candidate/chart tests pass | no |
| 056 | Story-generation end-to-end hardening and docs: exercise HN candidate → approval → internal research/analysis/writing/checking → review → ready using recorded HTTP/LLM fixtures and the real Temporal service/gateway/UI boundaries. Verify that the UI shows only Pending, Writing, Review, Ready, or Closed throughout. Add bounded budgets, sanitized operational logs, failure-recovery runbooks, search-attribute setup, five-status semantics, and 48-hour UI retention to architecture docs and README. Document the two human decisions explicitly: candidate approval starts generation; story approval marks the package ready and never publishes | The full local stack demonstrates success, unsupported, failed-after-retry, critique-revision, candidate rejection, story rejection, and review expiry; no duplicate rows or story workflows appear across the handoff; terminal stories remain visible only until 48 hours after original detection; hidden items leave no pending counts or filters; no histories are deleted; old replay suites, Ruff, strict Pyright, and full pytest pass; a fresh operator can run and troubleshoot the feature from committed docs alone | no |

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
