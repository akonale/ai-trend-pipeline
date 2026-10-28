# 025: Cloudflare Tunnel

Status: reviewed

## Goal

Add a `cloudflared` service to the production compose stack so the web app and the Temporal UI are
reachable through the existing Cloudflare Tunnel (behind Cloudflare Access email login), while
nothing on the server listens publicly. Also separate prod from dev:
- prod gets its own host ports and compose project name;
- prod reads its own env file, `.env.prod`.

Document the setup in a runbook.

Context: the Cloudflare side is already configured by hand (tunnel created, Access applications
added). The tunnel's public hostnames route to services inside the compose network. The human
confirms them in the Cloudflare dashboard (Zero Trust > Networks > Tunnels > the tunnel > Public
Hostname):

| Public hostname (placeholder) | Service URL | Access policy |
| --- | --- | --- |
| `app.example.com` | `http://web:8000` | owner's email + friend's email |
| `temporal.example.com` | `http://temporal-ui:8080` | owner's email only |

These in-network URLs do not change with the host port move below.

Real hostnames, emails and tokens live only in the server's `.env.prod` or the Cloudflare
dashboard. The repo uses `example.com` placeholders everywhere: spec, runbook, docs and tests.

## Decisions from the human

- **`.env.prod`**: production reads its settings from `.env.prod` in the repo root, separate from
  the dev `.env`, which `just` auto-loads through `set dotenv-load`. Every prod compose command uses
  `--env-file .env.prod`. These keys move to `.env.prod`:
  - `POSTGRES_PASSWORD`
  - `CLOUDFLARE_TUNNEL_TOKEN`
  - `TEMPORAL_UI_URL`
  - `ANTHROPIC_API_KEY`
  - `BLUESKY_HANDLE`
  - `BLUESKY_APP_PASSWORD`
  - `APP_URL`, for reference only (see below)

  The human has already moved `CLOUDFLARE_TUNNEL_TOKEN` from `.env` into `.env.prod` on the server.
- **No migration**: the production stack has never been started on the server, so there are no old
  volumes or containers to migrate.
- **`TEMPORAL_UI_URL`**: keep the compose passthrough to `web`. The real value is set only in
  `.env.prod`, for example `TEMPORAL_UI_URL=https://temporal.example.com`.
- **`APP_URL`**: documentation only. It names the web hostname set up in Cloudflare Access (no
  scheme, for example `APP_URL=app.example.com`). Nothing in `src/` reads it, and this task adds no
  setting. **Flag for the human:** say so if you want a real `APP_URL` setting (separate task).
- **Separate prod ports and project**: prod binds `127.0.0.1:18000->8000` (web) and
  `127.0.0.1:18233->8080` (Temporal UI), and sets `name: ai-trend-prod`. Dev keeps 5432, 7233 and
  8233, and `just web` stays on 8000.

**Flag for the human, dev `.env` leaking into `just deploy`:** `set dotenv-load` exports the dev
`.env` into every recipe. Compose gives shell variables priority over `--env-file`, so any key in
both files would silently take the dev value. To prevent this, the deploy recipe runs compose in a
clean environment:

```
env -i PATH="$PATH" HOME="$HOME" docker compose -f infra/compose.prod.yaml --env-file .env.prod up -d --build
```

The runbooks also warn not to run prod compose commands from a shell that has sourced `.env`.

## Acceptance criteria

- [ ] `infra/compose.prod.yaml` sets the top-level `name: ai-trend-prod`. Its only published ports
      are `127.0.0.1:18000:8000` (web) and `127.0.0.1:18233:8080` (temporal-ui). Its header comment
      refers to `.env.prod`, not `.env`.
- [ ] `infra/compose.prod.yaml` has a `cloudflared` service:
      - image `cloudflare/cloudflared` pinned to an explicit release tag (not `latest`, not untagged);
      - `command: ["tunnel", "--no-autoupdate", "run"]`;
      - `restart: unless-stopped`;
      - `depends_on` `web` and `temporal-ui`;
      - no `ports` and no `network_mode`;
      - `TUNNEL_TOKEN: ${CLOUDFLARE_TUNNEL_TOKEN:?set CLOUDFLARE_TUNNEL_TOKEN in .env.prod}`, with no
        default and never on the command line.
- [ ] The `web` service gets `TEMPORAL_UI_URL: ${TEMPORAL_UI_URL:-http://localhost:18233}`. This is
      compose config only, with no app-code changes. The `POSTGRES_PASSWORD` `:?` messages say
      `.env.prod`.
- [ ] Only the `deploy` recipe in `justfile` changes: `git pull --ff-only`, then the clean-env
      compose command above with `--env-file .env.prod`. `set dotenv-load` stays, so dev is
      unaffected.
