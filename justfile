default:
    @just --list

lint:
    uv run ruff check .
    uv run ruff format --check .
    uv run pyright

fmt:
    uv run ruff format .
    uv run ruff check --fix .

test:
    uv run pytest
