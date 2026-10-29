# Deploying

One Docker host (a VPS or similar), three containers behind Docker Compose:

```
internet -> caddy (80/443, automatic HTTPS) -> app (API + web, :8000) -> db (Postgres 16)
```

Only Caddy publishes ports. `app` and `db` are reachable only on the compose network. Everything is in `compose.prod.yaml`; settings are in `.env`. A one-shot `migrate` service runs before `app` (see "Database roles"). For a Raspberry Pi behind Tailscale Funnel use `compose.pi.yaml` instead (see that section below).

## First deploy

1. **Host**: any Linux box with Docker Engine and the Compose plugin. Open ports 80 and 443, and UDP 443 (HTTP/3; compose publishes it).
2. **DNS**: create an `A` record (and `AAAA` if the host has IPv6) for your hostname, for example `tracker.example.com`, pointing at the host. Caddy can only get a certificate once this resolves to the host.
3. **Discord application** (for sign-in): at <https://discord.com/developers/applications> create an application. Under OAuth2 add the redirect URI `https://<domain>/api/auth/callback`. The scope used is `identify`. Copy the client ID and the client secret.
4. **Get the code** onto the host (`git clone`), then in the repo root:
   ```
   cp deploy/.env.example .env
   chmod 600 .env
   ```
   Edit `.env`:

   | Variable | Meaning |
   |---|---|
   | `DOMAIN` | the hostname, no scheme |
   | `PUBLIC_ORIGIN` | `https://<domain>`, no trailing slash |
   | `POSTGRES_PASSWORD` | long random string (`openssl rand -hex 24`) |
   | `MIGRATE_DATABASE_URL` | owner role: `postgresql+psycopg://tracker:<POSTGRES_PASSWORD>@db:5432/tracker`. Only the `migrate` service gets it. |
   | `DATABASE_URL` | app role: `postgresql+psycopg://tracker_app:<another random password>@db:5432/tracker` (use hex, so nothing needs URL-encoding) |
   | `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET` | from step 3 |
   | `COOKIE_SECURE` | `1` |
   | `APP_IMAGE` | optional tag to run, see Updating |
5. **Start**:
   ```
   docker compose -f compose.prod.yaml up -d --build
   docker compose -f compose.prod.yaml ps
   ```
   `migrate` runs `alembic upgrade head` and (re)creates the `tracker_app` role on every start, then `app` serves. The app refuses to start while `change-me` is left in a URL, or when `PUBLIC_ORIGIN` is unset or not https. Caddy requests the certificate on first request; watch `docker compose -f compose.prod.yaml logs caddy`.
6. Open `https://<domain>/`.

## Loading data

The container has no data. Copy a `season_diff.py` output folder (with `index.json`) to the host, then ingest it by mounting it read-only into a one-off container:

```
docker compose -f compose.prod.yaml run --rm --no-deps \
  -v /path/on/host/season-diff:/diff:ro app \
  tracker ingest --label s2-cl516586 --from season1 --released 2026-10-15 --diff /diff
```

(`exec` works too if the folder is already inside the container, for example via `docker cp`.) Then send the alert for that build:

```
docker compose -f compose.prod.yaml exec app tracker notify --label s2-cl516586
```

`tracker notify` is added by the delivery work; the command above matches its `--label` option. Ingest the next build the same way, with `--from` set to the previous label.

## Updating

```
git pull
docker compose -f compose.prod.yaml build
docker compose -f compose.prod.yaml up -d
```

Migrations run automatically (the `migrate` service); running the image without compose does not migrate. To keep a rollback target, tag before building:

```
docker tag wardogs-patch-tracker:latest wardogs-patch-tracker:previous
```

## Backup and restore

Backup (custom format, safe while running):

```
docker compose -f compose.prod.yaml exec -T db pg_dump -U tracker -Fc tracker > backup-$(date +%F).dump
```

Restore into the running stack (replaces the data):

```
docker compose -f compose.prod.yaml stop app
docker compose -f compose.prod.yaml exec -T db psql -U tracker -d postgres -c 'DROP DATABASE tracker WITH (FORCE)' -c 'CREATE DATABASE tracker'
docker compose -f compose.prod.yaml exec -T db pg_restore -U tracker -d tracker --no-owner < backup-YYYY-MM-DD.dump
docker compose -f compose.prod.yaml start app
```

Automate the backup with cron and copy the dumps off the host; the `pgdata` volume is the only copy otherwise. The restore path was tested against this stack (21,976 change rows round-tripped).

## Rollback

Migrations only move forward, so a rollback that crosses a schema change needs the matching data:

1. Take a backup first (above), before every update.
2. Set `APP_IMAGE=wardogs-patch-tracker:previous` in `.env` and `docker compose -f compose.prod.yaml up -d --no-build`.
3. If the newer release applied a migration the old image does not know, restore the pre-update dump (above) before starting the old image.

## Database roles

Two roles, two URLs:

