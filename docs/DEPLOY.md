# Deploying

One Docker host (a VPS or similar), three containers behind Docker Compose:

```
internet -> caddy (80/443, automatic HTTPS) -> app (API + web, :8000) -> db (Postgres 16)
```

Only Caddy publishes ports. `app` and `db` are reachable only on the compose network. Caddy serves the web app's static files itself (a copy of `web/dist` baked into the Caddy image) and proxies only `/api/*` to the app. Everything is in `compose.prod.yaml`; settings are in `.env`. A one-shot `migrate` service runs before `app` (see "Database roles"). For a Raspberry Pi behind Tailscale Funnel use `compose.pi.yaml` instead (see that section below).

## First deploy

1. **Host**: any Linux box with **Docker Engine 28 or newer** and the Compose plugin (`docker version`). Engine 28 stopped exposing container ports to the local network through direct routing and keeps ports published on `127.0.0.1` reachable from the host only; older engines let neighbours on the LAN reach what you meant to keep private. Open exactly 80/tcp, 443/tcp and 443/udp (HTTP/3; compose publishes it), and set up the firewall as described in "Firewall" below. The server files are `compose.prod.yaml` and `compose.pi.yaml`; a bare `docker compose up` does nothing (the dev database lives in `compose.dev.yaml`).
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
( umask 077; docker compose -f compose.prod.yaml exec -T db pg_dump -U tracker -Fc tracker > backup-$(date +%F).dump )
chmod 600 backup-*.dump
```

**A dump contains secrets**: webhook URLs (a webhook URL is a credential: anyone holding it can post to that channel), Discord user ids and session data. Hence `umask 077` (the file is created owner-only; a plain `>` would make it readable by everyone under the default umask) and `chmod 600`. Keep dumps out of shared directories, and encrypt every copy that leaves the host, for example with age (`age -r <public-key> -o backup.dump.age backup.dump`, decrypt with `age -d -i key.txt backup.dump.age`) or gpg (`gpg --encrypt --recipient <you> backup.dump`). Keep the private key somewhere other than the backup target, and delete any plaintext copy made only for the upload.

Restore into the running stack (replaces the data):

```
docker compose -f compose.prod.yaml stop app
docker compose -f compose.prod.yaml exec -T db psql -U tracker -d postgres -c 'DROP DATABASE tracker WITH (FORCE)' -c 'CREATE DATABASE tracker'
docker compose -f compose.prod.yaml exec -T db pg_restore -U tracker -d tracker --no-owner < backup-YYYY-MM-DD.dump
docker compose -f compose.prod.yaml start app
```

Automate the backup with cron and copy the (encrypted) dumps off the host; the `pgdata` volume is the only copy otherwise. The restore path was tested against this stack (21,976 change rows round-tripped).

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

`tracker_app` extras, all set by `db_setup.py`:

- No `TEMP` privilege (revoked from `PUBLIC` and from the role): it cannot create temporary tables.
- `statement_timeout = 5s` as the role default, so a runaway query in `tracker ingest`/`notify` or an ad-hoc session is cancelled. A session may still `SET statement_timeout` itself (the API sets its own value), so this guards against accidents, not against a compromised app.
- No per-role `CONNECTION LIMIT` (`-1`): on Postgres 16.15 a burst of connections past a role's limit crash-restarted
  the whole server, so a limit would itself be a denial of service. Connections are bounded by the API pool (15), the
  CLI runs and the statement timeouts instead.
- The script refuses empty, whitespace-only and placeholder passwords (`change-me`, `changeme`, `password`, any case) for both roles, and a role name equal to the owner, `postgres` or `pg_*`.
- The password is sent as a SCRAM verifier, and the session turns statement logging off (`log_min_error_statement = panic`, `log_statement = none`), so a failed `CREATE`/`ALTER ROLE` cannot leave the plaintext in the Postgres log.

`docker compose run app tracker ingest ...` and `exec app tracker notify ...` use `DATABASE_URL`, so they run as `tracker_app`.

Upgrading an existing deployment: add `MIGRATE_DATABASE_URL` (the old owner URL) and change `DATABASE_URL` to `tracker_app` with a new password, then `up -d`.

## Firewall

Docker publishes ports by writing its own NAT and forward rules, which are evaluated before `ufw`/`firewalld` rules. `ufw deny 5432` does nothing for a published port, and the ports compose publishes are open whatever `ufw status` says. Two things do work:

1. **Publish only what you want, on the address you want.** Compose here publishes only Caddy: `80`, `443/tcp` and `443/udp` on the VPS (`compose.prod.yaml`), `127.0.0.1:8080` on the Pi. `app` and `db` publish nothing; keep it that way (never add `ports:` to them). To bind to one address instead of all, write `"203.0.113.10:443:443"` in the `ports` entries.
2. **Filter in the `DOCKER-USER` chain**, the one iptables chain Docker never rewrites and evaluates before its own rules. Example that lets only the three public ports through to containers and drops other new inbound connections (replace `eth0` with the public interface; persist with `iptables-persistent` or a systemd unit):

   ```
   iptables -I DOCKER-USER -i eth0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
   iptables -A DOCKER-USER -i eth0 -p tcp -m multiport --dports 80,443 -j ACCEPT
   iptables -A DOCKER-USER -i eth0 -p udp --dport 443 -j ACCEPT
   iptables -A DOCKER-USER -i eth0 -j DROP
   ```

   DOCKER-USER sees the destination port **after** DNAT, that is the container's port (`80`, `443`), which is what the rules above match even if you change `HTTP_PORT`/`HTTPS_PORT`. Add the same with `ip6tables` if Docker IPv6 is on.

**VPS**: open inbound 80/tcp, 443/tcp and 443/udp, plus SSH (ideally from your address only). Nothing else, in particular not 5432 or 8000.

**Raspberry Pi**: nothing inbound from the internet. Funnel makes an outbound connection to Tailscale. Allow SSH only from the LAN or the tailnet (for example `ufw allow from 192.168.1.0/24 to any port 22`). Port 8080 is bound to `127.0.0.1`, so the LAN cannot reach it on Engine 28 or newer.

Verify from a different machine: `nmap -Pn -p 22,80,443,5432,8000,8080 <host>` must show only the ports you meant to open; on the host, `ss -ltnp` must list only `0.0.0.0:80`/`:443` (VPS) or `127.0.0.1:8080` (Pi) from Docker.

## Updating pinned images

All images are pinned to exact tags: `node`, `python`, `caddy` and `ghcr.io/astral-sh/uv` in the `Dockerfile`, `postgres` in `compose.prod.yaml` and `compose.pi.yaml`. The app image's last stage holds only the finished virtualenv and the app: no `uv`, `pip`, `setuptools` or `wheel` (check with `docker scout cves <image>`). The Caddy image is built from the pinned `caddy` tag with the web app copied in (`docker compose build caddy`). All are multi-arch (amd64 and arm64). To update: pick the new tag (Docker Hub tags page; `docker buildx imagetools inspect <image:tag>` should list `linux/arm64`), change it in every file (`grep -rn "postgres:16\|caddy:2\|python:3\|node:24\|astral-sh/uv:" Dockerfile compose*.yaml`; `compose.dev.yaml` uses the floating `postgres:16` on purpose), rebuild and run the tests. Minor Postgres updates (16.x) are safe; a new major needs a dump and restore. For reproducible builds you can also append a digest (`image:tag@sha256:...`, from `imagetools inspect`), at the cost of updating it by hand.

## Notes

- **Forwarded headers**: Caddy sets `X-Forwarded-For` to the single client address it sees (anything the client sent is dropped). uvicorn trusts `X-Forwarded-*` from exactly one address: Caddy's fixed address on the `edge` network (`CADDY_IP`, via `FORWARDED_ALLOW_IPS` in the compose files), not the whole subnet, because the subnet also contains the host gateway and any other container you attach. The network is defined once per file from `EDGE_SUBNET`, `EDGE_GATEWAY` and `CADDY_IP` (defaults `172.29.57.0/24`, `.1`, `.254` in `compose.prod.yaml`; `172.29.58.0/24`, `.1`, `.254` in `compose.pi.yaml`, so both can run on one host). The network definition and the trust setting read the same variables, so they cannot drift. If a subnet clashes with something on your host, set all three in `.env`, keeping `CADDY_IP` near the top of the subnet (Docker hands other containers addresses from the bottom). Never publish port 8000 to the host.
- **No `--limit-concurrency`**: uvicorn used to answer 503 beyond 64 simultaneous connections, which let 150 looped static GETs push the API and the healthcheck into 503s (measured: 8,246 of 27,543 static requests and 64 of 164 API probes got 503). It is gone. Load is bounded by the containers' `mem_limit` and `pids_limit` and by the app's per-IP limiter on `/api` (429), and the static files are served by Caddy, so a flood of them never reaches Python. The same flood now gives 0 503s.
- **Caddy runs as a non-root user** (uid 65532) with only `NET_BIND_SERVICE`. Its `caddy_data` and `caddy_config` volumes must be writable by that uid. New volumes get the right ownership from the image; on an existing deployment whose volumes were made by the old root image, run once: `docker compose -f compose.prod.yaml run --rm --no-deps -u 0 --entrypoint chown caddy -R 65532:65532 /data /config` (certificates are kept). With `DOMAIN=localhost` a "failed to install root certificate" log line is expected (a non-root process cannot touch the trust store); use `curl -k`.
- Local test without a domain: set `DOMAIN=localhost`, `PUBLIC_ORIGIN=https://localhost:8443`, `HTTP_PORT=8080`, `HTTPS_PORT=8443` in `.env`. Caddy issues an internal certificate (use `curl -k`).
- **Restart policy**: containers use `restart: unless-stopped`, which restarts a container that exits (crash, OOM kill) and starts it again after a reboot. Docker does **not** restart a container that is only `unhealthy`; healthchecks gate start order and show in `docker compose ps`. For a hung service run `docker compose restart app`, or add a watchdog that watches health.
- The API docs (`/docs`, `/redoc`, `/openapi.json`) are not served in production; they exist only in dev (`uvicorn tracker.api:app`).

