# 055: Story detail and final review UI

Status: built

## Goal

Add `GET /stories/{id}` for a candidate or generation workflow: show one primary badge and a short
progress explanation while active, render the full `StoryPackage` with final Approve/Reject at
`review`, render the approved package read-only at `ready`, and render one `closed` badge with a
clear rejected/unsupported/failed/expired reason. Open state comes from workflow queries and closed
state from typed results through a service; templates never parse history or draw a lifecycle.
Active detail refreshes through htmx and everything respects the 48-hour cutoff.

## Design decisions

The `frontend-design` skill was read and its plan -> critique -> build process applied. The brief and
the existing system win: the "graph paper and ink" tokens in `web/static/app.css`, the shared shell,
and the 045-047/054 candidate ledger and proof-sheet language are reused as-is. No new palette,
typeface, lifecycle diagram, analytics, sidebar, JavaScript framework, CDN asset or build step.

**Subject, audience, job.** One editor opening a single completed story package for the final
decision, after already approving the candidate. The job is to read the story closely and decide.
This is a newsroom galley, not a dashboard: headline and prose carry the page, provenance and
citations sit quietly around the text, and the decision is the only chunky control.

**Palette (existing tokens only; light / dark).**

- Page ground: `--color-bg` `#E3ECDF` / `#191C30`; grid `--color-grid` `#D2DFCC` / `#23273F`.
- Reading and rail surface: the page ground for prose; `--color-surface` `#FFFFFF` / `#262A44` only
  for the decision/approved card.
- Headline, body: `--color-text` `#15172B` / `#E3ECDF`; summary, muted meta, citations, caveats:
  `--color-muted` `#4A4F5E` / `#B4BACB`.
- Borders and hard shadows: `--color-border` / `--color-shadow` `#15172B` / `#E3ECDF`.
- Accent links and Approve: `--color-accent` `#2448D8` / `#8DAEFF`; on accent:
  `--color-accent-contrast` `#FFFFFF` / `#15172B`; focus `--color-focus` `#2448D8` / `#FFC93C`.
- Reject: existing `.btn--danger-outline` (`--color-danger` `#FF9A9A`, `--color-on-fill` `#15172B`).
- Status stamps: the existing `.badge--*` fills — Pending `--color-neutral` `#D3D6DE`, Writing
  `--color-info` `#A8C8FF`, Review `--color-warning` `#FFC93C`, Ready `--color-success` `#7DDBA3`,
  Closed `--color-danger` `#FF9A9A`, all with `--color-on-fill` `#15172B`.
- Source badge: `.badge .source-badge`, exactly as the candidate and list pages.

**Type (existing families and scale).** `--font-display` (Arial Black stack) for the package headline
and section headings; `--font-body` for all prose and meta. Headline `--text-2xl` (the existing h1
clamp), 900, line-height 1.05, `max-width: 24ch`. Summary `--text-base`, weight 600, line-height 1.6,
`max-width: 62ch`. Body paragraphs `--text-base`, line-height 1.65, `max-width: 62ch`. Section
headings `--text-lg`, 800, line-height 1.2. Key points `--text-base`, line-height 1.6. Citation
markers, evidence hints, source/meta line `--text-sm`. Caveats `--text-base` but muted. No all-caps,
no mono micro-labels, no numbered section markers, no arrow glyphs, no drop caps.

**Spacing, borders and shadows.** `--space-1` 0.25rem through `--space-7` 4rem, `--border-width`
3px, `--border-thin` 2px, `--radius-sm` 4px, `--radius-pill` 999px, `--shadow-sm`/`--shadow-md` hard
offsets, `--duration-press` 80ms. Long-form sections are separated by `--space-5`; paragraphs by
`--space-4`; key points and caveats by `--space-3`. All new rules use tokens; no raw hex.

**Layout.** One reading column inside the shared `main` (max 44rem). On phones everything stacks. At
`min-width: 48rem` a 17rem utility rail sits to the right only for `review` (decision) and `ready`
(approval metadata); pending, writing and closed stay single-column. The reading column is not
boxed; only the rail is a `.card`.

```
360px                                   1280px (main still 44rem)
+------------------------------+        +--------------------------------------------------+
| Back to stories              |        | Back to stories                                  |
| [Hacker News] Detected 2h ago|        | [Hacker News] Detected 2 hours ago               |
| [Review]                     |        | [Review] Ready for your final review.            |
| Headline of the story        |        | Headline of the story, wrapping                  |
| Summary standfirst.          |        | + reading (1fr) --------------+ + rail 17rem -+ |
| Body paragraph.              |        | | Summary standfirst.          | | Final review | |
| Body paragraph.              |        | | Body paragraph.              | | note         | |
| Key points                   |        | | Key points with [1][2] cites | | [Approve]    | |
| - point with [1][2]          |        | | Evidence links               | | [Reject]     | |
| Evidence                     |        | | Caveats                      | +--------------+ |
| - source link                |        | | Fact references [1] ...      |                  |
| Caveats                      |        | +------------------------------+                  |
| - quiet caveat               |        +--------------------------------------------------+
| + Final review ------------+|
| | [Approve final story]    |||
| | [Reject final story]     |||
| +--------------------------+|
+------------------------------+
```

