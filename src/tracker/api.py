"""Read-only JSON API over the ingested builds. See docs/API.md."""
import os
from collections import defaultdict
from functools import cache
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query
from sqlalchemy import Engine, case, create_engine, exists, func, select

from tracker.db import asset, asset_alias, asset_status, build, build_area, change

COUNT_KEYS = ("added", "removed", "modified", "moved", "property_changes", "removed_values")

app = FastAPI(title="Wardogs patch tracker")


@cache
def get_engine() -> Engine:
    return create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)


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
    row = conn.execute(_select_build().where(build.c.label == label)).first()
    if row is None:
        raise HTTPException(404, "build not found")
    return row


@app.get("/api/builds")
def list_builds():
    with get_engine().connect() as conn:
        rows = conn.execute(
            _select_build().order_by(build.c.released_at.desc().nulls_last(), build.c.id.desc())
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
            .where(asset_status.c.build_id == row.id).group_by(asset.c.type).order_by(n.desc(), asset.c.type)
        ).all()
    return {
        **_build_json(row, [a.counts for a in areas]),
        "areas": [{"id": a.area, "title": a.title, "counts": a.counts} for a in areas],
        "types": [{"type": t.type, "assets": t.n} for t in types],
    }


@app.get("/api/builds/{label}/assets")
def list_assets(
    label: str,
    area: str | None = None,
    status: Literal["added", "removed", "modified", "moved"] | None = None,
    type: str | None = None,
    q: str | None = None,
    field: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
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

        status_order = case({"modified": 0, "moved": 1, "added": 2, "removed": 3}, value=asset_status.c.status)
        rows = conn.execute(
            select(asset.c.id, asset.c.path, asset.c.name, asset.c.type, asset_status.c.area, asset_status.c.status,
                   asset_alias.c.old_path, asset_status.c.paired_by, asset_status.c.exports, asset_status.c.note,
                   asset_status.c.noise)
            .select_from(joined.outerjoin(asset_alias, (asset_alias.c.build_id == asset_status.c.build_id)
                                          & (asset_alias.c.asset_id == asset_status.c.asset_id)))
            .where(*where).order_by(status_order, func.lower(asset.c.name), asset.c.path)
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