## Hardening in place

- TLS everywhere, automatic certificates and renewal; HTTP redirects to HTTPS; HTTP/3 (UDP 443 is published).
- Every response from the app carries `Strict-Transport-Security`, `X-Content-Type-Options`, `Cross-Origin-Opener-Policy`, `Referrer-Policy` and `X-Frame-Options`; HTML adds a CSP (`default-src 'self'`, Google Fonts and `cdn.discordapp.com` avatars allowed, `frame-ancestors 'none'`, `object-src 'none'`, `upgrade-insecure-requests`) and `Permissions-Policy`. Caddy adds the same headers only when the app did not.
- Caddy: 64 KB request body cap on every path (413 beyond it); `read_header 10s`, `read_body 30s`, `idle 2m` timeouts on every listener, port 80 included.
- Every response Caddy writes itself carries the security headers: the 413, the http to https redirect, an unknown `Host` (404), error pages and the 405. `Server` and `Via` are removed.
- The uvicorn access log drops query strings, so OAuth `code`/`state` values never reach the logs.
- The app refuses to start with an empty or placeholder database password (`change-me`, `changeme`, `password`, any case), or unless `PUBLIC_ORIGIN` is exactly `https://host[:port]` (no path, query, fragment, user info or whitespace). `COOKIE_SECURE=0` is accepted only together with `http://localhost` or `http://127.0.0.1`, and that origin only with `COOKIE_SECURE=0`.
- `app` and `caddy` run with a read-only root filesystem (`/tmp` is a small tmpfs), all capabilities dropped (Caddy keeps `NET_BIND_SERVICE`), `no-new-privileges`, and memory and process limits. `db` has `no-new-privileges` and limits but a writable root filesystem (Postgres needs it). `app` and `caddy` run as non-root users. Every service logs with `json-file`, rotated at 3 files of 10 MB.
- The app uses a DML-only database role (see "Database roles"); `db` is on an internal network with no outside access.
- `COOKIE_SECURE=1`; secrets only in `.env` (git- and image-excluded), never in images.
- Static serving resists path traversal and lists no directories; `/api/*` never falls back to the web app.
- Container healthchecks and restart policy; `app` waits for `migrate`, Caddy for a healthy app.
- Server version header is off; responses are gzip/zstd compressed.
- Images pinned to exact tags, at the newest patch releases of `caddy` 2.11, `postgres` 16 and `python` 3.13 at the time of writing. The app image has no `uv`, `pip` or `setuptools`.

