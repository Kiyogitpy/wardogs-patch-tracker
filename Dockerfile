# syntax=docker/dockerfile:1
# Base images are pinned to exact tags (see "Updating pinned images" in docs/DEPLOY.md).
# Targets: `app` (default, last stage) and `caddy` (Caddy with the built web app baked in at /srv).
FROM node:24.22.2-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# Build stage: uv lives here only. The runtime stage gets the finished venv, not the tooling.
FROM python:3.13.18-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.0 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# The published caddy:2.11.7 binary was built with Go 1.26.8 and golang.org/x/net 0.59.0 (fixable critical
# and high CVEs). Rebuild the same Caddy version with a patched toolchain and patched modules, then drop the
# binary into the pinned runtime image below. Bump the three pins together (see docs/DEPLOY.md).
FROM caddy:2.11.7-builder AS caddy-build
ENV GOTOOLCHAIN=go1.26.10 CGO_ENABLED=0
RUN xcaddy build v2.11.7 --output /caddy       --replace golang.org/x/net=golang.org/x/net@v0.62.0       --replace github.com/go-jose/go-jose/v4=github.com/go-jose/go-jose/v4@v4.1.6       --replace github.com/go-chi/chi/v5=github.com/go-chi/chi/v5@v5.4.0

# Runtime filesystem: the pinned caddy:2.11.7 image with the rebuilt binary swapped in, the web app at
# /srv, Alpine fixes and the file capability. It is then copied into a fresh image (next stage), so the
# old binary does not stay in a lower layer where scanners would still report it.
FROM caddy:2.11.7 AS caddy-rootfs
COPY --from=caddy-build /caddy /usr/bin/caddy
COPY --from=web /web/dist /srv
# Pick up Alpine security fixes published since the pinned tag was built (curl, brotli...).
RUN apk upgrade --no-cache
# The caddy binary carries cap_net_bind_service as a file capability; compose adds it back to the bounding set.
RUN setcap cap_net_bind_service=+ep /usr/bin/caddy && /usr/bin/caddy version
# Non-root: the named volumes copy this ownership when first created.
RUN mkdir -p /data/caddy /config/caddy && chown -R 65532:65532 /data /config

# Caddy serves the static web app itself (cheap), and proxies only /api to the app.
# Same runtime settings as the upstream caddy image (env, ports, workdir, default command).
FROM scratch AS caddy
COPY --from=caddy-rootfs / /
ENV PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin CADDY_VERSION=v2.11.7     XDG_CONFIG_HOME=/config XDG_DATA_HOME=/data
EXPOSE 80 443 443/udp 2019
WORKDIR /srv
USER 65532:65532
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]

FROM python:3.13.18-slim AS app
# No uv, no pip, no setuptools in the final image (they carry their own CVEs and are not needed to run).
RUN python -m pip uninstall -y pip setuptools wheel 2>/dev/null; \
    rm -rf /usr/local/lib/python3.13/site-packages/pip* /usr/local/lib/python3.13/site-packages/setuptools* \
           /usr/local/lib/python3.13/site-packages/wheel* /usr/local/bin/pip* /usr/local/bin/wheel
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 STATIC_DIR=/app/web/dist
WORKDIR /app
COPY --from=build /app/.venv ./.venv
COPY alembic.ini ./
COPY migrations ./migrations
COPY deploy/db_setup.py ./deploy/db_setup.py
COPY --from=web /web/dist ./web/dist
RUN useradd --system --uid 10001 --no-create-home app
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/builds', timeout=4)"]
# Serve only: migrations and the DB role run in the separate `migrate` service (deploy/db_setup.py).
# FORWARDED_ALLOW_IPS (env, default 127.0.0.1) says which peer may set X-Forwarded-For: Caddy's address only.
# No --limit-concurrency: it turned a static-file flood into 503s for the API and the healthcheck.
# Load is bounded by the compose mem_limit/pids_limit and the app's own per-IP limiter on /api.
CMD ["uvicorn", "tracker.server:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header", "--proxy-headers"]