- [ ] `.gitignore` and `.dockerignore` each gain a `.env.prod` line. Neither covers it today:
      both list only `.env`, which does not match `.env.prod`.
- [ ] `docs/runbooks/cloudflare.md` exists and covers:
      - the dashboard setup: tunnel, the two hostnames with the service URLs above, and two Access
        apps using one-time PIN;
      - why the Temporal UI is limited to the owner's email: it has no permissions of its own;
      - the `.env.prod` keys `CLOUDFLARE_TUNNEL_TOKEN`, `TEMPORAL_UI_URL` and `APP_URL`, noting that
        `APP_URL` is documentation only;
      - how to start the tunnel;
      - the manual checklist below;
      - how to rotate the token: refresh it in the dashboard, update `.env.prod`, then run
        `env -i PATH="$PATH" HOME="$HOME" docker compose -f infra/compose.prod.yaml --env-file .env.prod up -d cloudflared`;
      - the SSH fallback: `ssh -L 18000:127.0.0.1:18000 -L 18233:127.0.0.1:18233 <host>`.
- [ ] `docs/runbooks/deploy.md`:
      - uses `--env-file .env.prod` in every command;
      - lists all the `.env.prod` keys above, saying which are required;
      - says `.env.prod` is separate from the dev `.env` and that prod never reads `.env`;
      - warns about the shell-environment override;
      - uses ports 18000 and 18233 in the SSH section;
      - replaces the "both bind 8233" warning with "dev and prod run side by side";
      - has a single note: the compose project is named `ai-trend-prod`, and if a stack was ever
        started under the old default name `infra`, it can be removed with
        `docker compose -p infra down`;
      - links to `cloudflare.md`.
- [ ] `README.md` "Production" mentions `.env.prod`, the prod ports and the link to `cloudflare.md`.
      In `docs/mvp-plan.md`, the 034 row's `--env-file .env` becomes `--env-file .env.prod`.
- [ ] `just lint` and `just test` pass.

**Tests cannot verify the live tunnel.** The tests below only check files statically. The human
runs this checklist on the server after deploy. It is also copied into the runbook.

1. `just deploy` (or the `env -i ... --env-file .env.prod up -d --build` command) starts all six
   services under project `ai-trend-prod` (`docker compose ls`). The `cloudflared` logs (`... logs
   cloudflared`) show registered tunnel connections.
2. The dashboard shows the tunnel as Healthy, and the hostnames point to `http://web:8000` and
   `http://temporal-ui:8080`.
3. In a private window, the app hostname asks for the email PIN. After login with the owner's email,
   the home page loads and Generate works. Repeat with the friend's email.
4. The Temporal hostname lets the owner in and denies the friend.
5. Nothing is public:
   - on the server, `ss -tlnp` shows 18000 and 18233 bound only to `127.0.0.1`;
   - from another machine, ports 18000, 18233, 8000 and 8233 on the public IP are closed.
6. On a run page, "View in Temporal" opens the run on the Temporal hostname, using
   `TEMPORAL_UI_URL` from `.env.prod`.
7. With prod running, `just dev` and `just web` start on the same server with no port or container
   clashes.
8. `git status` does not list `.env.prod`.
9. The prod stack keeps working with `CLOUDFLARE_TUNNEL_TOKEN` removed from `.env`.

## Files to touch

- `infra/compose.prod.yaml` (from task 034)
- `tests/test_compose_prod.py` (from task 034)
- `docs/runbooks/deploy.md` (from task 034)
- `justfile` (`deploy` recipe only, from task 034)
- `.gitignore`
- `.dockerignore`
- `docs/runbooks/cloudflare.md` (new)
- `README.md`
- `docs/mvp-plan.md` (034 row text only)

## Tests to write

All tests go in `tests/test_compose_prod.py`. They are static: they read files and need no Docker,
no network and no git subprocess.

- Update `SERVICES` to include `cloudflared`, and rename the count test to six services.
- `test_project_name`: the top-level `name` is `ai-trend-prod`. Add a helper that returns the whole
  document.
- Update `test_published_ports`: exactly
  `{"web": ["127.0.0.1:18000:8000"], "temporal-ui": ["127.0.0.1:18233:8080"]}`.
- `test_prod_ports_differ_from_dev`: prod's host ports do not overlap the host ports in
  `infra/compose.dev.yaml`, and none of them is 8000.
- `test_cloudflared_service`:
  - the image is `cloudflare/cloudflared:<explicit tag>`, not `latest`;
  - the command is `["tunnel", "--no-autoupdate", "run"]`;
  - it depends on `web` and `temporal-ui`;
  - it has no `ports` and no `network_mode`.
