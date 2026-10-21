# ai-trend-pipeline

Python 3.12 project managed with [uv](https://docs.astral.sh/uv/). Tasks run through a [just](https://just.systems/) justfile.

## Setup

```sh
uv sync
```

Creates `.venv` and installs runtime and dev dependencies from `uv.lock`.

## Commands

```sh
just          # list recipes
just lint     # ruff check, ruff format --check, pyright (strict)
just fmt      # ruff format and ruff check --fix
just test     # pytest
```

If `just` is not installed, run the same commands with `uv run`, for example `uv run pytest`.

All tool config lives in `pyproject.toml`.

## Local stack

Needs Docker. Postgres and a Temporal dev server run through `infra/compose.dev.yaml`.

```sh
just dev     # terminal 1: start the stack, then run the worker
just hello   # terminal 2: run HelloWorkflow
just down    # stop the stack
```

Open http://localhost:8233 to see `hello-workflow` as Completed. On a remote dev server, tunnel
the UI port first: `ssh -L 8233:localhost:8233 <host>`.
