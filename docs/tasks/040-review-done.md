# 040: Review and Done steps (run page as steps 3 and 4)

Status: built

## Goal

Restyle the run page as flow step 3 (generating, review) and step 4 (posted, rejected, expired),
with clear per-state headings, a stage message while polling, and end states that offer the next
action. Add an optional `ReviewState.post_uri` so an approved run links to its Bluesky post.

## Decisions

### 1. `ReviewState.post_uri` (workflow, `daily_chart.py`)

- New field on `ReviewState`, last in the class: `post_uri: str | None = None`. Nothing else in the
  workflow changes: no new activity, signal, query, timer or I/O. `DailyChartResult` is unchanged
  (it already carries `post`).
- `_new_state(...)` gets a keyword parameter `post_uri: str | None = None` and passes it to
  `ReviewState`. Only the final state update after the decision passes it:
  `self._new_state(status, data.requested_by, review, decision.note, decision.decided_by,
  post_uri=post.uri if post is not None else None)`. So `approved` carries the URI the
  `post_chart_to_bluesky` activity returned; `drafting`, `awaiting_decision`, `posting`,
  `rejected` and `expired` carry `None` (they never pass it).
- Replay safety: the change adds no commands, so the command sequence for every existing history
  is identical and replay cannot hit a non-determinism error. Query results are not stored in
  history, so there is no stored `ReviewState` to migrate. Pydantic fills the default `None` when a
  payload lacks the field, and ignores it when an older reader gets it. Note for the docs: querying
  a closed run replays its history with the current code, so approved runs from before this change
  also get `post_uri` (the activity result is in the history). The `None` branch on an approved run
  is a fallback (for example a URI that fails the strict parse below), and the page must still
  render well without it.

### 2. Bluesky link (`web/view_models.py`)

- Pure function `bluesky_post_url(uri: str | None) -> str | None`.
- Accept only `re.fullmatch` of
  `at://(did:plc:[a-z2-7]{24})/app\.bsky\.feed\.post/([a-z2-7]{13})` (compiled with `re.ASCII`;
  `fullmatch`, not `match` with `$`, so a trailing newline fails). This is the shape our own
  activity produces: a `did:plc` account on bsky.social and a 13-char TID record key from
  `make_tid`. Anything else (other DID methods, other collections, extra path or query, spaces,
  upper case, `None`, empty) returns `None`, which means no link. Never build a link from free text.
- Result: `f"https://bsky.app/profile/{did}/post/{rkey}"`. Built only from the two captured groups,
  which can only hold `[a-z2-7:]`, so no quoting is needed. Jinja autoescaping still applies.
- The anchor uses `target="_blank" rel="noopener noreferrer"`: the post opens in a new tab so the
  run page stays put. `noreferrer` keeps the run URL out of the Referer header. A visually hidden
  " (opens in a new tab)" follows the link text.
- This is the first link to a public third-party site. The "no CDN or external assets" rule covers
  things the page loads (CSS, JS, fonts, images), not anchors the person clicks. The existing
  Temporal link keeps exactly `target="_blank" rel="noopener"` (a test asserts it).

### 3. Generating stage: no new `stage` field

`ReviewState` only knows `drafting`, `awaiting_decision` (plus the web's `sent` flag) and
`posting`. The draft sub-steps (fetch data, draft, critique loop, render) partly run inside
`chart_loop.draft_chart_with_critique`, so an honest `stage` field would touch two modules and need
a hard-to-time intermediate query test. That is not a few lines, so it is **out of this task**. The
page shows the stages the data does carry, as a labelled progress bar with one message per stage
(strings in section 5). The existing "Working on it, checking every 2 seconds" line stays.

### 4. Polling and fragment (unchanged contract)

- `polling` stays `status in ("drafting", "posting") or (awaiting and sent)`. The
  `#run-status` div keeps its id, class and the exact attributes
  `hx-get="/runs/{id}/status[?sent=1]" hx-trigger="every 2s" hx-swap="outerHTML"`, only while
  polling. The stepper out-of-band include at the end of the fragment is unchanged. Exactly one
  `id="flow-stepper"` per page. No new JavaScript.
- The `<h1>` moves from `run.html` into the fragment (top of `#run-status`) so the heading changes
  when polling moves the state on. `run.html` keeps the stepper include, the fragment include and the
  "← New topic" back link; it loses `<h1>Chart run</h1>`.
- "View in Temporal" stays the last element of the fragment, same markup.
- No failed status exists in the `ReviewState` Literal. A failed workflow keeps its last status
  (for example `posting`) and keeps polling; that is today's behaviour and out of scope.

## Design plan (frontend-design skill applied, constrained by the 036 system)

- Colour: no new tokens or colours. Ink and grid paper from 036: `--color-bg` #e3ecdf,
  `--color-surface` #ffffff, `--color-text` #15172b, `--color-accent` #2448d8, fills
  `--color-success` #7ddba3, `--color-danger` #ff9a9a, `--color-neutral` #d3d6de (dark theme via the
  existing tokens). The outcome band uses a fill colour only as a thick top border on a surface
  card; it is decorative (the heading carries the meaning) and no text sits on it, so no new
  contrast pair is needed and `tests/test_design_tokens.py` is unchanged. All text and focus rings
  sit on `--color-surface` or `--color-bg`, pairs that are already checked.