- `tracker` (owner, `POSTGRES_USER`, `MIGRATE_DATABASE_URL`): runs migrations. Used only by the `migrate` service, so the `app` container's environment never contains it. It is the Postgres bootstrap superuser, so treat its password as the root secret.
- `tracker_app` (`DATABASE_URL`): the API, `tracker ingest` and `tracker notify` all need only data access. It has SELECT/INSERT/UPDATE/DELETE on the tables, USAGE on the sequences, and the same on future tables through default privileges. It cannot create, alter or drop anything, has no `TRUNCATE`, cannot touch `alembic_version`, and is not a superuser.

`deploy/db_setup.py` creates or refreshes the role on every deploy (idempotent; a password change in `.env` applies on the next `up`). It runs in a startup service rather than `docker-entrypoint-initdb.d` because init scripts run only on an empty volume, so they would never reach an existing database. A new migration needs no grant changes: tables made by the owner are covered by the default privileges.

`docker compose run app tracker ingest ...` and `exec app tracker notify ...` use `DATABASE_URL`, so they run as `tracker_app`.

Upgrading an existing deployment: add `MIGRATE_DATABASE_URL` (the old owner URL) and change `DATABASE_URL` to `tracker_app` with a new password, then `up -d`.

## Updating pinned images

All images are pinned to exact tags: `node`, `python` and `ghcr.io/astral-sh/uv` in the `Dockerfile`, `postgres` and `caddy` in `compose.prod.yaml` and `compose.pi.yaml`. All are multi-arch (amd64 and arm64). To update: pick the new tag (Docker Hub tags page; `docker buildx imagetools inspect <image:tag>` should list `linux/arm64`), change it in every file (`grep -rn "postgres:16\|caddy:2\|python:3\|node:24\|astral-sh/uv:" Dockerfile compose*.yaml`), rebuild and run the tests. Minor Postgres updates (16.x) are safe; a new major needs a dump and restore. For reproducible builds you can also append a digest (`image:tag@sha256:...`, from `imagetools inspect`), at the cost of updating it by hand.

## Notes

