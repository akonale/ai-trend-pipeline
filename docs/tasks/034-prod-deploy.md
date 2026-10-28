# 034: Production stack with docker-compose

Status: verified

## Goal

Run the whole app (Postgres, Temporal, Temporal UI, worker, web) on the existing Hetzner server with
one `docker compose -f infra/compose.prod.yaml --env-file .env up -d --build`. Use one uv-based image
for worker and web, and read secrets only from the server's `.env`.

Decisions:
- **Temporal uses Postgres, not SQLite `start-dev`.** Use the `temporalio/auto-setup` image with a
  pinned tag (`DB=postgres12`, `POSTGRES_SEEDS=postgres`). It creates the `temporal` and
  `temporal_visibility` schemas in the Postgres container that is already in compose, so runs survive
  restarts and nothing new needs backing up. The UI is a separate `temporalio/ui` container. If
  auto-setup turns out not to work with a pinned current tag, the builder may fall back to
  `temporalio/temporal` `server start-dev --db-filename /data/temporal.db` on a named volume, and must
  record that in the handoff note.
- **Env file:** compose resolves `.env` next to the compose file (`infra/`), not the repo root. Every
  command (recipe, runbook) runs from the repo root with `--env-file .env`. The plan's "Done when"
  command needs this flag.

## Acceptance criteria

- [ ] `infra/compose.prod.yaml` defines `postgres`, `temporal`, `temporal-ui`, `worker` and `web`.
  Every service has `restart: unless-stopped`. Postgres data lives on a named volume, and so do
  rendered charts (`charts`, mounted in both worker and web at the same path, e.g. `/data/charts`).
- [ ] Published ports are exactly `127.0.0.1:8000:8000` (web) and `127.0.0.1:8233:8080` (Temporal
  UI). Postgres and 7233 are not published.
- [ ] `worker` and `web` use the same `build:` (repo-root `Dockerfile`) with different `command`s
  (`python -m ai_trend_pipeline.worker` / `python -m ai_trend_pipeline.web`). They set
  `TEMPORAL_ADDRESS=temporal:7233` and `CHART_OUTPUT_DIR` to the shared volume path. `web` also sets
  `WEB_HOST=0.0.0.0`. The temporal UI points at `temporal:7233`.
- [ ] Secrets (`POSTGRES_PASSWORD`, `ANTHROPIC_API_KEY`, `BLUESKY_HANDLE`, `BLUESKY_APP_PASSWORD`) are
  only `${VAR}` interpolations with no literal defaults. `POSTGRES_PASSWORD` uses `${POSTGRES_PASSWORD:?...}`.
  `.dockerignore` excludes `.env`, `.venv`, `.git` and `charts/`.
- [ ] `Dockerfile` builds from a pinned Python 3.12 slim image with uv. It runs `uv sync --frozen
  --no-dev` and runs as a non-root user. `config.py` gains `web_host` (`WEB_HOST`, default
  `127.0.0.1`), which `web/__main__.py` uses instead of the hard-coded host, so `just web` behaves as before.
- [ ] `just deploy` runs `git pull --ff-only` and then `docker compose -f infra/compose.prod.yaml
  --env-file .env up -d --build`. `docs/runbooks/deploy.md` covers: the `.env` keys to set, the first
  deploy, the SSH tunnel (`ssh -L 8000:127.0.0.1:8000 -L 8233:127.0.0.1:8233`), updating, logs,
  stopping, and the warning not to run `just dev` at the same time (port clash on 8233).

## Files to touch

- `Dockerfile` (new)
- `.dockerignore` (new)
- `infra/compose.prod.yaml` (new)
- `justfile` (add `deploy` recipe)
- `src/ai_trend_pipeline/config.py` (add `web_host`)
- `src/ai_trend_pipeline/web/__main__.py` (use `settings.web_host`)
- `docs/runbooks/deploy.md` (new)
- `README.md` (short "Production" pointer to the runbook)
- `CLAUDE.md` (add `just deploy` row to the Commands table)
- `pyproject.toml` / `uv.lock` (`uv add --dev pyyaml types-pyyaml` for the static tests)
- `tests/test_compose_prod.py` (new)
- `tests/test_config.py` (extend)

