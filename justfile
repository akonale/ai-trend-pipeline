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

dev:
    docker compose -f infra/compose.dev.yaml up -d --wait
    uv run python -m ai_trend_pipeline.worker

hello:
    uv run python -m ai_trend_pipeline.start_hello

down:
    docker compose -f infra/compose.dev.yaml down