- Type: existing stacks. h1 via `:where(h1)` (`--text-2xl`, display face, 900); h2 `--text-xl`;
  new h3 inside run details `--text-lg`, weight 800, `--font-display`, margin
  `var(--space-4) 0 var(--space-2)`. Sentence case everywhere, no all-caps labels.
- Layout: single left-aligned column inside `main` (max 44rem), works at 360px. The chart frame
  stays the one bold element (marigold hard shadow); everything else is quiet. Order inside
  `#run-status`:

```
[error alert]                         (only on 409)
[badge]                               .run-head
Heading for this state (h1)
Status: ... / wf-id / Requested by / Approved|Rejected by / Note      .run-meta
[progress bar + stage message + "Working on it..."]                    (polling only)
+--------------------------------------------------+                   (end states only)
|======== band (success | danger | neutral) =======|  .card.outcome
| End message                                      |
| [View the post]  [Make another chart]            |  .step-actions
+--------------------------------------------------+
[ chart frame: PNG + figcaption caption ]                              (when review)
+ details card: h2 topic, Why, h3 Critique + summary + reasons,
  h3 Options used + dl.options-list, option notes +                     (when review)
+ decision card: h2 Your decision, hint, [Approve and post] [Reject] +  (can_decide only)
View in Temporal
```

- Principles: one heading per state that says what is true now; one primary action per end state;
  buttons named by their action; no decoration beyond the 036 tokens.

### CSS (append to `web/static/app.css` under `/* Run page: steps 3 and 4 */`)

- `.run-head { margin: var(--space-6) 0 var(--space-3); }`
- `.run-head h1 { margin: var(--space-3) 0 0; }` (overrides the `:where(h1)` margin)
- `.run-meta { margin-bottom: var(--space-5); }`
- `.run-progress .run-progress__stage { color: var(--color-text); font-size: var(--text-base);
  font-weight: 700; }` (two classes so it beats `.run-progress p`, which is 0,1,1)
- `.outcome { border-top-width: 0.75rem; }` plus `.outcome--success { border-top-color:
  var(--color-success); }`, `.outcome--danger { border-top-color: var(--color-danger); }`,
  `.outcome--muted { border-top-color: var(--color-neutral); }`
- `.outcome__message { font-size: var(--text-lg); font-weight: 700; }`
- `.outcome .step-actions { margin-top: var(--space-4); }` (reuses 038 `.step-actions`: column
  below 40rem, row from 40rem)
- `.run-details h3 { ... }` as in Type above.
- Below 40rem: `.decisions { flex-direction: column; }` and `.decision .btn { width: 100%; }`.
- Reduced motion: unchanged (the progress bar rule already covers it). No `http` in the CSS.

## Copy (exact strings, constants in `web/view_models.py`)

| Where | Text |
| --- | --- |
| h1 drafting | `Drafting your chart` |
| h1 awaiting, not sent | `Review your chart` |
| h1 awaiting, sent | `Sending your decision` |
| h1 posting | `Posting to Bluesky` |
| h1 approved | `Posted to Bluesky` |
| h1 rejected | `Rejected` |
| h1 expired | `Expired: nobody decided in time` |
| stage drafting | `Drafting your chart. This usually takes a minute or two.` |
| stage sent | `Decision sent, waiting for the workflow.` |
| stage posting | `Posting your chart to Bluesky.` |
| end approved | `Your chart is live on Bluesky.` |
| end approved, no link (extra line) | `The link to the post is not available for this run.` |
| end rejected | `Nothing was posted.` |
| end expired | `Nobody approved or rejected this chart within 20 hours, so nothing was posted.` (hours from `APPROVAL_TIMEOUT`) |
| link | `View the post` + visually hidden ` (opens in a new tab)` |
| end action | `Make another chart` (href `/`) |
| critique summary | `Passed after 1 draft.` / `Failed after 2 drafts.` (`draft` when 1, else `drafts`) |
| details h3 | `Critique`, `Options used` |
| decision card h2 | `Your decision` (unchanged) |
| decision hint | `Approving posts this chart and caption to Bluesky.` |
| buttons | `Approve and post` (`btn btn--primary`), `Reject` (`btn btn--danger-outline`) |

