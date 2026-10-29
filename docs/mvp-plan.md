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
| 036 | Neo-brutalist design system: rewrite the tokens and components in `web/static/app.css` (colour, type scale, spacing, border width, hard shadows, buttons with a pressed state, inputs, selects, textareas, cards, chips, badges, alerts, tables, chart frame, focus ring; light and dark). Class names only, no layout changes. The human approves the look (palette, type, example screenshots in the spec) before building | Home, run, settings, health and not-found pages all use the new look; `tests/test_web*.py` pass unchanged; every token pair used for text, borders and focus meets WCAG AA (a small script or test checks the pairs and is kept in the repo); screenshots at 360px and 1280px, light and dark, show no horizontal overflow | yes |
| 037 | App shell: shared header (brand, nav "New chart" and "Your style", signed-in email chip) and slim footer (product tagline, a "dev" tag when `AUTH_MODE=local`), built from a `shell` view model; add an optional-user dependency so GET pages can show the email without ever returning 401 (POST routes keep `current_user`); nav wraps cleanly on phones with no JavaScript | Every page renders the same header and footer; the email shows in cloudflare mode when the header is present and the dev user in local mode; GET routes never return 401 when the header is missing; route tests cover both modes; no sideways scroll at 360px | no |
| 038 | Flow steps 1 and 2 plus the stepper: a stepper component (1 Topic, 2 Style, 3 Review, 4 Done with done, current, upcoming states from the view model) on every flow page; step 1 at `/` picks the topic (box, example chips, Next); step 2 at `/new/style?topic=...` holds the per-chart options (chart type, time range, countries, caption tone, instructions) and a read-only summary of the saved style with a link to edit defaults; Generate posts to the existing `POST /runs` (contract unchanged) | Step 2 without a topic redirects to step 1; typed values survive Back and validation errors; the stepper shows the right state on step 1, step 2 and the run page; existing `POST /runs` route tests pass unchanged; works at 360px | no |
| 039 | Per-chart style overrides: extend `PostOptions` with palette, font, hashtags and signature (precedence per-chart option, then saved profile, then default; applied in code as today, prompts unchanged); step 2 gets an editable style panel prefilled from the saved profile and a "Save as my default" checkbox that saves through `StyleService` with the same validation as `/settings`. New fields optional with defaults so old runs replay | Changing palette or font in step 2 changes that chart only; with the checkbox ticked the profile is saved; blank fields fall back to the profile; invalid values return 400 with field messages and keep what was typed; a time-skipping workflow test replays an old payload without the new fields; the daily cap and the style-store 503 behaviour are unchanged | no |
| 040 | Review and Done steps: restyle the run page as step 3 (generating: progress that shows the current stage; review: chart card, caption, critique, options used, Approve and Reject buttons) and step 4 (approved: success state with a link to the Bluesky post; rejected and expired: clear end states with a "Make another chart" button). To link the post, add an optional `post_uri` to `ReviewState` (set after posting; the view model turns the `at://` URI into a `bsky.app` profile and post link); polling and the decision flow are unchanged | An approved run shows the posted link; rejected and expired runs show their end state; runs without `post_uri` still render; existing run-page tests pass (updated only where visible text changed); a time-skipping workflow test covers the new state field | no |
| 041 | UI QA and docs: screenshot pass (360px, 768px, 1280px, light and dark) over every page and state (home, step 2, generating, review, approved, rejected, expired, settings, not found, store unavailable), a keyboard-only walk-through, fixes for what it finds, removal of unused CSS, and updates to `docs/architecture/tech.md` (templates, routes, design tokens), `README.md` and `docs/history.md` | No horizontal overflow in any screenshot; focus is visible everywhere; no unused old-style rules remain; the architecture and history docs match the new UI | no |

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