**Principles.** Long-form reading is the point: a comfortable 62ch measure and 1.65 line-height, not
a card grid. Key points are a clear list with quiet footnote-style citations that jump to the fact
references; evidence links are the clickable external references. Caveats are visible but quiet
(muted text behind a 2px left rule). Exactly one status stamp appears at the top; closed pages replace
the package with one reason block. The single bold element is the decision/approved rail: full-width
approve/reject buttons with the inherited pressed state, and a plain sentence that nothing is
published. Pending/writing show one short progress line, never internal state names.

**Copy (exact strings live in `view_models.py`, never in the template).**

- Back link: `Back to stories` -> `/stories`, above the heading on every state.
- Source line: `<time>` + `Detected 2 hours ago` (reuse `relative_time`); fallback `Unknown source`.
- Progress: Pending `Waiting for the candidate review.`; Writing uses the sanitized status detail
  when present (for example `Starting story generation.`) otherwise `Writing and checking the
  story.`; Review `Ready for your final review.`; Ready `Approved and ready. Nothing was published.`;
  Closed no progress line (the reason block follows).
- Polling hint: `Updating while the story is in progress.`
- Package section labels: `Key points`, `Evidence`, `Fact references`, `Caveats`, `Post text`.
- Decision card: heading `Final review`; note `Approving marks this story ready. Nothing is
  published.`; buttons `Approve final story`, `Reject final story`.
- Approved card: heading `Approved`; body `This is the final package. Nothing was published.`
  plus `Approved by <email>` and `Approved <relative time>` when present.
- Closed reasons: candidate rejected -> heading `This story was rejected`, body `A person rejected
  the candidate. Nothing was published.`; story rejected -> heading `This story was rejected at
  final review`, body `A person rejected the story package. Nothing was published.`; unsupported ->
  heading `This story could not be supported`, body the sanitized status detail or `There was not
  enough evidence to support this story.`; failed -> heading `Story generation failed`, body the
  sanitized status detail or `Story generation failed.`; expired -> heading `This story expired`,
  body `The review window closed before anyone decided. Nothing was published.`; unknown -> heading
  `This story is closed`, body `It is no longer available for review. Nothing was published.`
  A non-empty decision note renders as a quiet `Note: ...` line.
- Detail notices (allow-listed `?decided=`; unknown renders nothing): `approved` -> `Approved: the
  story is ready. Nothing was published.`; `rejected` -> `Rejected: the story is closed. Nothing was
  published.`; `closed` -> `That story was already decided or has expired. Nothing changed.`
- Invalid decision (400, detail re-rendered): `Choose Approve or Reject.`
- Not available, missing (404): heading `This story is not available`, body `It may have been
  removed, or the link may be wrong. The stories page is the best place to start.`
- Not available, past the 48-hour cutoff (410): heading `This story is no longer available`, body
  `Stories leave this page 48 hours after they were first detected. Nothing was published.`
- Not available, malformed typed result (502): heading `This story can't be shown right now`, body
  `Something in the story record could not be read. Nothing was published.`, retry `Try again`.
- Unavailable (503): heading `This story is unavailable`, body `We couldn't reach the story service.
  Nothing is lost.`, retry `Try again`.
- Every not-available page has `Back to stories` -> `/stories`.

**Package rendering.** Headline is the `<h1>`; summary is a standfirst paragraph; body is split into
paragraphs in the view model on blank lines (single newlines collapse to spaces); key points keep
their order; each key point's fact IDs become footnote-style citation links (`[1]`, `[2]`, ...)
deduplicated and numbered by first appearance across all key points, linking to `#story-fact-N`.
The `Fact references` list shows `[N] fact-v1-...` with matching `id="story-fact-N"`. The `Evidence`
list renders each `StorySourceLink` label as a link to its URL only when `safe_source_url` accepts
`http`/`https`; it opens with `target="_blank" rel="noopener noreferrer"` and includes a visually
hidden `(opens in a new tab)`. An unsafe or missing URL renders the label plus a muted
`Link unavailable.` and no anchor. Caveats render as a muted list behind a 2px left rule. Optional
`post_text` renders in a quiet `Post text` section when present. All prose is escaped by Jinja.

**Component notes (add near the candidate detail block in `app.css`; reuse `.candidate-section`,
`.candidate-detail__layout`, `.candidate-actions`, `.candidate-decisions`, `.candidate-flash` and
the global `.badge`/`.btn` rules; tokens only).**

- `.story-detail { margin-top: var(--space-5); }`
- `.story-detail__title { max-width: 24ch; margin-top: var(--space-3); }`
- `.story-detail__status { display: flex; flex-wrap: wrap; align-items: center; gap:
  var(--space-2) var(--space-3); margin: var(--space-3) 0 var(--space-4); color:
  var(--color-muted); font-size: var(--text-sm); }`
