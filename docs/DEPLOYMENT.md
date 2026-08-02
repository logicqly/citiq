# Citiq deployment (Railway)

Project `bubbly-determination`, environment `production`, building from
GitHub `logicqly/citiq`.

## Topology

Five services, which is the Railway **trial** plan's cap per project:

| Service | Source | Root dir | Dockerfile | Public? |
|---|---|---|---|---|
| `postgres` | `ghcr.io/railwayapp-templates/postgres-ssl:16` | | | no |
| `redis` | `redis:7-alpine` | | | no |
| `api` | repo | `/api` | `Dockerfile.combined` | no |
| `admin-web` | repo | `/admin-frontend` | `Dockerfile` | yes |
| `client-web` | repo | `/web` | `Dockerfile.prod` | yes |

Only the two frontends hold public domains, because the trial plan allows
exactly two. The API stays on the private network: each frontend's nginx
proxies `/api/*` to `http://api.railway.internal:8000`, and the browser is
handed `VITE_API_URL=/api` so every call is same-origin. That also means CORS
is not on the critical path in this topology.

## The collapse, and what it costs

The intended architecture is **seven** services: `admin-api`, `client-api` and
`worker` split apart so that each process holds only the database credential it
needs. `client-api` in particular gets `DATABASE_URL_APP` and nothing else, so
a compromise of the client-facing surface cannot read another tenant's data or
run migrations.

Five service slots cannot hold seven services, so `api` currently runs
`SERVICE_ROLE=combined`: admin routes, client routes, `/v1` and the inline
scheduler in one process, holding all three credentials at once. **The
process-level isolation is gone.**

What survives is the database-level guarantee. Client routes still go through
the `citiq_app` engine (`DATABASE_URL_APP`), which is subject to Row Level
Security, so tenant separation is still enforced by PostgreSQL and not by
application code. That guarantee depends entirely on `DATABASE_URL_APP`
pointing at `citiq_app`. Point it at the superuser and nothing separates
tenants at all.

**To restore the split** once the plan allows more services: create
`client-api` (`Dockerfile.client-api`, `DATABASE_URL_APP` only) and `worker`
(`Dockerfile.worker`, `DATABASE_URL_ADMIN` only), switch `api` to
`Dockerfile.admin-api` with `SERVICE_ROLE=admin`, and drop `DATABASE_URL_APP`
from it. All three Dockerfiles are already in the repo and current.

## Database roles

Migration `0011` creates two login roles and enables RLS:

- `citiq_admin` — `BYPASSRLS`. Used by admin routes and the scheduler.
- `citiq_app` — subject to RLS. Used by client routes.

It creates them **without passwords**, because a migration is committed to the
repository and must not carry credentials. `app/scripts/bootstrap_roles.py`
fills them in from `CITIQ_APP_PASSWORD` and `CITIQ_ADMIN_PASSWORD`, and the API
container runs it on every boot right after `alembic upgrade head`. It is
idempotent, and skips any role whose variable is unset (which is why local
docker-compose, where `scripts/init-db-roles.sql` already sets dev passwords,
needs no extra configuration).

These two variables must always match the passwords embedded in
`DATABASE_URL_ADMIN` and `DATABASE_URL_APP`. Rotating a role password means
changing both places together.

## Variables

`api`:

| Variable | Notes |
|---|---|
| `SERVICE_ROLE` | `combined` |
| `SCHEDULER_ENABLED` | `true` (inline scheduler; there is no worker service) |
| `PORT` | `8000` |
| `DATABASE_URL` | superuser, used only for migrations |
| `DATABASE_URL_ADMIN` | `citiq_admin` |
| `DATABASE_URL_APP` | `citiq_app` — keep it this way, see above |
| `CITIQ_ADMIN_PASSWORD`, `CITIQ_APP_PASSWORD` | consumed by `bootstrap_roles` |
| `REDIS_URL` | `redis://:<password>@redis.railway.internal:6379` |
| `JWT_SECRET_KEY` | generated per environment |
| `AUDIT_API_KEYS` | `label:key`, comma-separated; empty disables `/v1` (fail closed) |
| `ADMIN_FRONTEND_URL`, `CLIENT_FRONTEND_URL` | the two public domains |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `PERPLEXITY_API_KEY`, `GEMINI_API_KEY` | **placeholders (`REPLACE_ME`) until real keys are set** |

`admin-web` and `client-web`:

| Variable | Value |
|---|---|
| `PORT` | `80` |
| `API_HOST` | `http://api.railway.internal:8000` |
| `VITE_API_URL` | `/api` (baked into the bundle at build time) |
| `VITE_DASHBOARD_URL` | *(admin-web only)* the client-web URL, shown to staff when issuing credentials |

Both `VITE_*` values are build-time, not runtime: changing either needs a
rebuild, not just a restart.

## Frontend to API routing, and why it uses a resolver

Both frontends reach the API through their own nginx, over the private
network, so the API needs no public domain. Two details make that work:

- `proxy_pass` goes through a **variable** (`set $api_upstream ${API_HOST};`)
  rather than a literal hostname. nginx resolves a literal upstream once, when
  it parses the config, and exits with `host not found in upstream` if that
  lookup fails. Since `api.railway.internal` only exists while the API service
  is running, a literal would make both frontends crash-loop whenever the API
  is down or has not started yet, including on the very first deploy. A
  variable defers the lookup to request time.
- Deferring the lookup requires a `resolver` directive.
  `docker-entrypoint.d/19-resolvers.envsh` derives it from `/etc/resolv.conf`
  and exports `NGINX_LOCAL_RESOLVERS` before envsubst runs. The image ships an
  equivalent script, but it is gated behind `NGINX_ENTRYPOINT_LOCAL_RESOLVERS`
  and silently does nothing when that is unset, which would leave the literal
  `${NGINX_LOCAL_RESOLVERS}` in the config and fail the parse. Ours is
  unconditional. It brackets IPv6 nameservers, which is the normal case on
  Railway's private network.

The tradeoff is that an unreachable API now yields a 502 from nginx instead of
a container that refuses to start. That is the behaviour you want: the SPA
still loads and only API calls fail.

### `API_HOST` must be a reference, not a literal

**A service's private domain is pinned at creation and does not follow
renames.** The API service was seeded as `citiq`, then renamed to `admin-api`
and finally to `api`, and its `RAILWAY_PRIVATE_DOMAIN` stayed
`citiq.railway.internal` throughout. Hardcoding `api.railway.internal` produced
`could not be resolved (3: Host not found)` and a 502 on every proxied call,
with nothing in the service list hinting at the mismatch.

Both frontends therefore set:

```
API_HOST=http://${{api.RAILWAY_PRIVATE_DOMAIN}}:8000
```

Railway resolves that reference at deploy time, so the value tracks whatever
the API's private domain actually is. Check it with the service's
`RAILWAY_PRIVATE_DOMAIN` variable rather than assuming it matches the service
name.

## Cold-start order

`api` must reach a healthy state before the frontends are useful, because it
owns migrations and the role bootstrap. On a completely fresh database:

1. `postgres` and `redis` come up.
2. `api` runs `alembic upgrade head`, then `bootstrap_roles`, then serves.
3. Create the first admin user: `python -m app.cli create-admin --email ... `
4. Frontends proxy to `api` over the private network.

## Admin console basic auth

`admin-web` sits behind nginx HTTP basic auth in front of the JWT layer. The
credentials live in the committed `admin-frontend/.htpasswd`. Regenerate with
`htpasswd -nb admin <new-password>` and redeploy; the committed default should
not survive first launch.