## Tests to write

These are static checks only. They need no Docker daemon and no network.
- `test_compose_prod.py`: the file parses as YAML and has exactly the five services listed above.
- `test_compose_prod.py`: every service has `restart: unless-stopped`.
- `test_compose_prod.py`: the only published ports are `127.0.0.1:8000:8000` (web) and `127.0.0.1:8233:8080` (temporal-ui), with no `7233` or `5432` anywhere in `ports`.
- `test_compose_prod.py`: worker and web share the same `build` and set `TEMPORAL_ADDRESS=temporal:7233` and the same `CHART_OUTPUT_DIR`; both mount the `charts` volume there.
- `test_compose_prod.py`: env values whose keys contain PASSWORD/KEY/TOKEN/SECRET/HANDLE are `${VAR}` interpolations with no `:-` literal default.
- `test_compose_prod.py`: `Dockerfile` uses `uv sync --frozen --no-dev` and sets a non-root `USER`. `.dockerignore` lists `.env`.
- `test_config.py`: `WEB_HOST` is read into `web_host`, and it defaults to `127.0.0.1` when unset.

## Out of scope

- Cloudflare Tunnel, Caddy, TLS, domains or any public exposure (task 025).
- Auth, user identity and caps (026, 027).
- CI image builds, registries, Render, systemd units or backup automation.
- Changing `infra/compose.dev.yaml`, `just dev` or the server bootstrap script.
- Healthcheck endpoints or other app code changes beyond `web_host`.
- A `just up` recipe. `just deploy` is the only new recipe.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->
**planner**: Wrote the spec. Temporal runs on Postgres via pinned `temporalio/auto-setup` plus `temporalio/ui`; fall back to start-dev on a volume only if that fails, and log it.
Added `WEB_HOST` setting because the web host is hard-coded to 127.0.0.1, which is unreachable from outside the container.
Charts share a named volume between worker and web. Commands need `--env-file .env` because compose reads `.env` from `infra/`.
No `just up`; only `just deploy`. Static tests need `pyyaml` + `types-pyyaml` as dev deps.
Next: human approves (`approved`), then builder on `task/034-prod-deploy`.
**builder**: Added Dockerfile, .dockerignore, compose.prod.yaml (auto-setup 1.25.2 + ui 2.31.2, no fallback used), `just deploy`, runbook, `web_host`, static tests. lint and test pass (176).
Image tags (python 3.12.7, uv 0.5.5, temporal auto-setup 1.25.2, ui 2.31.2) were chosen from memory and never pulled; verify they exist on first deploy. Stack not started.
`docker compose config` parses the file (only blank-secret warnings with an empty env file).
**verifier**: `just lint` passed (ruff check, ruff format --check, pyright 0 errors). `just test` passed (176 tests).
All checks pass.
**reviewer**: Approved. All acceptance criteria and listed tests present; compose, Dockerfile, config and ports match the spec.
Notes: (1) docs/mvp-plan.md was edited (new Phase 7 row) though not in "Files to touch"; its row still mentions `just up`, which the spec excludes. (2) `# pyright: ignore[reportAny]` in tests/test_compose_prod.py has no explanatory comment. (3) Image tags unverified; confirm on first deploy.
**builder (fix 1)**: Added explanatory comment for the pyright ignore in tests/test_compose_prod.py; aligned the 034 row in docs/mvp-plan.md with the spec (`just deploy`, `--env-file .env`).
**verifier (re-run after fix 1)**: `just lint` passed (ruff check, ruff format --check on 78 files, pyright 0 errors). `just test` passed (176 tests).
All checks pass.
