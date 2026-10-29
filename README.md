# wardogs-patch-tracker

History, search and alerts for Wardogs game data across builds. It ingests the per-build diffs that
`wardogs-data-docs` produces (`update_pipeline.py diff --full` → `season_diff.py`) into Postgres.

## Setup

```
docker compose up -d                       # Postgres 16 on 127.0.0.1:5433
uv sync
export DATABASE_URL=postgresql+psycopg://tracker:tracker@127.0.0.1:5433/tracker
uv run alembic upgrade head
uv run pytest                              # creates and migrates its own tracker_test database
```

## Ingest a build

```
uv run tracker ingest --label s3-26000000 --from s2-cl516586 \
    --diff E:/wardogs_builds/s3-26000000/season-diff \
    --manifest E:/wardogs_builds/s3-26000000/manifest.json --released 2026-11-20
```

- One transaction per build. Re-running a label replaces its changes; metadata options you leave out keep their stored value.
- After loading, the command recounts the stored build the way `season_diff.py` does and compares every total and
  per-area count with `index.json`. On any mismatch it rolls back and exits 1.
- Moved assets (`old_path`) keep their id, so field history follows them; the old path is kept in `asset_alias`.
- Labels come from the command line: the `from`/`to` header in `index.json` is not used.

## Schema

`build` (with `prev_build_id`), `asset` (current path), `asset_alias`, `asset_status` (per build: area, status, noise
counts), `change` (field path, op, before/after as `jsonb`), `build_area` (counts from `index.json`). Noise rows are not
stored; `season_diff.py` owns that classification. Migrations are in `migrations/`; `alembic check` must report no drift.

## Roadmap

1. ~~Schema and ingest~~
2. ~~API (`docs/API.md`) and the build diff view~~
3. ~~Field history across builds and search~~ (design: `docs/DESIGN.md`)
4. Public deployment; Discord OAuth login for subscriptions to assets, areas or field patterns, delivered by Discord
   webhook after each ingest

## Run the API

```
export DATABASE_URL=postgresql+psycopg://tracker:tracker@127.0.0.1:5433/tracker
uv run uvicorn tracker.api:app --port 8000
```

Routes are under `/api` (see `docs/API.md`); interactive docs at `/docs`.

## Run the web app

```
cd web
npm ci
npm run dev          # http://localhost:5173, proxies /api to 127.0.0.1:8000 (override with API_TARGET)
npm test             # Vitest
npm run build        # type-check and build to web/dist
```