- `.story-detail__progress { font-weight: 700; }`
- `.story-summary { max-width: 62ch; margin-bottom: var(--space-5); font-weight: 600;
  line-height: 1.6; }`
- `.story-body p { max-width: 62ch; margin: 0 0 var(--space-4); line-height: 1.65; }`
- `.story-points, .story-evidence, .story-facts, .story-caveats { list-style: none; margin: 0;
  padding: 0; }`
- `.story-points { display: flex; flex-direction: column; gap: var(--space-3); }`
- `.story-points > li { max-width: 62ch; line-height: 1.6; }` (overrides the global `li` max-width)
- `.story-citations { display: inline-flex; gap: var(--space-1); margin-left: var(--space-1); }`
- `.story-citation { color: var(--color-muted); font-size: var(--text-sm); font-weight: 700; }`
- `.story-evidence > li, .story-facts > li, .story-caveats > li { max-width: 62ch; }`
- `.story-evidence > li { margin-bottom: var(--space-2); }`
- `.story-facts { margin-top: var(--space-3); color: var(--color-muted); font-size: var(--text-sm); }`
- `.story-caveats { padding-left: var(--space-3); border-left: var(--border-thin) solid
  var(--color-muted); color: var(--color-muted); }`
- `.story-caveats > li { margin-bottom: var(--space-2); }`
- `.story-detail__layout { margin-top: var(--space-5); }`
- `.story-detail__reading { min-width: 0; }`
- `.story-rail { margin-bottom: 0; }` and `.story-rail h2 { font-size: var(--text-lg); margin-bottom:
  var(--space-2); }`; `.story-rail .candidate-decisions { flex-direction: column; gap:
  var(--space-3); }`; `.story-rail .candidate-decisions .btn { width: 100%; }`
- `.story-closed { max-width: 62ch; margin-top: var(--space-5); }` and `.story-closed h2 { margin:
  0 0 var(--space-2); font-family: var(--font-display); font-size: var(--text-lg); font-weight: 800;
  line-height: 1.2; }`
- `.story-detail__problem { margin-top: var(--space-4); }`
- `@media (min-width: 48rem) { .story-detail__layout { display: grid; grid-template-columns:
  minmax(0, 1fr) 17rem; gap: var(--space-5); align-items: start; } }` (no sticky rail)
- No new transitions or animations, so `prefers-reduced-motion` needs no rule; the global focus
  outline and the existing `.candidate-flash:focus` rule cover the notice. No raw hex.

**Design critique (checked before building).** Rejected the skill's default cream/terracotta and
acid-accent palettes, the SaaS card kit, broadsheet hairline rules, all-caps eyebrows, mono
micro-labels, numbered section markers, arrow glyphs, drop caps, a serif reading face (it would
introduce a third family and break the 045-047/054 language), a lifecycle diagram, a sidebar and an
expiry countdown. The one memorable move is the stamped final-review rail beside a flat galley;
the reading typography and footnote citations carry the rest.

## Service and gateway contract

Keep Temporal as the system of record and add a detail use case in `services/stories.py` (never
imported by workflows or activities). The list path is unchanged. No new table, no history parsing,
and no workflow or model changes.

### Models and errors

- `CANDIDATE_ID_PREFIX = "candidate-v1-"`, `STORY_ID_PREFIX = "story-v1-"`; `MAX_DETAIL_ID_CHARS =
  128`.
- `StoryDetailKind(StrEnum)`: `candidate`, `generation`.
- `story_detail_kind(detail_id: str) -> StoryDetailKind`: strip nothing from the path segment; blank,
  over 128 characters, or any id without one of the two prefixes raises `StoryDetailNotFound`. The
  prefixes are the deterministic, versioned workflow-id prefixes from 044/053.
- `StoryDetail`: `detail_id` (requested id), `workflow_id` (canonical id used for the decision),
  `candidate_id`, `kind`, `title`, `source`, `created_at`, `status: PrimaryStoryStatus`,
  `internal_status: str`, `status_detail: str | None`, `package: StoryPackage | None`,
  `decided_by: str | None`, `decided_at: datetime | None`, `note: str`.
- `DecideStoryRequest` (`extra="forbid"`): `detail_id` (1..128), `decision:
  Literal["approve","reject"]`, `decided_by: DecidedByText | None = None`.
- `StoryDecisionResult`: `workflow_id`, `status: Literal["ready","rejected","unsupported","failed",
  "expired"]`, `decided_by: str | None`, `decided_at: datetime | None`.
- Typed errors: `StoryDetailError` base; `StoryDetailNotFound` (missing/unknown id or RPC
  `NOT_FOUND`); `StoryDetailExpired` (at or past the 48-hour UI cutoff); `StoryNotReviewable` (not
  a generation workflow, or its status is not `review`); `StoryDetailMalformed` (typed result or
  query state violates its contract or cannot decode); `StoryDetailUnavailable` (connect failure or
  any other Temporal `RPCError`). Keep `StoryStatusUnavailable` for the list path.

