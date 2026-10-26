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

**Your approval?** column: `yes` means the orchestrator stops for you to approve the spec and to merge
the PR. `no` means it skips both checkpoints: it sets `Status: approved` itself, and once the scribe
reports the PR with a green CI check it merges the PR (`gh pr merge`) and moves on. The 2-fix-loop
limit and "spec is wrong or unclear" still stop and ask you in every case.

### Phase 0: Foundation
| # | Task | Done when | Your approval? |
| --- | --- | --- | --- |
| 001 | Project skeleton: uv, Python 3.12, Pydantic, Temporal SDK, Ruff, Pyright strict, pytest, justfile | `just lint` and `just test` pass on an empty project | yes |
| 002 | CLAUDE.md with project rules and the review checklist | Agents can follow it without asking | yes |
| 003 | Local stack: docker-compose Postgres, Temporal dev server, `just dev`, hello workflow + test | Hello workflow visible in the Temporal UI | yes |
| 004 | CI: GitHub Actions runs lint, types, tests on every PR | A PR shows green checks | yes |

### Phase 1: Daily Viz
| # | Task | Done when | Your approval? |
| --- | --- | --- | --- |
| 005 | Activity: fetch Hacker News top stories | Returns typed stories; tested with a fixture | no |
| 006 | Activity: LLM picks a chartable topic | Returns topic + reason; tested with a recorded response | no |
| 007 | Activity: fetch a matching Our World in Data dataset | Returns a typed table for a known topic | no |
| 008 | Activity: LLM writes a Vega-Lite spec + caption; render to PNG | A PNG file is produced from a fixture | no |
| 009 | Activity: LLM critique, one regenerate on failure | Bad fixture triggers one retry | no |
| 010 | Workflow: wire 005–009 together + approval signal + CLI to approve/reject | Workflow pauses, CLI approves, workflow continues | yes |
| 011 | Activity: post to Bluesky (test account), idempotent | Approved chart appears on the test account once | yes |

### Fixes
| # | Task | Done when | Your approval? |
| --- | --- | --- | --- |
| 017 | Fix: LLM activities use `tool_choice` auto (forced tool rejected by the model) | Tests assert `tool_choice` auto and that the prompts name the tool | no |
| 018 | Fix: topic picker chooses from the OWID catalog titles | Off-catalog pick raises a retryable error; every catalog title matches `match_dataset`; fixture tests pass | no |
| 019 | Optional manual topic override (`just chart --topic "..."`), depends on 018 | With a topic the workflow skips HN + `pick_topic`; CLI and time-skipping tests pass | no |

## Next: Topic Studio

**Goal:** you and a friend open a web page, type any topic, click Generate, review the chart, and
approve it, from any browser, behind a login that needs no auth code in the app.
**Order:** clean foundations, then user experience, then Cloudflare for sharing, then your style,
then refining.

**Where it runs for now:** the dev server, with `just dev` and `just web` each in their own tmux
window. A production server can host it later without changes.

### Web architecture (rules for every Topic Studio task)

The web app is a thin layer over a service layer that the CLI also uses. Dependencies point one way:

```
 web routes ─┐
             ├──▶ services ──▶ Temporal client ──▶ workflows ──▶ activities
 CLI ────────┘     (charts)                        (no web or service imports)
```

| Folder | Holds | Never does |
| --- | --- | --- |
| `src/ai_trend_pipeline/services/` | Use cases: start a chart, get its state, decide, refine, list runs. Plain async functions/classes taking Pydantic models | Read HTTP requests, render HTML, print |
| `src/ai_trend_pipeline/web/app.py` | `create_app(settings)` factory: wires routes, templates, static files, dependencies | Business logic |
| `src/ai_trend_pipeline/web/routes/` | One module per page (`home.py`, `runs.py`, ...). Parse input, call one service, render a template | Call Temporal or the LLM directly |
| `src/ai_trend_pipeline/web/templates/` | Jinja templates; `partials/` for htmx fragments | Logic beyond loops and ifs |
| `src/ai_trend_pipeline/web/view_models.py` | Pydantic models shaped for templates | I/O |
| `src/ai_trend_pipeline/config.py` | One `Settings` model read from env vars, passed in, not imported as a global | Read env vars anywhere else |

- Routes get services and settings through FastAPI dependencies, so tests swap in fakes. Route tests
  never need Temporal running.
- The signed-in user (task 026) is one dependency, `current_user`, used by every route that changes
  something.
- No JavaScript build step: htmx and a small CSS file, served from `web/static/`.