Unchanged on purpose (tests rely on them): `STATUS_LABELS`, the `Status: ` line, `BADGES` and
`badge_for`, `Requested by: `, `Approved by: ` / `Rejected by: `, `Note: `, `Why: `,
`Working on it, checking every 2 seconds`, `View in Temporal`, `<figure class="chart-frame"`, a
plain `<figcaption>` (no class), `<dl class="options-list">` (and no other use of `options-list`),
`class="run-progress"` immediately followed by `role="status"` in the same tag.

End-state actions are `<a class="btn ...">` anchors, never `<button>` (end and drafting pages must
contain no `<button`). Approved: `View the post` is `btn btn--primary` and `Make another chart` is
`btn btn--secondary`; without a link only `Make another chart`, as `btn btn--primary`. Rejected and
expired: `Make another chart` as `btn btn--primary`.

## View model changes (`web/view_models.py`)

New `RunView` fields, all computed in `build_run_view` (templates only test them):
`heading: str`, `progress_message: str = ""`, `is_end: bool = False`,
`outcome_tone: Literal["success", "danger", "muted", ""] = ""`, `end_message: str = ""`,
`post_url: str | None = None`, `post_link_note: str = ""`, `another_href: str = "/"`,
`critique_summary: str = ""`. `is_end` is true for approved, rejected, expired.
`post_url = bluesky_post_url(state.post_uri)` only when status is `approved`; `post_link_note` is
set only when approved and `post_url is None`. Existing fields and their values stay.

## Acceptance criteria

- [ ] `ReviewState.post_uri` exists (default `None`); after approval the `review_state` query
      returns status `approved` with `post_uri` equal to the activity's `uri`; awaiting, rejected
      and expired runs have `None`. No new I/O or commands in the workflow.
- [ ] An approved run with a valid `post_uri` shows `Posted to Bluesky` and a `View the post`
      anchor to `https://bsky.app/profile/<did>/post/<rkey>` with `target="_blank"` and
      `rel="noopener noreferrer"`; an approved run without a usable `post_uri` renders 200 with the
      no-link line and no `bsky.app` anchor.
- [ ] Rejected and expired runs show their heading, end message and a `Make another chart` link
      to `/`, with no `<button` and no polling attributes.
- [ ] Drafting, posting and decision-sent pages show their heading and stage message inside the
      `role="status"` progress block and poll every 2s; the awaiting page shows `Approve and post`
      and `Reject` and does not poll. The stepper and `#run-status` swap contract are unchanged.
- [ ] Every existing test passes with no assertion changed (none of the strings they check
      change); `just lint` and `just test` pass.
- [ ] `docs/architecture/tech.md`, `docs/architecture/business.md` and `README.md` describe the
      new field, the run page states and the Bluesky link.

## Files to touch

- `src/ai_trend_pipeline/daily_chart.py` (field + `_new_state` param + final state update)
- `src/ai_trend_pipeline/web/view_models.py` (`bluesky_post_url`, copy constants, `RunView` fields)
- `src/ai_trend_pipeline/web/templates/run.html` (drop the h1)
- `src/ai_trend_pipeline/web/templates/partials/run_status.html`
- `src/ai_trend_pipeline/web/static/app.css` (append the run-page block)
- `tests/test_daily_chart.py` (new workflow test)
- `tests/test_web_run_states.py` (new)
- `docs/architecture/tech.md` (query field list, Templates section: run page states, the bsky link
  and its rel/target, why no stage field)
- `docs/architecture/business.md` (section 3: headings per state, the post link after approval,
  Make another chart on end states)
- `README.md` (run page paragraph: approved runs link to the post)

## Tests to write

`tests/test_daily_chart.py` (time-skipping env, existing fakes, `POST_URI`):
- `test_post_uri_set_only_after_approval`: awaiting state has `post_uri is None`; approve, await
  result, query shows `approved` and `post_uri == POST_URI`.