## Not in place

- **Rate limiting at the proxy**: Caddy has none (it needs a custom build with `xcaddy`). The app has its own limits in code; for more, limit at the host or provider firewall.
- No automated backups, log shipping or monitoring.
- Image digests are not pinned (tags only).
- The Caddy image still reports Go standard library and `golang.org/x/net` CVEs in `docker scout`, because they are compiled into the newest `caddy` release; they clear when upstream ships a rebuilt tag (bump the tag in the `Dockerfile`).

## Raspberry Pi with Tailscale Funnel

Tailscale Funnel publishes a service from your tailnet on the public internet at `https://<host>.<tailnet>.ts.net`, with Tailscale terminating TLS. No router port forwarding, no domain, no Let's Encrypt. Use `compose.pi.yaml`: Caddy listens on plain http at `127.0.0.1:8080` only, and Funnel forwards to it.

```
internet -> Tailscale relay (TLS) -> tailscaled on the Pi -> 127.0.0.1:8080 caddy -> app -> db
```

1. **OS**: 64-bit Raspberry Pi OS (or Ubuntu) on a Pi 4 or 5 with 4 GB or more. The 32-bit OS cannot run the arm64 Postgres and Caddy images. Check with `uname -m` (`aarch64`).
2. **Docker**: install **Docker Engine 28 or newer** and the Compose plugin from Docker's apt repository (<https://docs.docker.com/engine/install/debian/>); Debian's own `docker.io` package is usually older. Engine 28 is what keeps the loopback-published Caddy port (`127.0.0.1:8080`) off the LAN and blocks direct routing to container ports. Check `docker version`. Every image used is multi-arch and has a `linux/arm64` manifest.
3. **Tailscale**: install it (<https://tailscale.com/download/linux>), then `sudo tailscale up`. In the admin console enable HTTPS certificates (DNS page) and make sure your policy lets this node use Funnel (the `funnel` node attribute).
4. **Configure**: `cp deploy/.env.example .env`, `chmod 600 .env`. Set `PUBLIC_ORIGIN=https://<host>.<tailnet>.ts.net` (see `tailscale status`), the two database URLs and passwords, the Discord keys and `COOKIE_SECURE=1`. `DOMAIN` is not used.
5. **Start**: `docker compose -f compose.pi.yaml up -d --build` (the first build on a Pi takes a while). Check `curl -i http://127.0.0.1:8080/api/builds`.
6. **Expose**: `sudo tailscale funnel --bg 8080`. Funnel listens externally only on 443, 8443 and 10000; the number you pass is the local port. Verify with `tailscale funnel status` and from another network: `https://<host>.<tailnet>.ts.net/`.
7. **Discord**: add the redirect URI `https://<host>.<tailnet>.ts.net/api/auth/callback` to the application (step 3 of "First deploy").
8. **Only this service on Funnel.** `tailscale funnel` exposes just the port you name. Leave SSH and every other Pi service off Funnel, keep them bound to localhost or the tailnet, and keep SSH on key authentication. **Scope check, after every Tailscale change and before announcing the URL:** `tailscale funnel status` must show exactly one entry, `https://<host>.<tailnet>.ts.net (Funnel on)` proxying to `http://127.0.0.1:8080`, and nothing else; `tailscale serve status` shows the same set. Anything extra is public: remove it with `sudo tailscale funnel --https=<port> off`.
9. **Backups off the Pi.** The Pi's storage is otherwise the only copy of the data. Run the `pg_dump` from "Backup and restore" from cron (use `-f compose.pi.yaml`; keep the `umask 077`/`chmod 600`, the dump contains webhook URLs), encrypt it with age or gpg, and copy it to another machine or object storage (`rclone`, `scp`, a NAS). Test a restore. Also open nothing inbound on the Pi apart from SSH from the LAN or tailnet (see "Firewall").
10. **Storage**: Postgres writes constantly, and SD cards wear out and can corrupt on power loss. Prefer a USB SSD for the OS or at least the Docker data root (`/var/lib/docker`); if you stay on an SD card, use a high-endurance card and keep frequent off-Pi backups.

### Client IP behind Funnel

The in-app rate limiter keys on the address uvicorn reports, so the real visitor address has to survive the hops: Funnel (tailscaled on the host) -> Docker port mapping -> Caddy -> uvicorn.

- Caddy sees the Funnel connection coming from the `edge` network's gateway (`EDGE_GATEWAY`, `172.29.58.1` by default), because the host reaches the container through Docker's port mapping. `Caddyfile.pi` trusts **only that address** (`trusted_proxies static <gateway>/32`), not `private_ranges`: every other container on the host also has a private address, and a trusted peer may set the client address.
- `trusted_proxies_strict` makes Caddy walk `X-Forwarded-For` from the right and take the **rightmost untrusted entry**. Entries to its left are whatever the visitor sent and are never believed. Caddy then forwards a single `X-Forwarded-For: <client>` and uvicorn trusts only Caddy's own address (`CADDY_IP`).
- A connection from any other address (another container, even one on the same network) is untrusted, so its `X-Forwarded-For` is ignored and the app sees that container's own address.

What Funnel does with the header decides the result. From Tailscale's source (`ipn/ipnlocal/serve.go`) the Serve/Funnel proxy sets `X-Forwarded-For` to the visitor's address with `Header.Set`; the user documentation does not promise it, and no Funnel node was available to test. The three possibilities:

| Funnel does | Caddy's `client_ip` | Effect |
|---|---|---|
| Replaces the header (expected) | the visitor | correct, per-visitor limiting |
| Appends to what the visitor sent (`forged, visitor`) | the visitor (rightmost) | correct; the forged entry is ignored |
| Omits the header (visitor's own header passes through, or none) | the gateway if none was sent; **the visitor's forged value if one was sent** | no header: every visitor shares one limiter bucket (one noisy client can make everyone get 429); passed through: the limiter can be dodged by forging the header |

**First-request check (do this before sharing the URL).** From a network that is not yours (phone data, not Wi-Fi):

```
curl -s -o /dev/null https://<host>.<tailnet>.ts.net/api/builds
curl -s -o /dev/null -H 'X-Forwarded-For: 1.2.3.4' https://<host>.<tailnet>.ts.net/api/builds
docker compose -f compose.pi.yaml logs --tail 4 app
```

Both log lines must show **your public address** as the client. If you see `172.29.58.1` (or your `EDGE_GATEWAY`), Funnel sends no header: the app works but limits everyone together. If you see `1.2.3.4`, Funnel passes the visitor's header through unchanged: stop the funnel (`sudo tailscale funnel --https=443 off`) and tell the maintainers, the limiter is bypassable.

Remaining limit: anything that can open the loopback port from the host looks exactly like Funnel and may set the client address (any local process; on Docker Desktop even a container reaching `host.docker.internal`). On a Linux host with Engine 28 or newer other containers cannot reach a `127.0.0.1`-published port. Keep untrusted code off the Pi.

Verified here with the Pi stack (`-p wpt-fix2-pi`, `PI_PORT=58090`), reading the app's access log:

- from the trusted hop, `X-Forwarded-For: 203.0.113.9` -> client `203.0.113.9`;
- from the trusted hop, `X-Forwarded-For: 1.2.3.4, 10.0.0.1` -> client `10.0.0.1` (rightmost untrusted);
- from the trusted hop without the header -> client `172.29.58.1` (the gateway);
- from another container on the `edge` network straight to Caddy (`172.29.58.254:8080`), `X-Forwarded-For: 6.6.6.6` or `1.2.3.4, 10.0.0.1` -> client is that container's own address `172.29.58.3`;
- from a container straight to `app:8000` with a forged header -> client is the container's own address (uvicorn trusts only Caddy).
