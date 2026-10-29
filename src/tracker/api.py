"""Read-only JSON API over the ingested builds. See docs/API.md."""
import json
import math
import os
import time
from collections import OrderedDict, defaultdict
from functools import cache
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from psycopg import OperationalError
from psycopg.errors import QueryCanceled
from sqlalchemy import Engine, case, create_engine, exists, func, select
from sqlalchemy.exc import DBAPIError, TimeoutError as PoolTimeout

from tracker.db import asset, asset_alias, asset_status, build, build_area, change

COUNT_KEYS = ("added", "removed", "modified", "moved", "property_changes", "removed_values")

# A text filter. NUL is rejected here because Postgres text cannot hold it (the driver would raise a 500).
Filter = Annotated[str | None, Query(max_length=200, pattern=r"^[^\x00]*$")]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0, le=1_000_000)]

# Build order, defined once: newest first. Chronological is this order reversed (done in Python).
NEWEST_FIRST = (build.c.released_at.desc().nulls_last(), build.c.id.desc())
# Asset status order within a build, as season_diff.py lists them.
STATUS_ORDER = case({"modified": 0, "moved": 1, "added": 2, "removed": 3}, value=asset_status.c.status)

# redirect_slashes off: Starlette would build the redirect target from the Host header.
app = FastAPI(title="Wardogs patch tracker", redirect_slashes=False)

MAX_BODY = 64 * 1024  # bytes of request body accepted under /api
BUSY = {"detail": "the database is busy, try again shortly"}
POOL_TIMEOUT = 5  # seconds a request waits for a pooled connection
STATEMENT_TIMEOUT_MS = 3000  # per statement, API engine only (ingest and notify use their own engines)
# Token buckets per client IP: (requests per minute, burst). Tests patch these.
RATE_AUTH = (10, 5)
RATE_API = (120, 60)
MAX_BUCKETS = 10_000
BUCKET_IDLE = 600.0  # seconds without a request before a bucket is dropped
_buckets: OrderedDict[tuple[str, str], list[float]] = OrderedDict()  # key -> [tokens, last seen], oldest first
_now = time.monotonic


def _take(ip: str, group: str, per_minute: int, burst: int) -> float:
    """Spend one token; returns 0 when allowed, else the seconds until one is available."""
    now = _now()
    key = (ip, group)
    tokens, last = _buckets.pop(key, (float(burst), now))
    tokens = min(float(burst), tokens + (now - last) * per_minute / 60)
    allowed = tokens >= 1
    _buckets[key] = [tokens - 1 if allowed else tokens, now]  # re-inserted last: the dict is ordered by recency
    while _buckets and (len(_buckets) > MAX_BUCKETS or now - next(iter(_buckets.values()))[1] > BUCKET_IDLE):
        _buckets.popitem(last=False)
    return 0.0 if allowed else (1 - tokens) * 60 / per_minute


async def _reply(send, status: int, detail: str, headers: list[tuple[bytes, bytes]] = ()) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send({"type": "http.response.start", "status": status, "headers": [
        (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), *headers]})
    await send({"type": "http.response.body", "body": body})


