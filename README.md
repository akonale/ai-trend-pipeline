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