### Gateway

`StoryDetailGateway` protocol:

```
async def candidate_state(self, workflow_id: str) -> CandidateReviewState
async def candidate_result(self, workflow_id: str) -> StoryCandidateResult
async def story_state(self, workflow_id: str) -> StoryGenerationState
async def story_result(self, workflow_id: str) -> StoryGenerationResult
async def decide(self, workflow_id: str, decision: FinalStoryDecision, *, approve: bool) -> None
```

`TemporalStoryDetailGateway(client)` uses `get_workflow_handle_for(StoryCandidateWorkflow.run, id)`
and `get_workflow_handle_for(StoryGenerationWorkflow.run, id)`, then `query`, `result` and
`signal`. It maps gRPC `NOT_FOUND` to `StoryDetailNotFound`, any other `RPCError` to
`StoryDetailUnavailable`, and result-decoding `ValidationError`/`ValueError`/`TypeError` to
`StoryDetailMalformed`. It never calls `fetch_history` and never reads raw event payloads.

### Show algorithm (`StoryDetailService.show(detail_id, *, now)`)

`now` must be timezone-aware (naive raises `ValueError`); compute `cutoff = now - STORY_UI_RETENTION`
once. `retained(created_at, cutoff)` is the existing strict helper (`created_at > cutoff`); a
non-retained item raises `StoryDetailExpired`. Do the cutoff check as soon as a created timestamp is
available, before any result read.

1. `kind = story_detail_kind(detail_id)`.
2. Candidate id: `state = candidate_state(id)`.
   - `awaiting_review` -> retain `state.candidate.observed_at`; return Pending with
     `candidate.title`/`source`, `package=None`.
   - `approved` with `state.story_workflow_id` -> follow it with `_generation_detail(...)`; the
     candidate id remains a working alias. If the story handle is missing, treat it as Writing with
     the `Starting story generation.` detail, not Closed.
   - `approved` without a story id -> retain `observed_at`; return Writing with
     `status_detail="Starting story generation."`.
   - `rejected`/`expired` -> retain `observed_at`; `result = candidate_result(id)`; validate the
     status is one of those two (an unexpected `approved` follows the approved branch); return
     Closed with `internal_status=result.status`, the sanitized note/decision metadata.
3. Generation id: `_generation_detail(id, cutoff)`.
   - `state = story_state(id)`; `state.story_created_at` is required or `StoryDetailMalformed`;
     `retained` or `StoryDetailExpired`.
   - Fetch `candidate_state(state.candidate_id)` once for title and source. A missing candidate
     history is not fatal: fall back to `""` (the view shows `Untitled story` / `Unknown source`).
     Other RPC errors propagate as Unavailable.
   - Open statuses (`researching`, `analyzing`, `writing`, `checking`, `review`) -> return the
     primary status; include `state.package` only at `review`; never include internal state names
     beyond `status_detail`.
   - Terminal status (`ready`, `rejected`, `unsupported`, `failed`, `expired`) -> `result =
     story_result(id)`; validate `result.story_id == state.story_id`, `result.candidate_id ==
     state.candidate_id`, and `result.status == state.status.value`; `ready` must carry a package or
     `StoryDetailMalformed`; retain `result.story_created_at` again. Return `ready` with the package,
     `rejected`/`unsupported`/`failed`/`expired` as Closed. For closed states do not return the
     rejected package to the view (it is not rendered).

Never expose prompts, evidence rows, raw failures, stacks or payloads.

### Decision semantics (`StoryDetailService.decide(request, *, now)`)

- Only a `story-v1-` id can receive a final decision; a candidate id raises `StoryNotReviewable`.
- Query `story_state`; missing created time -> `StoryDetailMalformed`; past the cutoff ->
  `StoryDetailExpired` (no signal is sent).
- If the status is not `review` -> `StoryNotReviewable` (the route maps this to the closed notice).
- Build `FinalStoryDecision(decided_by=request.decided_by)` and send exactly one `approve` or
  `reject` signal, then `await story_result(id)`. First final decision wins in the workflow; the
  returned terminal status is authoritative. Validate it is one of the five terminal statuses and
  that `ready` carries a package.
- The service returns the result status; it never publishes, renders, charts or calls another
  service. A repeated or raced decision is not an error: it returns whatever terminal status the
  workflow recorded.

## 48-hour cutoff

`StoryDetailService` computes the cutoff once per request and applies the existing strict
`retained()` rule to candidate and generation details, the htmx fragment, and the decision path:
47:59:59 old is visible; 48:00:00 old raises `StoryDetailExpired` before any result read or signal.
Generation inherits the candidate's `observed_at` as `story_created_at`, so it never resets the
clock. This is presentation retention only: no history is deleted and the workflow's own review
timer is already capped at the same instant.

## htmx active refresh

- `GET /stories/{detail_id}/status` renders `partials/story_detail_body.html`, whose root is
  `<div id="story-detail">`. The full page includes the same partial.