- `test_tunnel_token_required_without_default`: `TUNNEL_TOKEN` is a
  `${CLOUDFLARE_TUNNEL_TOKEN:?...}` interpolation with no `:-` default, and it is not in `command`.
- `test_web_temporal_ui_url_from_env`: web's `TEMPORAL_UI_URL` is
  `${TEMPORAL_UI_URL:-http://localhost:18233}`.
- Update the existing `test_secrets_are_interpolations_without_defaults` so it also covers
  `TUNNEL_TOKEN`. Raise the `checked` floor to 4.
- `test_prod_uses_env_prod`:
  - the `deploy` recipe in `justfile` contains `--env-file .env.prod` and `env -i`;
  - no `--env-file .env` without `.prod` (regex `--env-file \.env(?!\.prod)`) appears in the deploy
    recipe, `compose.prod.yaml`, `deploy.md`, `cloudflare.md` or `README.md`.
- `test_env_prod_ignored`: `.gitignore` and `.dockerignore` each contain a `.env.prod` line. Extend
  the existing dockerignore test or add a new one.
- `test_no_real_domain`: `compose.prod.yaml`, `cloudflare.md`, `deploy.md` and `README.md` do not
  contain `trendrunner`.

## Out of scope

- Any Python app code (`src/`). That includes an `APP_URL` setting, `Cf-Access-*` headers (task
  026) and caps (task 027).
- Other `justfile` recipes, `set dotenv-load`, `Dockerfile`, `infra/compose.dev.yaml` and CI.
- Creating or changing anything in Cloudflare.
- Creating or editing `.env.prod` or moving secrets. The human has done this on the server.
- Any volume-migration steps. Prod has never run, so there is nothing to migrate.
- Removing the `127.0.0.1` bindings or the SSH fallback.
- Real domains, emails or tokens anywhere in the repo.

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->
**planner**: Wrote spec: `cloudflared` service (pinned, `TUNNEL_TOKEN` from required `${CLOUDFLARE_TUNNEL_TOKEN:?}`), web `TEMPORAL_UI_URL` passthrough, runbook, doc pointers, static compose tests.
Live tunnel is not testable; manual checklist is in the spec and must go in the runbook.
Human must confirm dashboard service URLs `http://web:8000` and `http://temporal-ui:8080`.
Next: human approves (approval: yes), then builder on `task/025-cloudflare-tunnel`.
**planner**: Revised per human: prod ports 18000/18233 + `name: ai-trend-prod`; `APP_URL` docs-only; placeholders `example.com`.
Flags: project rename orphans old `infra_*` prod volumes (migration steps in deploy.md); confirm if a real `APP_URL` setting is wanted.
Next: human approves, then builder.
**planner**: Revised: prod reads `.env.prod` (deploy recipe, docs, compose comment); `.env.prod` added to .gitignore/.dockerignore (both lacked it).
Flag: `set dotenv-load` exports dev `.env` and shell vars beat `--env-file`, so deploy recipe runs compose under `env -i`.
Human must move `CLOUDFLARE_TUNNEL_TOKEN` from `.env` to `.env.prod` by hand. Next: human approves, then builder.
**planner**: Final revision: dropped the volume migration (prod never started); deploy.md only notes project `ai-trend-prod` and `docker compose -p infra down` for any old stack.
Recorded that the human already moved `CLOUDFLARE_TUNNEL_TOKEN` into `.env.prod`; `.env.prod` revision stays applied.
Files to touch and tests unchanged by this revision. Open flags: `env -i` in deploy recipe, `APP_URL` setting wanted?
Next: human approves (approval: yes), then builder on `task/025-cloudflare-tunnel`.
**builder**: Implemented per spec: cloudflared service (image pinned to `2024.12.2`, NOT pulled or verified against the registry), prod ports 18000/18233, `name: ai-trend-prod`, `.env.prod`, env -i deploy, runbooks, tests.
Compose not run (static tests only). Open flags unchanged: `APP_URL` setting wanted? Next: verifier.
**verifier**: `just lint` passed (ruff check, ruff format --check, pyright 0 errors).
`just test` passed (184 passed).
All checks pass.
**reviewer**: Approved. All criteria and listed tests present; no real domains/secrets; cloudflared has no ports, token has no default; `.env.prod` in .gitignore and .dockerignore; deploy uses `env -i` with `--env-file .env.prod`.
Note 1: CLAUDE.md line 23 (`just deploy` row, from 034) still says `--env-file .env`; not in this spec's files, fix in a follow-up.
Note 2: mvp-plan 034 acceptance-cell edit is consistent with the spec. Image tag `2024.12.2` unverified; confirm on first deploy.
