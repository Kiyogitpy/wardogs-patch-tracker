"""Load one `season_diff.py` output directory into the database as one build."""
import json
from collections import Counter
from datetime import date
from pathlib import Path

from sqlalchemy import Connection, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from tracker.db import asset, asset_alias, asset_status, build, build_area, change

STATUSES = ("added", "removed", "modified", "moved")
NOISE_KEYS = ("renamed_keys", "ref_shifts", "dup_keys", "swapped_keys", "type_reinterpret")


class TotalsMismatch(Exception):
    pass


def load_diff(diff_dir: Path) -> tuple[dict, list[tuple[str, dict]]]:
    """Return index.json and every (area id, asset entry). Large areas are split over several files."""
    index = json.loads((diff_dir / "index.json").read_text(encoding="utf-8"))
    entries = []
    for area in index["areas"]:
        files = area["file"] if isinstance(area["file"], list) else [area["file"]]
        for name in files:
            for entry in json.loads((diff_dir / name).read_text(encoding="utf-8"))["assets"]:
                entries.append((area["id"], entry))
    return index, entries


def ingest(conn: Connection, diff_dir: Path, label: str, prev_label: str | None = None,
           manifest: dict | None = None, released_at: date | None = None) -> dict:
    """Replace build `label` with the diff in `diff_dir`. Raises TotalsMismatch; the caller's transaction rolls back."""
    index, entries = load_diff(diff_dir)
    dupes = [p for p, n in Counter(e["path"] for _, e in entries).items() if n > 1]
    if dupes:
        raise ValueError(f"assets listed more than once in the diff: {dupes[:5]}")

    values = {
        "prev_build_id": _ensure_build(conn, prev_label) if prev_label else None,
        "steam_buildid": manifest["steam"]["buildid"] if manifest else None,
        "manifest_fingerprint": manifest["fingerprint"] if manifest else None,
        "released_at": released_at,
        "ingested_at": func.now(),
    }
    # A re-ingest keeps metadata it was not given instead of erasing it.
    build_id = conn.execute(
        insert(build).values(label=label, **values)
        .on_conflict_do_update(index_elements=["label"], set_={k: v for k, v in values.items() if v is not None})
        .returning(build.c.id)
    ).scalar_one()
    for table in (change, asset_status, asset_alias, build_area):
        conn.execute(delete(table).where(table.c.build_id == build_id))

    # Rename before the upsert so a moved asset keeps its id. On a re-ingest the old path is already gone.
    for _, e in entries:
        if e["old_path"]:
            conn.execute(update(asset).where(asset.c.path == e["old_path"]).values(path=e["path"]))
    upsert = insert(asset)
    _insert_many(conn, upsert.on_conflict_do_update(
        index_elements=["path"], set_={"name": upsert.excluded.name, "type": upsert.excluded.type},
    ), [{"path": e["path"], "name": e["name"], "type": e["type"]} for _, e in entries])
    ids = dict(conn.execute(select(asset.c.path, asset.c.id).where(asset.c.path.in_([e["path"] for _, e in entries]))).all())

    _insert_many(conn, insert(asset_status), [
        {"build_id": build_id, "asset_id": ids[e["path"]], "area": area, "status": e["status"],
         "paired_by": e["paired_by"], "exports": e.get("exports"), "note": e.get("note"), "noise": e["noise"]}
        for area, e in entries
    ])
    changes = [
        {"build_id": build_id, "asset_id": ids[e["path"]], "field_path": c["path"], "op": c["op"],
         "before": c["before"], "after": c["after"]}
        for _, e in entries for c in e["changes"]
    ]
    _insert_many(conn, insert(change), changes)
    _insert_many(conn, insert(asset_alias), [
        {"old_path": e["old_path"], "build_id": build_id, "asset_id": ids[e["path"]]} for _, e in entries if e["old_path"]
    ])
    _insert_many(conn, insert(build_area), [
        {"build_id": build_id, "area": a["id"], "title": a["title"], "counts": a["counts"]} for a in index["areas"]
    ])

    check_totals(conn, build_id, index)
    return {"build_id": build_id, "assets": len(entries), "changes": len(changes)}


def check_totals(conn: Connection, build_id: int, index: dict) -> None:
    """Recount the stored build the way season_diff.py counts and compare with index.json."""
    rows = conn.execute(
        select(asset_status.c.area, asset_status.c.status, asset_status.c.noise, func.count(change.c.id))
        .select_from(asset_status.outerjoin(change, (change.c.build_id == asset_status.c.build_id)
                                            & (change.c.asset_id == asset_status.c.asset_id)))
        .where(asset_status.c.build_id == build_id)
        .group_by(asset_status.c.build_id, asset_status.c.asset_id)
    ).all()

    areas = {a["id"]: dict.fromkeys((*STATUSES, "property_changes", "removed_values"), 0) for a in index["areas"]}
    totals = dict.fromkeys(["assets_" + s for s in STATUSES]
                           + ["noise_only_assets", "property_changes", "removed_values"]
                           + ["noise_" + k for k in NOISE_KEYS], 0)
    for area, status, noise, n in rows:
        counts = areas[area]
        counts[status] += 1
        if status == "removed":
            counts["removed_values"] += n
        elif status != "added":
            counts["property_changes"] += n
        if status == "modified" and n == 0 and any(noise[k] for k in NOISE_KEYS):
            totals["noise_only_assets"] += 1
        for k in NOISE_KEYS:
            totals["noise_" + k] += noise[k]
    for counts in areas.values():
        for s in STATUSES:
            totals["assets_" + s] += counts[s]
        totals["property_changes"] += counts["property_changes"]
        totals["removed_values"] += counts["removed_values"]

    problems = [f"{k}: stored {v}, index.json {index['totals'].get(k)}" for k, v in totals.items() if index["totals"].get(k) != v]
    problems += [f"area {a['id']}: stored {areas[a['id']]}, index.json {a['counts']}"
                 for a in index["areas"] if a["counts"] != areas[a["id"]]]
    if problems:
        raise TotalsMismatch("; ".join(problems))


def _ensure_build(conn: Connection, label: str) -> int:
    conn.execute(insert(build).values(label=label).on_conflict_do_nothing(index_elements=["label"]))
    return conn.execute(select(build.c.id).where(build.c.label == label)).scalar_one()


def _insert_many(conn: Connection, stmt, rows: list[dict]) -> None:
    if rows:
        conn.execute(stmt, rows)