- Poll only while the primary status is `pending` or `writing`: the root gets
  `hx-get="/stories/{quote(detail_id, safe='')}/status"`, `hx-trigger="every 10s"`,
  `hx-swap="outerHTML"`, and the progress area shows the hint. Polling stops automatically when the
  fragment returns `review`, `ready` or `closed` (no `hx-get`). `review` deliberately does not poll:
  the package is stable and re-rendering long-form text would disturb reading and focus. Changes
  made elsewhere are seen on reload or on the post-decision redirect.
- The polling region excludes the `Back to stories` link, the notice and the header/footer, so an
  `outerHTML` swap cannot drop focus from navigation. There is no `aria-live`; the list page uses
  the same silent-refresh policy. The decision forms exist only at `review`, when polling has
  stopped.
- The fragment returns 200 for success and for every typed failure (`NotFound`, `Expired`,
  `Malformed`, `Unavailable`) so htmx swaps and stops; a failure fragment renders
  `This story is no longer updating.` or `Status is unavailable. Check again.` with no `hx-get`.
  Full pages use the 404/410/502/503 not-available pages.
- No JavaScript build step, no framework, no per-row query. The full page and every fragment work
  without JavaScript; the decision forms are plain POST forms.

## Routes

Add to `web/routes/stories.py` **after** `/stories/rows` so the literal route still wins:

| Method | Path | Behaviour |
| --- | --- | --- |
| GET | `/stories/{detail_id}` | `?decided=` allow-listed notice; 404/410/502/503 pages; full shell |
| GET | `/stories/{detail_id}/status` | htmx fragment; always 200; no shell, no filter bar |
| POST | `/stories/{detail_id}/decide` | `current_user`; form `decision=approve\|reject`; 400 invalid; 303 success/closed; 404/410/502/503 pages |

POST redirects: success or race -> `303 /stories/{quote(detail_id, safe='')}?decided=<token>#story-notice`;
`StoryNotReviewable` -> `303 ...?decided=closed#story-notice`. The detail page renders the notice with
`id="story-notice" tabindex="-1" role="status"` and the existing `.candidate-flash` focus outline.
Map the returned status: `ready` + approve -> `approved`; `rejected` + reject -> `rejected`; anything
else (`expired`, `unsupported`, `failed`, or a first decision that differs) -> `closed`.

`web/dependencies.py` adds `get_story_detail_service`, reusing the cached
`app.state.temporal_client` and mapping connect-time `RPCError`/`OSError`/`RuntimeError` to
`StoryDetailUnavailable`. Routes catch the typed detail errors directly (like the candidate detail
routes); no global handler is added for the list page's error type.

## View model contract (`web/view_models.py`, pure, no I/O)

- `StoryPackageView`: `headline`, `summary`, `body_paragraphs: list[str]`,
  `key_points: list[StoryKeyPointView]`, `evidence: list[StoryEvidenceLink]`,
  `fact_references: list[StoryFactReference]`, `caveats: list[str]`, `post_text: str | None`, and
  the section labels (`key_points_label`, `evidence_label`, `facts_label`, `caveats_label`,
  `post_text_label`).
- `StoryKeyPointView`: `text`, `citations: list[StoryCitation]`; `StoryCitation`: `label` (`[1]`),
  `href` (`#story-fact-1`), `fact_id`; `StoryFactReference`: `anchor` (`story-fact-1`), `label`,
  `fact_id`; `StoryEvidenceLink`: `label`, `url: str | None`, `available: bool`, `hint`.
- `StoryDetailView`: `page_title`, `detail_id`, `workflow_id`, `back_href`, `back_label`, `notice`,
  `error`, `state: Literal["pending","writing","review","ready","closed","problem"]`, `title`,
  `source_label`, `created_iso`, `created_text`, `status_label`, `status_class`, `progress`,
  `polling`, `polling_href`, `polling_trigger` (`every 10s`), `polling_hint`,
  `region_id` (`story-detail`), `package`, `decision_heading`, `decision_note`, `decision_action`,
  `approve_label`, `reject_label`, `approved_heading`, `approved_body`, `approved_by`,
  `approved_at`, `closed_heading`, `closed_body`, `closed_note`, `problem_message`.
- `StoryNotAvailableView`: `page_title`, `heading`, `body`, `retry_href: str | None`,
  `retry_label`, `back_href`, `back_label`.
- Builders: `build_story_detail_view(detail, *, now, refresh_id=None, notice="", error="")`,
  `build_story_detail_problem_view(detail_id, *, message)`, `build_story_not_available_view(kind,
  *, detail_id)`, `story_package_view(package)`, `story_decision_notice(token)`,
  `safe_source_url` (reuse).