class Guard:
    """Pure ASGI middleware, outermost, so it runs before auth and before any body is buffered: per-IP rate
    limit, then a cap on request bodies (Content-Length up front, streamed bytes for chunked requests)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not (path == "/api" or path.startswith("/api/")):
            return await self.app(scope, receive, send)
        ip = (scope.get("client") or ("unknown",))[0]
        per_minute, burst = RATE_AUTH if path.startswith("/api/auth/") else RATE_API
        wait = _take(ip, "auth" if path.startswith("/api/auth/") else "api", per_minute, burst)
        if wait:
            return await _reply(send, 429, "too many requests", [(b"retry-after", str(math.ceil(wait)).encode())])
        declared = dict(scope["headers"]).get(b"content-length", b"0")
        if declared.isdigit() and int(declared) > MAX_BODY:
            return await _reply(send, 413, "request body too large")

        seen = 0
        rejected = False

        async def counted_receive():
            nonlocal seen, rejected
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > MAX_BODY and not rejected:
                    rejected = True
                    await _reply(send, 413, "request body too large")
                if rejected:  # stop reading: the app sees the client go away
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message):
            if not rejected:
                await send(message)

        try:
            await self.app(scope, counted_receive, guarded_send)
        except Exception:
            if not rejected:
                raise


@app.middleware("http")
async def check_origin(request: Request, call_next):
    """CSRF defence for state-changing requests: Origin must equal PUBLIC_ORIGIN (unset fails closed); JSON bodies only."""
    path = request.url.path
    if (path == "/api" or path.startswith("/api/")) and request.method not in ("GET", "HEAD", "OPTIONS"):
        expected = os.environ.get("PUBLIC_ORIGIN", "").rstrip("/")
        if not expected or request.headers.get("origin") != expected:
            return JSONResponse({"detail": "bad origin"}, status_code=403)
        has_body = request.headers.get("content-length", "0") != "0" or "transfer-encoding" in request.headers
        media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if has_body and media_type != "application/json":
            return JSONResponse({"detail": "content type must be application/json"}, status_code=415)
    return await call_next(request)


app.add_middleware(Guard)  # added after check_origin, so it wraps it


@app.exception_handler(DBAPIError)
async def database_error(request: Request, exc: DBAPIError):
    """Timeouts, a full disk or shared memory, too many connections, a lost connection: busy, not a crash."""
    if isinstance(exc.orig, QueryCanceled):
        return JSONResponse({"detail": "query took too long, narrow the search"}, status_code=503)
    if isinstance(exc.orig, OperationalError):
        return JSONResponse(BUSY, status_code=503)
    raise exc


@app.exception_handler(PoolTimeout)
async def pool_exhausted(request: Request, exc: PoolTimeout):
    return JSONResponse(BUSY, status_code=503)


@cache
def get_engine() -> Engine:
    return create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True, pool_timeout=POOL_TIMEOUT,
                         connect_args={"options": f"-c statement_timeout={STATEMENT_TIMEOUT_MS}"})


def _build_json(row, counts: list[dict]) -> dict:
    return {
        "label": row.label,
        "prev_label": row.prev_label,
        "steam_buildid": row.steam_buildid,
        "released_at": row.released_at.isoformat() if row.released_at else None,
        "ingested_at": row.ingested_at.isoformat(),
        "totals": {k: sum(c.get(k, 0) for c in counts) for k in COUNT_KEYS},
    }


def _contains(column, value: str):
    """Case-insensitive substring match; `%`, `_` and `\\` in user input are literal."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return column.ilike(f"%{escaped}%", escape="\\")


def _select_build():
    prev = build.alias("prev")
    return (select(build, prev.c.label.label("prev_label"))
            .outerjoin(prev, prev.c.id == build.c.prev_build_id)
            .where(build.c.ingested_at.is_not(None)))


def _get_build(conn, label: str):
    # Postgres text cannot hold NUL, so no label containing one exists.
    row = None if "\x00" in label else conn.execute(_select_build().where(build.c.label == label)).first()
    if row is None:
        raise HTTPException(404, "build not found")
    return row


@app.get("/api/builds")
def list_builds():
    with get_engine().connect() as conn:
        rows = conn.execute(
            _select_build().order_by(*NEWEST_FIRST)
        ).all()
        counts = defaultdict(list)
        for build_id, c in conn.execute(select(build_area.c.build_id, build_area.c.counts)):
            counts[build_id].append(c)
    return [_build_json(r, counts[r.id]) for r in rows]


@app.get("/api/builds/{label}")
def get_build(label: str):
    with get_engine().connect() as conn:
        row = _get_build(conn, label)
        areas = conn.execute(
            select(build_area.c.area, build_area.c.title, build_area.c.counts)
            .where(build_area.c.build_id == row.id).order_by(build_area.c.position)
        ).all()
        n = func.count().label("n")
        types = conn.execute(
            select(asset.c.type, n).select_from(asset_status.join(asset, asset.c.id == asset_status.c.asset_id))
            .where(asset_status.c.build_id == row.id).group_by(asset.c.type).order_by(n.desc(), asset.c.type.collate("C"))
        ).all()
    return {
        **_build_json(row, [a.counts for a in areas]),
        "areas": [{"id": a.area, "title": a.title, "counts": a.counts} for a in areas],
        "types": [{"type": t.type, "assets": t.n} for t in types],
    }