### Phase 2: Foundations
| # | Task | Done when | Your approval? |
| --- | --- | --- | --- |
| 020 | Service layer + settings: move start/show/decide out of `cli.py` into `services/charts.py`, add `config.py` and move every existing env var read into it (`cli.py`, `topic.py`, `temporal_client.py`, `bluesky.py`); the CLI becomes a thin wrapper. No behaviour change | Existing CLI tests pass unchanged; new service tests use a fake Temporal handle | yes |
| 021 | Web app skeleton: FastAPI + Jinja + htmx per the architecture above, `create_app`, `just web` on `127.0.0.1:8000`, one health page | Page loads through the SSH tunnel; route test uses the FastAPI test client | yes |

### Phase 3: Web UI (local, through your SSH tunnel)
| # | Task | Done when | Your approval? |
| --- | --- | --- | --- |
| 022 | Home page: topic box + Generate button starts a chart run via the service, then opens its run page | Clicking Generate starts a run visible in the Temporal UI | yes |
| 023 | Run page: live status (htmx polling), PNG preview, caption, critique, Approve / Reject buttons, and a "View in Temporal" link (`TEMPORAL_UI_URL` setting + workflow id) | A chart goes from Generate to posted without the CLI; the link opens that run in the Temporal UI | yes |
| 024 | Runs list on the home page: recent runs with topic, status, and link (service wraps the Temporal list API) | Both pending and finished runs show up | no |

### Phase 4: Share with a friend (Cloudflare)
| # | Task | Done when | Your approval? |
| --- | --- | --- | --- |
| 025 | Cloudflare Tunnel: `cloudflared` service in compose, two hostnames (web app, Temporal UI), runbook `docs/runbooks/cloudflare.md`; both reachable only through the tunnel | Web app opens for both of you after the email login; Temporal UI opens for you only; nothing listens publicly on the server | yes |
| 026 | Know who's who: `current_user` dependency reads the `Cf-Access-Authenticated-User-Email` header (or a fixed dev user when `AUTH_MODE=local`); requester and approver stored on each run and shown in the UI | Runs list shows who requested and approved each chart | yes |
| 027 | Per-person daily cap (default 10 charts, `DAILY_CHART_CAP`) | The 11th Generate of the day shows a friendly "limit reached" | no |

**You do these by hand before 025 (about 20 minutes, not agent work):**
1. Buy a domain or move one to Cloudflare (free plan), so Cloudflare manages its DNS.
2. In Cloudflare Zero Trust (free for up to 50 users): create a tunnel and copy its token into the
   server's `.env` as `CLOUDFLARE_TUNNEL_TOKEN`.
3. Add two Access applications, both with login by one-time PIN:
   - `charts.yourdomain.com`: allow your email and your friend's.
   - `temporal.yourdomain.com`: allow **only your email**. The Temporal UI has no permissions of its
     own, so anyone let in can cancel or reset workflows.

### Phase 5: Your style
| # | Task | Done when | Your approval? |
| --- | --- | --- | --- |
| 028 | Style profiles: per-person defaults (palette, font, caption tone, hashtags, signature), stored in Postgres, editable on a settings page, applied to the chart-spec and caption prompts | Two people get visibly different charts for the same topic | yes |
| 029 | Per-post options on the home page: chart type, time range, countries, caption tone, free-text instructions | Options visibly change the chart; blank options fall back to the style profile | yes |

### Phase 6: Refine before posting
| # | Task | Done when | Your approval? |
| --- | --- | --- | --- |
| 030 | Refine loop: a `refine` signal with an instruction ("make it a bar chart") regenerates spec and caption, re-runs critique, and waits for review again (max 5 rounds); `just refine ID "..."` | Time-skipping test covers refine → refine → approve | yes |
| 031 | Refine box on the run page, with the history of versions | Refining from the browser updates the preview | yes |
| 032 | Topic matching: show the 5 closest OWID datasets for a typed topic and let you pick one | A vague topic leads to a pick list, not a failed run | yes |
| 033 | Friendly run timeline on the run page: plain-language steps with timings, read from the workflow history via the service | Your friend can follow a run without the Temporal UI | yes |

## Running a task

In Claude Code on the dev server, inside the repo:

```
/run-task 001 project skeleton
```

The main session only orchestrates: it calls each agent in turn, reads the status line, and stops at
the two human checkpoints (approve the spec, merge the PR) for tasks marked `yes` in the
**Your approval?** column. Run the main session on `sonnet`
(`/model sonnet`); it routes work, it doesn't do it.
