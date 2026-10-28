# Cloudflare Tunnel runbook

The `cloudflared` service in `infra/compose.prod.yaml` connects the stack to Cloudflare, so the web
app and the Temporal UI are reachable behind Cloudflare Access while nothing on the server listens
publicly. Hostnames below are placeholders (`example.com`); real values live only in `.env.prod`
and the Cloudflare dashboard.

`infra/compose.prod.yaml` sets `AUTH_MODE: cloudflare` on `web`, so POST routes need the
`Cf-Access-Authenticated-User-Email` header that Cloudflare Access adds. The app trusts it only
because the web port is bound to `127.0.0.1` and reached only through the tunnel.

## Dashboard setup

1. Zero Trust > Networks > Tunnels: create a tunnel and copy its token.
2. On the tunnel's Public Hostname tab add:

   | Public hostname | Service URL |
   | --- | --- |
   | `app.example.com` | `http://web:8000` |
   | `temporal.example.com` | `http://temporal-ui:8080` |

3. Zero Trust > Access > Applications: add two self-hosted applications, one per hostname, with
   one-time PIN login:
   - `app.example.com`: allow the owner's email and the friend's email.
   - `temporal.example.com`: allow the owner's email only.

The Temporal UI is limited to the owner because it has no permissions of its own: anyone who gets
in can inspect and terminate any workflow.

## `.env.prod` keys

- `CLOUDFLARE_TUNNEL_TOKEN`: the tunnel token. Required; compose refuses to start without it.
- `TEMPORAL_UI_URL`: public Temporal UI URL used by "View in Temporal", for example
  `https://temporal.example.com`. Defaults to `http://localhost:18233`.
- `APP_URL`: the web hostname set up in Access, for example `app.example.com`. Documentation only:
  nothing reads it.

Run prod compose commands as described in [deploy.md](deploy.md) (clean environment, `--env-file .env.prod`).

## Start the tunnel

```sh
just deploy
```

or

```sh
env -i PATH="$PATH" HOME="$HOME" docker compose -f infra/compose.prod.yaml --env-file .env.prod up -d --build
env -i PATH="$PATH" HOME="$HOME" docker compose -f infra/compose.prod.yaml --env-file .env.prod logs cloudflared
```

## Rotate the token

Refresh the token in the dashboard, update `CLOUDFLARE_TUNNEL_TOKEN` in `.env.prod`, then run:

```sh
env -i PATH="$PATH" HOME="$HOME" docker compose -f infra/compose.prod.yaml --env-file .env.prod up -d cloudflared
```

## SSH fallback

```sh
ssh -L 18000:127.0.0.1:18000 -L 18233:127.0.0.1:18233 <host>
```

## Manual checklist (after deploy)

1. `just deploy` starts all six services under project `ai-trend-prod` (`docker compose ls`). The
   `cloudflared` logs show registered tunnel connections.
2. The dashboard shows the tunnel as Healthy, and the hostnames point to `http://web:8000` and
   `http://temporal-ui:8080`.
3. In a private window, the app hostname asks for the email PIN. After login with the owner's
   email, the home page loads and Generate works. Repeat with the friend's email.
4. The Temporal hostname lets the owner in and denies the friend.
5. Nothing is public: on the server, `ss -tlnp` shows 18000 and 18233 bound only to `127.0.0.1`;
   from another machine, ports 18000, 18233, 8000 and 8233 on the public IP are closed.
6. On a run page, "View in Temporal" opens the run on the Temporal hostname.
7. With prod running, `just dev` and `just web` start on the same server with no clashes.
8. `git status` does not list `.env.prod`.
9. The prod stack keeps working with `CLOUDFLARE_TUNNEL_TOKEN` removed from `.env`.