- **Forwarded headers**: Caddy sets `X-Forwarded-For` to the single client address it sees (anything the client sent is dropped). uvicorn trusts `X-Forwarded-*` only from `172.29.57.0/24`, the fixed `edge` network that holds just `caddy` and `app` (`FORWARDED_ALLOW_IPS` in the compose files). If that subnet clashes with something on your host, change it in both places. Never publish port 8000 to the host.
- `--limit-concurrency 64`: uvicorn answers 503 beyond 64 simultaneous connections/requests (Caddy's idle keep-alive connections count). Raise it if you see 503s under real load.
- Local test without a domain: set `DOMAIN=localhost`, `PUBLIC_ORIGIN=https://localhost:8443`, `HTTP_PORT=8080`, `HTTPS_PORT=8443` in `.env`. Caddy issues an internal certificate (use `curl -k`).
- **Restart policy**: containers use `restart: unless-stopped`, which restarts a container that exits (crash, OOM kill) and starts it again after a reboot. Docker does **not** restart a container that is only `unhealthy`; healthchecks gate start order and show in `docker compose ps`. For a hung service run `docker compose restart app`, or add a watchdog that watches health.
- The API docs (`/docs`, `/redoc`, `/openapi.json`) are not served in production; they exist only in dev (`uvicorn tracker.api:app`).

## Hardening in place

- TLS everywhere, automatic certificates and renewal; HTTP redirects to HTTPS; HTTP/3 (UDP 443 is published).
- Every response from the app carries `Strict-Transport-Security`, `X-Content-Type-Options`, `Cross-Origin-Opener-Policy`, `Referrer-Policy` and `X-Frame-Options`; HTML adds a CSP (`default-src 'self'`, Google Fonts and `cdn.discordapp.com` avatars allowed, `frame-ancestors 'none'`, `object-src 'none'`, `upgrade-insecure-requests`) and `Permissions-Policy`. Caddy adds the same headers only when the app did not.
- Caddy: 64 KB request body cap on every path (413 beyond it), `read_header 10s`, `read_body 30s`, `idle 2m` timeouts.
- The uvicorn access log drops query strings, so OAuth `code`/`state` values never reach the logs.
- The app refuses to start with a `change-me` URL or a missing or non-https `PUBLIC_ORIGIN` (`http://localhost...` only with `COOKIE_SECURE=0`).
- `app` and `caddy` run with a read-only root filesystem (`/tmp` is a small tmpfs), all capabilities dropped (Caddy keeps `NET_BIND_SERVICE`), `no-new-privileges`, and memory and process limits. `db` has `no-new-privileges` and a memory limit. `app` runs as a non-root user.
- The app uses a DML-only database role (see "Database roles"); `db` is on an internal network with no outside access.
- `COOKIE_SECURE=1`; secrets only in `.env` (git- and image-excluded), never in images.
- Static serving resists path traversal and lists no directories; `/api/*` never falls back to the web app.
- Container healthchecks and restart policy; `app` waits for `migrate`, Caddy for a healthy app.
- Server version header is off; responses are gzip/zstd compressed.
- Images pinned to exact tags.

## Not in place

- **Rate limiting at the proxy**: Caddy has none (it needs a custom build with `xcaddy`). The app has its own limits in code; for more, limit at the host or provider firewall.
- No automated backups, log shipping or monitoring.
- Image digests are not pinned (tags only).

## Raspberry Pi with Tailscale Funnel

Tailscale Funnel publishes a service from your tailnet on the public internet at `https://<host>.<tailnet>.ts.net`, with Tailscale terminating TLS. No router port forwarding, no domain, no Let's Encrypt. Use `compose.pi.yaml`: Caddy listens on plain http at `127.0.0.1:8080` only, and Funnel forwards to it.

```
internet -> Tailscale relay (TLS) -> tailscaled on the Pi -> 127.0.0.1:8080 caddy -> app -> db
```

1. **OS**: 64-bit Raspberry Pi OS (or Ubuntu) on a Pi 4 or 5 with 4 GB or more. The 32-bit OS cannot run the arm64 Postgres and Caddy images. Check with `uname -m` (`aarch64`).
2. **Docker**: install Docker Engine and the Compose plugin from Docker's apt repository (<https://docs.docker.com/engine/install/debian/>). Every image used is multi-arch and has a `linux/arm64` manifest.
3. **Tailscale**: install it (<https://tailscale.com/download/linux>), then `sudo tailscale up`. In the admin console enable HTTPS certificates (DNS page) and make sure your policy lets this node use Funnel (the `funnel` node attribute).
4. **Configure**: `cp deploy/.env.example .env`, `chmod 600 .env`. Set `PUBLIC_ORIGIN=https://<host>.<tailnet>.ts.net` (see `tailscale status`), the two database URLs and passwords, the Discord keys and `COOKIE_SECURE=1`. `DOMAIN` is not used.
5. **Start**: `docker compose -f compose.pi.yaml up -d --build` (the first build on a Pi takes a while). Check `curl -i http://127.0.0.1:8080/api/builds`.
6. **Expose**: `sudo tailscale funnel --bg 8080`. Funnel listens externally only on 443, 8443 and 10000; the number you pass is the local port. Verify with `tailscale funnel status` and from another network: `https://<host>.<tailnet>.ts.net/`.
7. **Discord**: add the redirect URI `https://<host>.<tailnet>.ts.net/api/auth/callback` to the application (step 3 of "First deploy").
8. **Only this service on Funnel.** `tailscale funnel` exposes just the port you name. Leave SSH and every other Pi service off Funnel: never run `tailscale funnel` for them, keep them bound to localhost or the tailnet, and keep SSH on key authentication. Check that `tailscale funnel status` lists only 8080.
9. **Backups off the Pi.** The Pi's storage is otherwise the only copy of the data. Run the `pg_dump` from "Backup and restore" from cron (use `-f compose.pi.yaml`) and copy the dump to another machine or object storage (`rclone`, `scp`, a NAS). Test a restore.
10. **Storage**: Postgres writes constantly, and SD cards wear out and can corrupt on power loss. Prefer a USB SSD for the OS or at least the Docker data root (`/var/lib/docker`); if you stay on an SD card, use a high-endurance card and keep frequent off-Pi backups.

### Client IP behind Funnel

The in-app rate limiter keys on the address uvicorn reports, so the real visitor address has to survive the hops: Funnel (on the host) -> Docker port mapping -> Caddy -> uvicorn.

- Funnel/Serve's reverse proxy (tailscaled, `ipn/ipnlocal/serve.go`) sets `X-Forwarded-For` to the visitor's source address (taken from the ingress node's `Tailscale-Ingress-Src` header), plus `X-Forwarded-Host` and, over TLS, `X-Forwarded-Proto: https`. For Funnel requests it also sets `Tailscale-Funnel-Request: ?1` and omits the `Tailscale-User-*` identity headers. It uses `Header.Set`, so a value sent by the visitor is replaced.
- `Caddyfile.pi` sets `trusted_proxies static private_ranges` and `client_ip_headers X-Forwarded-For`. The Funnel connection reaches Caddy through Docker's port mapping, so Caddy sees it from the bridge gateway (a private address) and trusts it. Caddy then forwards a single `X-Forwarded-For: <client>` upstream, and uvicorn trusts only the `edge` subnet (`172.29.57.0/24`), so it reports the visitor as the client.
- The port is published on `127.0.0.1` only, so the only peers that can set the header are Funnel and processes on the Pi itself. Any local process could spoof a client address; keep untrusted code off the host.

Verified here: Caddy's and uvicorn's trust handling. With the Pi stack up, `curl -H 'X-Forwarded-For: 203.0.113.9' http://127.0.0.1:8080/api/builds` appears in the app's access log as client `203.0.113.9`; in the standard stack, where Caddy does not trust the sender, a spoofed header is ignored and the log shows the real peer.

Not verified (no Funnel node available): that Funnel sends the header as the code reads. That is inferred from Tailscale's source (`serve.go`); the user documentation does not mention it. After your first Funnel request, check `docker compose -f compose.pi.yaml logs app`: the client in the access log must be your public address, not `172.29.57.1`. If it shows the gateway, Funnel did not send the header and the limiter would see one shared address.
