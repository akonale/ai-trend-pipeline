# Deploy runbook

Runs Postgres, Temporal, the Temporal UI, the worker and the web app on the server with
`infra/compose.prod.yaml`. Run every command from the repo root: compose reads `.env` next to the
compose file (`infra/`), so we always pass `--env-file .env`.

## `.env` keys

- `POSTGRES_PASSWORD` (required; compose refuses to start without it)
- `ANTHROPIC_API_KEY`
- `BLUESKY_HANDLE`
- `BLUESKY_APP_PASSWORD`

Never commit `.env`.

## First deploy

```sh
git clone <repo> && cd ai-trend-pipeline
cp /path/to/your/secrets .env   # or create it with the keys above
docker compose -f infra/compose.prod.yaml --env-file .env up -d --build
```

## Reach the app (SSH tunnel)

Nothing is public. Only `127.0.0.1:8000` (web) and `127.0.0.1:8233` (Temporal UI) are published.

```sh
ssh -L 8000:127.0.0.1:8000 -L 8233:127.0.0.1:8233 <host>
```

Then open http://localhost:8000 and http://localhost:8233.

## Update

```sh
just deploy   # git pull --ff-only, then compose up -d --build
```

## Logs

```sh
docker compose -f infra/compose.prod.yaml --env-file .env logs -f worker web
```

## Stop

```sh
docker compose -f infra/compose.prod.yaml --env-file .env down
```

Add `-v` only if you want to delete the database and charts.

## Warning

Do not run `just dev` while the prod stack is up: both bind port 8233 on the host.
