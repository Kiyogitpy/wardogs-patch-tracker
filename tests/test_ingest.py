import json
import shutil
from datetime import date

import pytest
from sqlalchemy import func, select

from tracker.db import asset, asset_alias, asset_status, build, change
from tracker.ingest import TotalsMismatch, ingest


def count(conn, table):
    return conn.execute(select(func.count()).select_from(table)).scalar_one()


def test_ingest_stores_every_asset_and_change(db, fixtures):
    manifest = {"steam": {"buildid": "25937608"}, "fingerprint": "abc"}
    with db.begin() as conn:
        result = ingest(conn, fixtures / "diff_b2", "b2", "b1", manifest, date(2026, 10, 15))
    with db.connect() as conn:
        assert result["assets"] == 6 and result["changes"] == 6
        assert count(conn, asset_status) == 6 and count(conn, change) == 6
        row = conn.execute(select(build).where(build.c.label == "b2")).one()
        assert (row.steam_buildid, row.manifest_fingerprint, row.released_at) == ("25937608", "abc", date(2026, 10, 15))
        assert conn.execute(select(build.c.label).where(build.c.id == row.prev_build_id)).scalar_one() == "b1"
        added = conn.execute(select(change.c.before, change.c.after).where(change.c.field_path == "Properties.Tags[2]")).one()
        assert added == (None, "Tag.X")


def test_reingest_replaces_the_build(db, fixtures):
    with db.begin() as conn:
        ingest(conn, fixtures / "diff_b2", "b2", "b1", {"steam": {"buildid": "1"}, "fingerprint": "f"})
    with db.begin() as conn:
        ingest(conn, fixtures / "diff_b2", "b2")
    with db.connect() as conn:
        assert count(conn, build) == 2 and count(conn, asset_status) == 6 and count(conn, change) == 6
        assert conn.execute(select(build.c.steam_buildid).where(build.c.label == "b2")).scalar_one() == "1"


def test_moved_asset_keeps_its_history(db, fixtures):
    with db.begin() as conn:
        ingest(conn, fixtures / "diff_b1", "b1", "b0")
        ingest(conn, fixtures / "diff_b2", "b2", "b1")
    with db.connect() as conn:
        mosin = conn.execute(select(asset).where(asset.c.name == "AnimSet_Mosin")).one()
        assert mosin.path == "Game/New/AnimSet_Mosin.json"
        assert count(conn, asset) == 6
        history = conn.execute(
            select(build.c.label, change.c.field_path).join(build, build.c.id == change.c.build_id)
            .where(change.c.asset_id == mosin.id).order_by(build.c.id)
        ).all()
        assert history == [("b1", "Properties.Rate"), ("b2", "Package")]
        assert conn.execute(select(asset_alias.c.old_path, asset_alias.c.asset_id)).one() == ("Game/Old/AnimSet_Mosin.json", mosin.id)


def test_totals_mismatch_writes_nothing(db, fixtures, tmp_path):
    diff = shutil.copytree(fixtures / "diff_b2", tmp_path / "diff")
    index = json.loads((diff / "index.json").read_text(encoding="utf-8"))
    index["totals"]["property_changes"] += 1
    (diff / "index.json").write_text(json.dumps(index), encoding="utf-8")
    with pytest.raises(TotalsMismatch, match="property_changes: stored 4, index.json 5"):
        with db.begin() as conn:
            ingest(conn, diff, "b2")
    with db.connect() as conn:
        assert count(conn, build) == 0 and count(conn, change) == 0


def test_duplicate_asset_path_is_rejected(db, fixtures, tmp_path):
    diff = shutil.copytree(fixtures / "diff_b2", tmp_path / "diff")
    vehicles = json.loads((diff / "vehicles.json").read_text(encoding="utf-8"))
    vehicles["assets"].append(vehicles["assets"][0])
    (diff / "vehicles.json").write_text(json.dumps(vehicles), encoding="utf-8")
    with pytest.raises(ValueError, match="DA_Truck"):
        with db.begin() as conn:
            ingest(conn, diff, "b2")