- `build_story_detail_view` sets `polling = state in {"pending","writing"}`,
  `polling_href = /stories/{quote(refresh_id or detail.detail_id, safe='')}/status`,
  `decision_action = /stories/{quote(detail.workflow_id, safe='')}/decide`, and chooses the `<h1>`
  as `package.headline` when a package is present, else `detail.title.strip() or "Untitled story"`.
  `status_label`/`status_class` reuse the single `STORY_STATUS_LABELS`/`STORY_STATUS_BADGE_CLASSES`
  mapping; never branch on internal statuses in the template.
- `body_paragraphs` splits on blank lines and collapses single newlines to spaces; fact citations
  are deduplicated and numbered by first appearance across key points (maximum 20). Evidence URLs
  pass `safe_source_url`; unsafe/missing URLs render no anchor. All user text is escaped by Jinja.

## Acceptance criteria

- [ ] `GET /stories/{id}` resolves a deterministic candidate or generation workflow and renders the
      shared shell and exactly one of the five primary statuses. Pending/writing show one short
      progress explanation and no package; review renders the full package (headline, summary, body
      paragraphs, key points with clickable footnote citations, safe clickable evidence links,
      caveats, optional post text) and the final Approve/Reject rail; ready renders the same package
      read-only with approval metadata; closed renders one `closed` badge and a clear
      rejected/unsupported/failed/expired reason with no package or decision form.
- [ ] `StoryDetailService` uses `review_state`/`story_state` queries for open workflows and typed
      `result()` values for terminal workflows, follows an approved candidate's `story_workflow_id`,
      never parses history, and maps missing, past-cutoff, malformed, non-reviewable and unavailable
      cases to the typed errors and 404/410/502/503 pages described above. The service never
      publishes, charts or calls another use case.
- [ ] Final Approve/Reject is accepted only for a `story-v1-` workflow in `review`; the first final
      decision wins; repeated/raced/already-closed decisions redirect to the closed notice; an
      invalid decision re-renders at 400 with no signal; a decision or view at/after 48 hours sends
      no signal; nothing is published and no chart or Bluesky activity is imported or called.
- [ ] The strict 48-hour cutoff (`created_at > now - 48h`) applies to the full page, the fragment and
      the decision: visible at 47:59:59 and absent at 48:00:00, with generation never resetting the
      inherited detection time; expired details stop polling.
- [ ] The detail refreshes through `GET /stories/{id}/status` every 10 seconds only while pending or
      writing, swapping only `#story-detail`; review, ready, closed, expired and unavailable views
      carry no `hx-get`; fragment failures return 200 and stop; no JavaScript build or framework is
      added, and reading and deciding work with JavaScript disabled.
- [ ] At 360px the page is one stacked column with no horizontal overflow; at 768px and 1280px the
      reading column keeps a comfortable measure and review/ready use the 17rem rail. Only existing
      colour, type, spacing, border and shadow tokens are used (no raw hex), WCAG AA contrast and
      the 3px focus outline hold, reduced motion is respected, keyboard order and the notice focus
      are correct, and `just lint` / `just test` pass with existing candidate, chart and story-list
      tests unchanged.

## Files to touch

- `src/ai_trend_pipeline/services/stories.py` (new detail models, `StoryDetailService`, kind
  resolution, cutoff, decision semantics, typed detail errors; list behaviour unchanged)
- `src/ai_trend_pipeline/services/temporal_gateway.py` (`TemporalStoryDetailGateway`: candidate and
  story queries/results/signals, NotFound/RPC/decode mapping; no history calls)
- `src/ai_trend_pipeline/web/dependencies.py` (`get_story_detail_service`)
- `src/ai_trend_pipeline/web/routes/stories.py` (`GET /stories/{id}`, `GET /stories/{id}/status`,
  `POST /stories/{id}/decide`; invalid-decision 400; notice tokens; keep `/stories/rows` first)
- `src/ai_trend_pipeline/web/view_models.py` (`StoryDetailView`, package/citation/evidence view
  helpers, not-available view, progress/closed-reason/notice copy, builders)
- `src/ai_trend_pipeline/web/templates/story_detail.html` (new full page)
- `src/ai_trend_pipeline/web/templates/partials/story_detail_body.html` (new polling body)
- `src/ai_trend_pipeline/web/templates/story_not_available.html` (new 404/410/502/503 page)
- `src/ai_trend_pipeline/web/static/app.css` (reading typography, status header, citations,
  evidence/facts, caveats, rail, closed block, problem line; existing tokens only)
- `tests/test_story_detail_service.py` (new)
- `tests/test_web_story_detail.py` (new)
- `docs/architecture/tech.md` (routes, services, templates/design)
- `README.md` (replace the "detail page arrives later" note with the story detail and final-review
  behaviour)

## Tests to write

`tests/test_story_detail_service.py` (fake gateway; fixed aware clock; no Temporal)

- `test_candidate_pending_maps_to_pending_without_package` - title/source/created retained, no
  package, no result call.
- `test_candidate_approved_follows_the_generation_workflow` - approved with `story_workflow_id`
  queries the story; state researching -> Writing with the story id; package absent.
- `test_candidate_approved_without_story_is_writing_with_starting_detail` - `status_detail` is
  `Starting story generation.`; no result read.
