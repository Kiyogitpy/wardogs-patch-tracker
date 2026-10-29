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

# Caddy serves the static web app itself (cheap), and proxies only /api to the app.
FROM caddy:2.11.7 AS caddy
COPY --from=web /web/dist /srv
# Pick up Alpine security fixes published since the pinned tag was built (curl, brotli...).
RUN apk upgrade --no-cache
# Non-root: the named volumes copy this ownership when first created. The caddy binary carries
# cap_net_bind_service as a file capability; compose adds it back to the bounding set.
RUN mkdir -p /data/caddy /config/caddy && chown -R 65532:65532 /data /config
USER 65532:65532

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
