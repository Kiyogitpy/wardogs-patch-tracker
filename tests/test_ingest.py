import json
import re
import shutil
from datetime import date

import pytest
from sqlalchemy import func, select

from tracker import cli
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


# ---------- hostile input ----------

def hostile_diff(fixtures, tmp_path, edit=None, raw=None):
    """A copy of diff_b2 whose vehicles.json is changed by `edit(first_asset)` or by `raw(text) -> text`."""
    diff = shutil.copytree(fixtures / "diff_b2", tmp_path / "diff")
    target = diff / "vehicles.json"
    if edit:
        data = json.loads(target.read_text(encoding="utf-8"))
        edit(data["assets"][0])
        target.write_text(json.dumps(data), encoding="utf-8")
    if raw:
        target.write_text(raw(target.read_text(encoding="utf-8")), encoding="utf-8")
    return diff


def rejects(db, diff, match):
    with pytest.raises(ValueError, match=match):
        with db.begin() as conn:
            ingest(conn, diff, "b2")
    with db.connect() as conn:
        assert count(conn, build) == 0 and count(conn, asset) == 0 and count(conn, change) == 0


def set_file(diff, name):
    index = json.loads((diff / "index.json").read_text(encoding="utf-8"))
    index["areas"][0]["file"] = name
    (diff / "index.json").write_text(json.dumps(index), encoding="utf-8")


def test_area_file_must_be_relative(db, fixtures, tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"assets": []}), encoding="utf-8")
    diff = hostile_diff(fixtures, tmp_path)
    set_file(diff, str(outside))
    rejects(db, diff, "must be a relative path")
    set_file(diff, "/etc/passwd")
    rejects(db, diff, "must be a relative path")


def test_area_file_cannot_climb_out_with_dotdot(db, fixtures, tmp_path):
    diff = hostile_diff(fixtures, tmp_path)
    (tmp_path / "outside.json").write_text(json.dumps({"assets": []}), encoding="utf-8")
    set_file(diff, ["../outside.json"])
    rejects(db, diff, "leaves the diff directory")


def test_area_file_cannot_be_a_symlink_out(db, fixtures, tmp_path):
    diff = hostile_diff(fixtures, tmp_path)
    (tmp_path / "outside.json").write_text(json.dumps({"assets": []}), encoding="utf-8")
    try:
        (diff / "link.json").symlink_to(tmp_path / "outside.json")
    except OSError:
        pytest.skip("symlinks need a privilege on this machine")
    set_file(diff, "link.json")
    rejects(db, diff, "leaves the diff directory")


def test_a_symlink_inside_the_diff_is_fine(db, fixtures, tmp_path):
    diff = hostile_diff(fixtures, tmp_path)
    try:
        (diff / "alias.json").symlink_to(diff / "vehicles.json")
    except OSError:
        pytest.skip("symlinks need a privilege on this machine")
    set_file(diff, "alias.json")
    with pytest.raises(ValueError, match="listed more than once"):  # it got past the path check
        with db.begin() as conn:
            ingest(conn, diff, "b2")


@pytest.mark.parametrize("edit, match", [
    (lambda e: e.update(name="a\x00b"), "DA_Truck.*NUL"),
    (lambda e: e["changes"][0].update(after=["ok", "x\x00y"]), "DA_Truck.*NUL"),
    (lambda e: e["changes"][0].update(path="Rows.\x00"), "DA_Truck.*NUL"),
    (lambda e: e.update(name="n" * 301), "DA_Truck.*name is 301 characters"),
    (lambda e: e.update(path="Game/Vehicles/" + "p" * 1000), "path is 1014 characters"),
    (lambda e: e.update(old_path="o" * 1001), "old_path is 1001 characters"),
    (lambda e: e["changes"][0].update(path="f" * 1001), "DA_Truck.*field path of 1001 characters"),
    (lambda e: e["changes"][0].update(after="v" * (1 << 20)), "DA_Truck.*after value.*bytes"),
    (lambda e: e["changes"][0].update(before={"k": ["v" * (1 << 20)]}), "DA_Truck.*before value.*bytes"),
])
def test_hostile_values_are_rejected_before_any_write(db, fixtures, tmp_path, edit, match):
    rejects(db, hostile_diff(fixtures, tmp_path, edit), match)


def test_limits_accept_the_edge(db, fixtures, tmp_path):
    def edit(e):
        e["name"] = "n" * 300
        e["changes"][0].update(path="f" * 1000, after="v" * ((1 << 20) - 2))  # +2 quotes = exactly 1 MiB
    with db.begin() as conn:
        ingest(conn, hostile_diff(fixtures, tmp_path, edit), "b2")


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_json_constants_are_rejected(db, fixtures, tmp_path, literal):
    diff = hostile_diff(fixtures, tmp_path, raw=lambda t: re.sub(r'"before":\s*\[[^\]]*\]', f'"before": {literal}', t, count=1))
    rejects(db, diff, f"vehicles.json: {literal} is not valid JSON")


def test_overflowing_float_is_rejected(db, fixtures, tmp_path):
    diff = hostile_diff(fixtures, tmp_path, raw=lambda t: re.sub(r'"before":\s*\[[^\]]*\]', '"before": 1e999', t, count=1))
    rejects(db, diff, "DA_Truck.*non-finite number")


def test_cli_reports_a_bad_diff_without_a_traceback(db, fixtures, tmp_path, monkeypatch, capsys):
    diff = hostile_diff(fixtures, tmp_path, lambda e: e.update(name="a\x00b"))
    monkeypatch.setenv("DATABASE_URL", db.url.render_as_string(hide_password=False))
    code = cli.main(["ingest", "--label", "b2", "--diff", str(diff)])
    err = capsys.readouterr().err
    assert code == 1 and "DA_Truck" in err and "NUL" in err and "Traceback" not in err
    set_file(diff, "../nope.json")
    assert cli.main(["ingest", "--label", "b2", "--diff", str(diff)]) == 1
    assert "leaves the diff directory" in capsys.readouterr().err