- `test_post_uri_none_when_rejected_or_expired`: reject one run and let another expire (time
  skipping); both final queries have `post_uri is None`.
- `test_review_state_without_post_uri_validates`: `ReviewState.model_validate({"status":
  "approved"})` gives `post_uri is None` (old payload shape).

`tests/test_web_run_states.py` (reuse `RecGateway`, `client_for`, `REVIEW` from `tests/test_web.py`;
valid URI constant `at://did:plc:` + 24 chars of `[a-z2-7]` + `/app.bsky.feed.post/` + 13 chars):
- `bluesky_post_url` maps the valid URI to the exact `https://bsky.app/profile/.../post/...` URL.
- `bluesky_post_url` returns `None` (parametrized) for: `None`, `""`, the fake `POST_URI` from the
  workflow tests, `did:web:` DID, `app.bsky.feed.like` collection, extra path segment, `?x=1`
  suffix, trailing `\n`, leading space, upper-case DID, `https://bsky.app/...`,
  `javascript:alert(1)`, a `"><script>` payload, a Cyrillic lookalike letter.
- `build_run_view` per state (parametrized: drafting, awaiting, awaiting+sent, posting, approved,
  rejected, expired): `heading`, `progress_message`, `is_end`, `outcome_tone`, `end_message` match
  the copy table.
- `build_run_view` approved: valid URI gives `post_url` and empty `post_link_note`; `None` or a
  bad URI gives `post_url is None` and the no-link note; non-approved states never get `post_url`
  even when `post_uri` is set.
- `critique_summary` singular and plural.
- Route: approved with valid `post_uri` renders the bsky anchor with `target="_blank"` and
  `rel="noopener noreferrer"`, `View the post` and `Make another chart`.
- Route: approved without `post_uri` returns 200, shows the no-link line, contains no `bsky.app`.
- Route: rejected and expired show heading, end message, `href="/"` for `Make another chart`,
  no `<button`, no `hx-trigger`.
- Route: drafting, posting, awaiting+sent pages and `/status` fragments contain the stage message
  and `hx-trigger="every 2s"`; awaiting (not sent) and end states do not.
- Route: awaiting page has `Approve and post`, `Reject`, the decision hint, `Critique`,
  `Options used` only when options exist; exactly one `<h1` on every state's page.
- Route: `/runs/wf-1/status` for every state keeps `id="run-status"`, one `id="flow-stepper"` with
  `hx-swap-oob="true"`, and no `<html`.

## Existing test assertions that change

None. Every string, class and attribute the run-page tests check (`tests/test_web.py`,
`test_web_ui.py`, `test_web_flow.py`, `test_web_shell.py`, `test_web_options.py`,
`test_daily_chart.py`) is kept. If the builder finds one that must change, stop and note it in the
handoff rather than editing it silently.

## Screenshots for the PR

360px and 1280px, light and dark: drafting, awaiting, decision sent, approved with link, approved
without link, rejected, expired.

## Out of scope

- New routes, changes to `POST /runs`, `POST /runs/{id}/decision` or the decision flow; a note
  field on the web decision form.
- A `stage` field or any per-activity progress; changing `chart_loop.py`.
- Any change to `bluesky.py`, `DailyChartResult`, the CLI (`cli.py` output) or services.
- New colour tokens, fonts, JavaScript, or changes to the stepper partial and `RUN_STEP`.
- Handling a failed workflow on the run page.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: Spec written; frontend-design skill was in context and applied within the 036 tokens (no new colours).
`post_uri` set only in the final post-decision state update, no new commands, replay-safe; closed runs
replay so older approved runs also get the link. No `stage` field (not a few lines; deferred).
No existing test assertion changes; h1 moves into the polled fragment. Next: orchestrator approves (approval: no), builder.

**builder**: Built per spec; tests first (test_web_run_states.py, 4 workflow tests); lint and full suite pass, no existing assertion changed. Skill frontend-design was in context. Added one small rule `.decision-card .meta` margin (hint touched buttons in screenshots).
Replay claim held: a fresh worker (empty cache) queried the closed approved run and got `post_uri` (test_closed_approved_run_replays_with_post_uri); this replays with current code, it cannot test a truly older history.
Browser checks at 360/1280, light/dark: no overflow, no mid-word breaks, one stepper/h1, polling stops at end, Approve POST redirects to ?sent=1, bsky link has rel noopener noreferrer. Next: verifier.
