# Deploying

One Docker host (a VPS or similar), three containers behind Docker Compose:

```
internet -> caddy (80/443, automatic HTTPS) -> app (API + web, :8000) -> db (Postgres 16)
```

Only Caddy publishes ports. `app` and `db` are reachable only on the compose network. Everything is in `compose.prod.yaml`; settings are in `.env`.

## First deploy

1. **Host**: any Linux box with Docker Engine and the Compose plugin. Open ports 80 and 443 (and UDP 443 for HTTP/3).
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
   | `DATABASE_URL` | `postgresql+psycopg://tracker:<same password>@db:5432/tracker` (URL-encode special characters, or use hex) |
   | `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET` | from step 3 |
   | `COOKIE_SECURE` | `1` |
   | `APP_IMAGE` | optional tag to run, see Updating |
5. **Start**:
   ```
   docker compose -f compose.prod.yaml up -d --build
   docker compose -f compose.prod.yaml ps
   ```
   The app container runs `alembic upgrade head` on every start, then serves. Caddy requests the certificate on first request; watch `docker compose -f compose.prod.yaml logs caddy`.
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

Migrations run automatically on start. To keep a rollback target, tag before building:

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

## Notes

- **`--forwarded-allow-ips='*'`**: uvicorn trusts `X-Forwarded-*` headers from any peer so it sees the real client IP and `https` scheme. This is safe here only because `app` publishes no host port: the sole thing that can connect to it is Caddy on the compose network. If you ever publish port 8000, restrict it to Caddy's address or remove the flag.
- Local test without a domain: set `DOMAIN=localhost`, `PUBLIC_ORIGIN=https://localhost:8443`, `HTTP_PORT=8080`, `HTTPS_PORT=8443` in `.env`. Caddy issues an internal certificate (use `curl -k`).

## Hardening in place

- TLS everywhere, automatic certificates and renewal; HTTP redirects to HTTPS.
- `app` runs as a non-root user; Postgres and the app are not published to the host.
- `COOKIE_SECURE=1`; secrets only in `.env` (git- and image-excluded), never in images.
- HTML responses carry a CSP (`default-src 'self'`, Google Fonts and `cdn.discordapp.com` avatars allowed, `frame-ancestors 'none'`), `Referrer-Policy`, `X-Content-Type-Options` and `Permissions-Policy`.
- Static serving resists path traversal and lists no directories; `/api/*` never falls back to the web app.
- Container healthchecks and restart policy; `app` waits for a healthy database, Caddy for a healthy app.
- Server version header is off; responses are gzip/zstd compressed.

## Not in place

- **Rate limiting**: none is configured, so sign-in and API endpoints are unthrottled. Options: the Caddy `rate_limit` plugin (needs a custom Caddy build with `xcaddy`), or limit at the host or provider firewall. Decide before opening to the public.
- No automated backups, log shipping or monitoring.
- The image uses `ghcr.io/astral-sh/uv:latest` and unpinned base tags; pin digests if you need reproducible builds.
