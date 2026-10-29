# Deploy runbook

Runs Postgres, Temporal, the Temporal UI, the worker, the web app and `cloudflared` on the server
with `infra/compose.prod.yaml` (compose project `ai-trend-prod`). Run every command from the repo
root and always pass `--env-file .env.prod`: compose would otherwise look for `.env` next to the
compose file (`infra/`).

`.env.prod` is separate from the dev `.env`. Prod never reads `.env`. Public access through the
Cloudflare Tunnel is covered in [cloudflare.md](cloudflare.md).

## `.env.prod` keys

- `POSTGRES_PASSWORD` (required; compose refuses to start without it)
- `CLOUDFLARE_TUNNEL_TOKEN` (required; compose refuses to start without it)
- `TEMPORAL_UI_URL` (optional; public Temporal UI URL, for example `https://temporal.example.com`)
- `DAILY_CHART_CAP` (optional, default 10; charts each person can start from the web per UTC day, resets at 00:00 UTC, every started run counts, the CLI is not capped)
- `ANTHROPIC_API_KEY`
- `BLUESKY_HANDLE`
- `BLUESKY_APP_PASSWORD`
- `APP_URL` (optional, documentation only; the web hostname, for example `app.example.com`)

`DATABASE_URL` (style profiles) is assembled in compose from `POSTGRES_PASSWORD`, so it needs no new
`.env.prod` key. It requires a password without spaces, quotes or backslashes.

Never commit `.env.prod`.

## Shell environment warning

Compose gives shell variables priority over `--env-file`. Do not run prod compose commands from a
shell that has sourced the dev `.env`: its values would silently win. `just deploy` runs compose
under `env -i PATH="$PATH" HOME="$HOME" ...` for this reason; do the same by hand:

```sh
env -i PATH="$PATH" HOME="$HOME" docker compose -f infra/compose.prod.yaml --env-file .env.prod <command>
```

## First deploy

```sh
git clone <repo> && cd ai-trend-pipeline
cp /path/to/your/secrets .env.prod   # or create it with the keys above
env -i PATH="$PATH" HOME="$HOME" docker compose -f infra/compose.prod.yaml --env-file .env.prod up -d --build
```

## Reach the app (SSH tunnel)

Nothing is public. Only `127.0.0.1:18000` (web) and `127.0.0.1:18233` (Temporal UI) are published.

```sh
ssh -L 18000:127.0.0.1:18000 -L 18233:127.0.0.1:18233 <host>
```

Then open http://localhost:18000 and http://localhost:18233.

## Update

```sh
just deploy   # git pull --ff-only, then compose up -d --build
```

After a deploy, ensure the configured source ingestion workflows exist (idempotent, safe to re-run;
worker startup does not start them):

```sh
env -i PATH="$PATH" HOME="$HOME" docker compose -f infra/compose.prod.yaml --env-file .env.prod \
  exec worker python -m ai_trend_pipeline.ingestion_starter
```

## Logs

```sh
env -i PATH="$PATH" HOME="$HOME" docker compose -f infra/compose.prod.yaml --env-file .env.prod logs -f worker web cloudflared
```

## Stop

```sh
env -i PATH="$PATH" HOME="$HOME" docker compose -f infra/compose.prod.yaml --env-file .env.prod down
```

Add `-v` only if you want to delete the database and charts.

## Upgrading image tags

- Confirm each new tag exists in the registry before editing the pinned tags.
- If the stack holds data, bump one service group at a time (Postgres first) and deploy between steps.
- Run `just deploy`, then check `ps` and the logs with the commands above.

### Postgres collation version mismatch

Symptom: after a Postgres bump on an existing data volume, Temporal crash-loops with
`template database "template1" has a collation version mismatch`. The new image's OS library
differs from the one the data was created with. Fix it once, inside the Postgres container.
It only changes metadata, loses no data, and Temporal recovers through its restart policy:

```sh
docker exec ai-trend-prod-postgres-1 sh -c 'for d in template1 postgres; do psql -U "$POSTGRES_USER" -d postgres -c "ALTER DATABASE $d REFRESH COLLATION VERSION;"; done'
```

Any other database that reports a collation warning may need the same statement.
A Postgres major-version bump (16 to 17 or 18) is different: it needs a dump and restore, not this.

## Notes

Dev and prod run side by side: they use different host ports and project names.

The compose project is named `ai-trend-prod`. If a stack was ever started under the old default
name `infra`, it can be removed with `docker compose -p infra down`.