@app.get("/api/builds/{label}/assets")
def list_assets(
    label: str,
    area: Filter = None,
    status: Literal["added", "removed", "modified", "moved"] | None = None,
    type: Filter = None,
    q: Filter = None,
    field: Filter = None,
    limit: Limit = 50,
    offset: Offset = 0,
):
    with get_engine().connect() as conn:
        build_id = _get_build(conn, label).id
        where = [asset_status.c.build_id == build_id]
        if area is not None:
            where.append(asset_status.c.area == area)
        if status is not None:
            where.append(asset_status.c.status == status)
        if type is not None:
            where.append(asset.c.type == type)
        if q:
            where.append(_contains(asset.c.name, q) | _contains(asset.c.path, q))
        field_match = None
        if field:
            field_match = _contains(change.c.field_path, field)
            where.append(exists().where(change.c.build_id == build_id, change.c.asset_id == asset.c.id, field_match))

        joined = asset_status.join(asset, asset.c.id == asset_status.c.asset_id)
        total = conn.execute(select(func.count()).select_from(joined).where(*where)).scalar_one()

        rows = conn.execute(
            select(asset.c.id, asset.c.path, asset.c.name, asset.c.type, asset_status.c.area, asset_status.c.status,
                   asset_alias.c.old_path, asset_status.c.paired_by, asset_status.c.exports, asset_status.c.note,
                   asset_status.c.noise)
            .select_from(joined.outerjoin(asset_alias, (asset_alias.c.build_id == asset_status.c.build_id)
                                          & (asset_alias.c.asset_id == asset_status.c.asset_id)))
            # "C" collation: code-point order, independent of the server locale (which ignores punctuation).
            .where(*where).order_by(STATUS_ORDER, func.lower(asset.c.name).collate("C"), asset.c.path.collate("C"))
            .limit(limit).offset(offset)
        ).all()

        changes = defaultdict(list)
        if rows:
            query = (select(change.c.asset_id, change.c.field_path, change.c.op, change.c.before, change.c.after)
                     .where(change.c.build_id == build_id, change.c.asset_id.in_([r.id for r in rows]))
                     .order_by(change.c.id))
            if field_match is not None:
                query = query.where(field_match)
            for c in conn.execute(query):
                changes[c.asset_id].append(
                    {"field_path": c.field_path, "op": c.op, "before": c.before, "after": c.after})

    return {
        "total": total,
        "items": [
            {"path": r.path, "name": r.name, "type": r.type, "area": r.area, "status": r.status,
             "old_path": r.old_path, "paired_by": r.paired_by, "exports": r.exports, "note": r.note,
             "noise": r.noise, "changes": changes[r.id]}
            for r in rows
        ],
    }


def _in_ingested_build(asset_id_column):
    """EXISTS: the asset is listed by the diff of at least one ingested build."""
    return exists().where(asset_status.c.asset_id == asset_id_column, asset_status.c.build_id == build.c.id,
                          build.c.ingested_at.is_not(None))


@app.get("/api/assets")
def search_assets(q: Filter = None, limit: Limit = 50, offset: Offset = 0):
    where = [_in_ingested_build(asset.c.id)]
    if q:
        where.append(_contains(asset.c.name, q) | _contains(asset.c.path, q)
                     | exists().where(asset_alias.c.asset_id == asset.c.id, _contains(asset_alias.c.old_path, q)))
    with get_engine().connect() as conn:
        total = conn.execute(select(func.count()).select_from(asset).where(*where)).scalar_one()
        rows = conn.execute(
            select(asset.c.id, asset.c.path, asset.c.name, asset.c.type).where(*where)
            .order_by(func.lower(asset.c.name).collate("C"), asset.c.path.collate("C")).limit(limit).offset(offset)
        ).all()
        listed = defaultdict(list)  # asset id -> the ingested builds listing it, newest first
        if rows:
            for r in conn.execute(
                select(asset_status.c.asset_id, build.c.label, asset_status.c.status, asset_status.c.area)
                .select_from(asset_status.join(build, build.c.id == asset_status.c.build_id))
                .where(asset_status.c.asset_id.in_([r.id for r in rows]), build.c.ingested_at.is_not(None))
                .order_by(*NEWEST_FIRST)
            ):
                listed[r.asset_id].append(r)
    return {
        "total": total,
        "items": [
            {"path": r.path, "name": r.name, "type": r.type, "builds": len(listed[r.id]),
             "last": {"label": listed[r.id][0].label, "status": listed[r.id][0].status, "area": listed[r.id][0].area}}
            for r in rows
        ],
    }


def resolve_asset(conn, path: str):
    """The asset at `path`, or the one that most recently moved from it (a former path); None when unknown."""
    row = conn.execute(select(asset).where(asset.c.path == path, _in_ingested_build(asset.c.id))).first()
    if row is None:
        row = conn.execute(
            select(asset).select_from(asset_alias.join(asset, asset.c.id == asset_alias.c.asset_id)
                                      .join(build, build.c.id == asset_alias.c.build_id))
            .where(asset_alias.c.old_path == path, build.c.ingested_at.is_not(None))
            .order_by(*NEWEST_FIRST).limit(1)
        ).first()
    return row