- `test_open_story_states_use_the_query_and_only_review_has_a_package` - researching/analyzing/
  writing/checking -> package `None`; review -> package; no result call.
- `test_ready_uses_the_typed_result_and_returns_the_package` - query ready -> one result read;
  ready detail with package and decision metadata.
- `test_each_closed_reason_maps_to_closed` - story rejected/unsupported/failed/expired and candidate
  rejected/expired; `internal_status`, sanitized detail and package suppression are correct.
- `test_unknown_or_missing_id_is_not_found` - blank, over-long, wrong prefix and fake `NOT_FOUND`.
- `test_cutoff_is_strict_for_every_state` - candidate and story at 47:59:59 visible; at 48:00:00
  raises `StoryDetailExpired` before any result read.
- `test_candidate_metadata_fallback_does_not_fail_the_story` - story exists but candidate query is
  `NotFound`; title/source fall back and the story still renders.
- `test_decide_sends_one_final_decision_and_returns_the_result` - approve and reject; one signal;
  `decided_by` passed; returned status authoritative.
- `test_decide_rejects_candidate_ids_and_non_review_states` - both raise `StoryNotReviewable` with
  no signal.
- `test_decide_after_the_cutoff_sends_no_signal` - `StoryDetailExpired` and zero signal calls.
- `test_repeated_decision_returns_the_first_terminal_status` - fake state is terminal on the second
  call; `StoryNotReviewable`; the first decision is unchanged.
- `test_malformed_query_or_result_raises_story_detail_malformed` - missing created time, ready
  without a package, status mismatch, and a fake decode error.
- `test_non_not_found_rpc_maps_to_story_detail_unavailable`.
- `test_naive_now_raises_value_error` - for both `show` and `decide`.
- `test_detail_service_imports_no_publishing_or_chart_modules` - source/AST check for `bluesky`,
  `render`, `daily_chart`, `chart_spec` and `fetch_history`.

`tests/test_web_story_detail.py` (fake detail service; no Temporal)

- `test_pending_and_writing_pages_show_one_badge_and_one_explanation` - no package, no internal
  state names, exactly one progress line, polling hint only while polling.
- `test_review_renders_the_full_package` - h1 headline, summary, body paragraphs, key points in
  order, citation links and anchors, evidence links with href/target/rel and hidden hint, caveats,
  post text, two decision forms with the final labels and the nothing-published note.
- `test_ready_renders_the_package_read_only` - same package, approved panel and metadata, no
  decision form or buttons.
- `test_closed_renders_one_badge_and_each_reason` - parametrized rejected/unsupported/failed/
  expired (candidate and story), reason heading/body/detail, no package, no buttons.
- `test_missing_is_404_and_past_cutoff_is_410` - neutral copy, `Back to stories`, shared shell.
- `test_malformed_and_unavailable_are_502_and_503` - neutral copy, retry only where specified, no
  exception text or traceback.
- `test_approve_and_reject_redirect_with_notice_and_focus` - 303 to
  `?decided=approved|rejected#story-notice`; follow renders `#story-notice` with `tabindex="-1"`
  `role="status"`; fake records the decided_by email.
- `test_repeated_or_raced_decision_redirects_with_closed_notice` - service raises
  `StoryNotReviewable`; 303 with `decided=closed`; no second signal.
- `test_invalid_decision_is_400_without_a_signal` - `Choose Approve or Reject.`, detail markup, no
  service decision call.
- `test_source_links_are_limited_to_http_and_https` - `javascript:`, `data:`, `ftp:` and
  protocol-relative URLs built with `model_construct` render no anchor and the unavailable text;
  a valid `https` link is escaped and clickable.
- `test_untrusted_package_text_is_escaped` - `<script>`/`<img onerror>` in headline, summary, body,
  key points, caveats and evidence labels come back escaped with no raw tags.
- `test_long_body_citations_and_missing_optional_sections` - ~6000-character body, five key points
  with four fact IDs each, four evidence links and five caveats render without error; empty key
  points/evidence/caveats/post text omit their sections.
- `test_polling_only_while_pending_or_writing` - pending/writing carry `hx-get`,
  `hx-trigger="every 10s"`, `hx-swap="outerHTML"`; review/ready/closed/none do not.
- `test_status_fragment_returns_only_the_body` - `/stories/{id}/status` has `id="story-detail"`, no
  `<html>`, no site header or back link; a terminal response omits `hx-get`.
- `test_fragment_typed_failures_are_200_and_stop_polling` - expired/unavailable fragments return
  200 with neutral text and no `hx-get`; full pages keep 410/503.
- `test_decision_never_calls_chart_or_publishing` - override `get_chart_service` with a fake whose
  attributes raise; approve still 303s and only the detail service is called.
- `test_pages_work_without_javascript` - reading, citations, evidence and decision forms are plain
  links/forms; no navigation depends on `hx-` attributes.
- `test_keyboard_order_and_notice_focus_target` - Back to stories, reading, evidence, buttons in
  DOM order; notice has the focus target; no hidden interactive controls.
