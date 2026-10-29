# syntax=docker/dockerfile:1
FROM node:24-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 STATIC_DIR=/app/web/dist
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src
RUN uv sync --frozen --no-dev
COPY --from=web /web/dist ./web/dist
RUN useradd --system --uid 10001 --no-create-home app
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/builds', timeout=4)"]
# Migrate, then serve. exec so uvicorn is PID 1 and gets SIGTERM.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn tracker.server:app --host 0.0.0.0 --port 8000 --no-server-header --proxy-headers --forwarded-allow-ips='*'"]