@app.get("/api/asset")
def get_asset(path: Annotated[str, Query(max_length=1000, pattern=r"^[^\x00]*$")], field: Filter = None):
    with get_engine().connect() as conn:
        row = resolve_asset(conn, path)
        if row is None:
            raise HTTPException(404, "asset not found")

        prev = build.alias("prev")
        aliases = conn.execute(
            select(asset_alias.c.old_path, build.c.label)
            .select_from(asset_alias.join(build, build.c.id == asset_alias.c.build_id))
            .where(asset_alias.c.asset_id == row.id, build.c.ingested_at.is_not(None)).order_by(*NEWEST_FIRST)
        ).all()
        history = conn.execute(
            select(build.c.id.label("build_id"), build.c.label, prev.c.label.label("prev_label"), build.c.released_at,
                   asset_status.c.area, asset_status.c.status, asset_status.c.paired_by, asset_status.c.exports,
                   asset_status.c.note, asset_status.c.noise)
            .select_from(asset_status.join(build, build.c.id == asset_status.c.build_id)
                         .outerjoin(prev, prev.c.id == build.c.prev_build_id))
            .where(asset_status.c.asset_id == row.id, build.c.ingested_at.is_not(None)).order_by(*NEWEST_FIRST)
        ).all()
        query = (select(change.c.build_id, change.c.field_path, change.c.op, change.c.before, change.c.after)
                 .where(change.c.asset_id == row.id).order_by(change.c.id))
        if field:
            query = query.where(_contains(change.c.field_path, field))
        changes = defaultdict(list)
        for c in conn.execute(query):
            changes[c.build_id].append({"field_path": c.field_path, "op": c.op, "before": c.before, "after": c.after})

    return {
        "path": row.path, "name": row.name, "type": row.type,
        "aliases": [{"old_path": a.old_path, "build": a.label} for a in reversed(aliases)],
        "history": [
            {"build": {"label": h.label, "prev_label": h.prev_label,
                       "released_at": h.released_at.isoformat() if h.released_at else None},
             "area": h.area, "status": h.status, "paired_by": h.paired_by, "exports": h.exports, "note": h.note,
             "noise": h.noise, "changes": changes[h.build_id]}
            for h in reversed(history)
        ],
    }


@app.get("/api/changes")
def list_changes(
    field: Annotated[str, Query(min_length=2, max_length=200, pattern=r"^[^\x00]*$")],
    build_label: Annotated[str | None, Query(alias="build", max_length=200, pattern=r"^[^\x00]*$")] = None,
    area: Filter = None,
    op: Literal["added", "removed", "changed", "resequenced"] | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
):
    with get_engine().connect() as conn:
        where = [_contains(change.c.field_path, field), build.c.ingested_at.is_not(None)]
        if build_label is not None:
            where.append(build.c.id == _get_build(conn, build_label).id)
        if area is not None:
            where.append(asset_status.c.area == area)
        if op is not None:
            where.append(change.c.op == op)
        joined = (change.join(build, build.c.id == change.c.build_id)
                  .join(asset, asset.c.id == change.c.asset_id)
                  .join(asset_status, (asset_status.c.build_id == change.c.build_id)
                        & (asset_status.c.asset_id == change.c.asset_id)))
        total = conn.execute(select(func.count()).select_from(joined).where(*where)).scalar_one()
        rows = conn.execute(
            select(build.c.label, build.c.released_at, asset.c.path, asset.c.name, asset.c.type,
                   asset_status.c.area, asset_status.c.status, change.c.field_path, change.c.op, change.c.before,
                   change.c.after)
            .select_from(joined).where(*where)
            .order_by(*NEWEST_FIRST, func.lower(asset.c.name).collate("C"), asset.c.path.collate("C"), change.c.id)
            .limit(limit).offset(offset)
        ).all()
    return {
        "total": total,
        "items": [
            {"build": r.label, "released_at": r.released_at.isoformat() if r.released_at else None,
             "asset": {"path": r.path, "name": r.name, "type": r.type}, "area": r.area, "status": r.status,
             "field_path": r.field_path, "op": r.op, "before": r.before, "after": r.after}
            for r in rows
        ],
    }


from tracker.accounts import router  # noqa: E402  (accounts reaches back into this module lazily)

app.include_router(router)