- Existing `tests/test_web_stories.py`, `tests/test_web_candidate_detail.py`,
  `tests/test_web_candidates.py`, `tests/test_story_status_service.py`, chart route tests and the
  full suite must pass unchanged.

## Screenshot and accessibility checks

- Screenshot at 360x800, 768x800 and 1280x800, light and dark: pending, writing, review with a long
  body and citations, ready, closed rejected, closed unsupported, closed failed, closed expired,
  missing 404, past-cutoff 410, malformed 502, unavailable 503, and the approved/rejected notice.
  Attach the images to the PR or handoff (do not commit them).
- At 360px confirm `document.documentElement.scrollWidth <= window.innerWidth`; the headline wraps,
  body paragraphs keep the measure, citation markers do not overflow, buttons are full width, and
  the rail stacks after the reading column.
- Keyboard-only: skip link -> Stories -> back link -> status -> summary/body -> key-point citations
  -> evidence links -> caveats -> Approve final story -> Reject final story -> notice; the 3px
  `--color-focus` outline is visible at every stop, and after a decision the `#story-notice`
  fragment receives focus.
- Disable JavaScript and repeat reading, citation jumps, evidence links, both decisions and the
  not-available pages. The notice focus works without a script.
- With JavaScript enabled, confirm pending/writing refresh only `#story-detail` every 10 seconds,
  review/ready/closed stop, focus stays on navigation while polling, and a fragment failure leaves
  the neutral line without looping.
- With reduced motion enabled, confirm no new animation runs. At 200% zoom recheck 360px wrapping.
- Confirm muted summary/citations/caveats on the ground and surface, accent links on the ground,
  ink on all five status fills and `.btn--danger-outline` text meet WCAG AA; `app.css` adds no raw
  hex and `tests/test_design_tokens.py` stays green.

## Out of scope

- Any workflow, activity, model or search-attribute change in `story_candidate.py`,
  `story_generation.py`, `story_status.py`, `story_visibility.py`, `worker.py`, `config.py` or the
  candidate/story start paths; no new review timeout, signal, query or visibility field.
- Publishing, Bluesky, chart generation, rendering, media, analytics, charts, dashboards, a sidebar,
  lifecycle diagrams, a JavaScript framework/build step, external assets or a new palette/typeface.
- Candidate detail/decision changes, candidate inbox changes, `/stories` list/filter/pagination
  changes, or a new per-row query on the list path.
- Fact-detail pages or a fact/evidence mapping that does not exist in `StoryPackage`; citations
  link to the on-page fact reference list and evidence links point at their own URLs.
- Deleting histories, cleanup workflows, database tables, migrations or backfilling old rows.
- Editing `docs/history.md` (the scribe handles the merged-task entry) or committing screenshots.

## Assumptions

- Workflow ids are the deterministic versioned ids from 044/053 (`candidate-v1-…`, `story-v1-…`); any
  other path segment is a 404. A future `story-v2-` id needs a new prefix branch.
- `StoryGenerationState` does not carry a title or source, so the detail service performs one extra
  candidate query for that metadata; if the candidate history is missing the story still renders
  with `Untitled story` / `Unknown source`. This is a detail-only query, never a list query.
- An approved candidate with `story_workflow_id` is followed transparently and the candidate id
  remains a working alias. A missing child is shown as Writing with `Starting story generation.`,
  because the detail query cannot observe the list page's execution-closed signal; the list's
  generation-start-failure mapping is unchanged.
- Polling is limited to pending and writing, unlike the list's active set (which includes review), so
  a long-form review page is not re-rendered while someone reads or tabs through it. Review changes
  made elsewhere appear on reload or after a decision.
- `StoryPackage` contains fact IDs and evidence links but no fact-to-evidence mapping, so citations
  are footnote-style anchors to a fact reference list; the clickable source references are the
  package's evidence links.
- Missing/unknown is 404, past the 48-hour UI cutoff is 410, a malformed typed result is 502 and a
  service outage is 503; fragment failures are always 200 so htmx swaps and stops.
- `ready` and `rejected` results already carry the package from 053; no workflow change is needed to
  render the approved package read-only. Nothing in this task publishes.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: wrote the spec with the `frontend-design` skill loaded and applied inside the existing
graph-paper/ink system. Next: human approval, then the builder (tests first). Assumptions above
record the prefix-based id resolution, the extra candidate metadata query, transparent handoff
following, pending/writing-only polling, footnote citations (no fact/evidence mapping exists), and
the 404/410/502/503 mapping.

**builder**: built `StoryDetailService` + `TemporalStoryDetailGateway` (queries for open, typed
results for closed, typed errors, strict 48h cutoff, one final signal), view models, three templates,
routes and token-only CSS; tests first (18 service, 32 web). Screenshots (360/768/1280, light/dark)
in `/tmp/055-shots`; 360 scrollWidth 345 <= 360. `just lint` and full `just test` (964) pass. Next:
verifier.
