# 003: Local stack: docker-compose Postgres, Temporal dev server, `just dev`, hello workflow + test

Status: built

## Goal

Run Postgres and a Temporal dev server locally with Docker Compose. Add a hello workflow and activity, plus a worker and starter, so that `just dev` followed by `just hello` makes a completed `HelloWorkflow` show up in the Temporal UI. The workflow is tested with the time-skipping environment.

## Acceptance criteria

- [ ] `infra/compose.dev.yaml` defines two services. `postgres` uses a pinned `postgres` image tag, a named volume, dev-only credentials given as `${VAR:-default}` defaults (no `.env` file needed, no real secrets), and a `pg_isready` healthcheck. `temporal` uses a pinned `temporalio/temporal` image tag and runs `server start-dev` bound to `0.0.0.0` inside the container. Host ports bind to `127.0.0.1` only: 5432 (Postgres), 7233 (Temporal gRPC), 8233 (Temporal UI). No `latest` tags. Postgres is not wired into any Python code yet.
- [ ] `src/ai_trend_pipeline/hello.py` defines Pydantic models `HelloInput(name: str)` and `HelloOutput(greeting: str)`, an activity `say_hello(HelloInput) -> HelloOutput` that returns `"Hello, <name>!"`, a workflow `HelloWorkflow` whose `run(HelloInput) -> HelloOutput` calls that activity with a `start_to_close_timeout`, and a `TASK_QUEUE = "hello-task-queue"` constant. The workflow code has no I/O, clock or randomness. Pydantic is imported via `workflow.unsafe.imports_passed_through()` where the sandbox needs it.
- [ ] `src/ai_trend_pipeline/temporal_client.py` provides `temporal_address() -> str` (reads `TEMPORAL_ADDRESS`, defaults to `localhost:7233`) and `async connect() -> Client`, which always uses `temporalio.contrib.pydantic.pydantic_data_converter`. `src/ai_trend_pipeline/worker.py` (`python -m ai_trend_pipeline.worker`) connects, retrying for up to about 30 s while the server starts, and runs a worker for `HelloWorkflow` and `say_hello` on `TASK_QUEUE`. `src/ai_trend_pipeline/start_hello.py` (`python -m ai_trend_pipeline.start_hello`) runs `HelloWorkflow` with workflow id `hello-workflow` and prints the greeting.
- [ ] The `justfile` gains three recipes. `dev` runs `docker compose -f infra/compose.dev.yaml up -d --wait` and then the worker in the foreground. `hello` runs the starter. `down` runs `docker compose -f infra/compose.dev.yaml down`. `CLAUDE.md` adds all three to the Commands table (with their fallback commands) so that `test_claude_md_commands_match_justfile` keeps passing. It also removes "docker-compose" from the "no speculative code" list.
- [ ] `README.md` gets a "Local stack" section: needs Docker. Run `just dev` in one terminal and `just hello` in another, then open http://localhost:8233 to see `hello-workflow` as Completed (on a remote dev server, use `ssh -L 8233:localhost:8233 <host>`). `just down` stops the stack.
- [ ] Manual check, by the builder, recorded in the handoff log: after `just dev` and `just hello`, `temporal workflow describe -w hello-workflow` (or the UI) shows status Completed. `just lint` and `just test` pass, and `just test` does not need the compose stack running.

## Files to touch

- `infra/compose.dev.yaml` (new)
- `src/ai_trend_pipeline/hello.py` (new)
- `src/ai_trend_pipeline/temporal_client.py` (new)
- `src/ai_trend_pipeline/worker.py` (new)
- `src/ai_trend_pipeline/start_hello.py` (new)
- `tests/test_hello.py` (new)
- `justfile`
- `CLAUDE.md`
- `README.md`
- `pyproject.toml` / `uv.lock` (only if a dev dependency such as `pytest-asyncio` is needed for async tests; add it with `uv add --dev`)

## Tests to write

- `test_say_hello_activity`: run `say_hello` in `temporalio.testing.ActivityEnvironment` with `HelloInput(name="World")` and assert it returns `HelloOutput(greeting="Hello, World!")`.
- `test_hello_workflow_returns_greeting`: in `WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)`, start a `Worker` on a unique task queue with `HelloWorkflow` and `say_hello`, execute the workflow, and assert the result is a `HelloOutput` with the expected greeting.
- `test_temporal_address_default_and_env`: with `monkeypatch`, `temporal_address()` returns `localhost:7233` when `TEMPORAL_ADDRESS` is unset and returns the env value when it is set.

## Out of scope

- Using Postgres from Python: no drivers, schemas, migrations or connection code. That comes in task 013.
- Pointing Temporal at Postgres. The dev server keeps its built-in storage.
- Production compose, deploy, CI or GitHub Actions (tasks 004, 014, 015).
- Containerising the worker. It runs on the host through `uv run`.
- Tests that need Docker or a running Temporal server, and any test that parses the compose file.
- Edits to `infra/cloud-init.yaml`, `infra/bootstrap.sh`, `.claude/` or `docs/mvp-plan.md`.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->
**planner**: Wrote the spec for 003. Status is `draft` as requested (the planner default would be `planned`). It fits in one PR: about 5 small new files plus edits to the justfile, CLAUDE.md and README, so no split is needed. Next: human approves, then builder.
**planner**: Adding the `dev`/`hello`/`down` recipes means CLAUDE.md has to change, because `test_claude_md_commands_match_justfile` requires every recipe to appear there. The compose file goes in `infra/` per CLAUDE.md, so the recipes pass `-f`.
**planner**: The time-skipping test environment downloads a Temporal test-server binary on first run. That is not an API call, but CI (004) will need network access or a cache for it. Ports bind to 127.0.0.1, so a remote dev server needs an SSH tunnel to reach the UI.
**builder**: Implemented all files on task/003-local-stack, tests first; added pytest-asyncio (dev). `just lint` and `just test` pass (8 tests, no stack needed).
**builder**: Manual check done: `just dev` + `just hello` printed "Hello, World!", and `temporal workflow describe -w hello-workflow` showed COMPLETED. Stack then torn down with `just down`.
**builder**: Pinned images postgres:16.4 and temporalio/temporal:1.5.1 (pulled OK). Worker retry catches RuntimeError from Client.connect. Next: verifier.
