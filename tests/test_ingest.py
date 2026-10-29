import json
import re
import shutil
from datetime import date

import pytest
import sqlalchemy.exc
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


# ---------- malformed diffs and files that are not regular files ----------

def edit_json(diff, name, edit):
    data = json.loads((diff / name).read_text(encoding="utf-8"))
    edit(data)
    (diff / name).write_text(json.dumps(data), encoding="utf-8")


def test_area_file_that_is_a_directory_is_a_clear_error(db, fixtures, tmp_path):
    diff = hostile_diff(fixtures, tmp_path)
    (diff / "subdir").mkdir()
    set_file(diff, "subdir")
    rejects(db, diff, "area weapons-ammo: file 'subdir' is not a regular file")


def test_area_file_that_is_missing_is_a_clear_error(db, fixtures, tmp_path):
    diff = hostile_diff(fixtures, tmp_path)
    set_file(diff, "nope.json")
    rejects(db, diff, "area weapons-ammo: file 'nope.json' does not exist")
    set_file(diff, ["vehicles.json", "nope.json"])
    rejects(db, diff, "file 'nope.json' does not exist")


def test_index_json_missing_or_a_directory_is_a_clear_error(db, fixtures, tmp_path):
    diff = hostile_diff(fixtures, tmp_path)
    (diff / "index.json").unlink()
    rejects(db, diff, "index.json: does not exist")
    (diff / "index.json").mkdir()
    rejects(db, diff, "index.json: is not a regular file")


@pytest.mark.parametrize("name, edit, match", [
    ("index.json", lambda d: d.pop("areas"), "index.json: missing 'areas'"),
    ("index.json", lambda d: d.pop("totals"), "index.json: missing 'totals'"),
    ("index.json", lambda d: d["areas"][0].pop("id"), "missing 'id'"),
    ("index.json", lambda d: d["areas"][0].pop("file"), "missing 'file'"),
    ("index.json", lambda d: d["areas"][0].pop("title"), "missing 'title'"),
    ("index.json", lambda d: d["areas"][0].pop("counts"), "missing 'counts'"),
    ("index.json", lambda d: d.update(areas={}), "areas: expected list"),
    ("vehicles.json", lambda d: d.pop("assets"), "vehicles.json: missing 'assets'"),
    ("vehicles.json", lambda d: d.update(assets=None), "assets: expected list"),
    ("vehicles.json", lambda d: d["assets"][0].pop("path"), "missing 'path'"),
    ("vehicles.json", lambda d: d["assets"][0].pop("name"), "missing 'name'"),
    ("vehicles.json", lambda d: d["assets"][0].pop("type"), "missing 'type'"),
    ("vehicles.json", lambda d: d["assets"][0].pop("status"), "missing 'status'"),
    ("vehicles.json", lambda d: d["assets"][0].pop("noise"), "missing 'noise'"),
    ("vehicles.json", lambda d: d["assets"][0].pop("changes"), "missing 'changes'"),
    ("vehicles.json", lambda d: d["assets"][0].pop("old_path"), "missing 'old_path'"),
    ("vehicles.json", lambda d: d["assets"][0]["noise"].pop("dup_keys"), "missing 'dup_keys'"),
    ("vehicles.json", lambda d: d["assets"][0]["changes"][0].pop("op"), "missing 'op'"),
    ("vehicles.json", lambda d: d["assets"][0]["changes"][0].pop("before"), "missing 'before'"),
    ("vehicles.json", lambda d: d["assets"].append("not an object"), "expected dict"),
])
def test_missing_required_keys_are_value_errors_not_key_errors(db, fixtures, tmp_path, name, edit, match):
    diff = hostile_diff(fixtures, tmp_path)
    edit_json(diff, name, edit)
    rejects(db, diff, match)


def test_incomplete_manifest_is_a_value_error(db, fixtures):
    with pytest.raises(ValueError, match="manifest: missing 'fingerprint'"):
        with db.begin() as conn:
            ingest(conn, fixtures / "diff_b2", "b2", manifest={"steam": {"buildid": "1"}})
    with pytest.raises(ValueError, match="manifest.steam: missing 'buildid'"):
        with db.begin() as conn:
            ingest(conn, fixtures / "diff_b2", "b2", manifest={"steam": {}, "fingerprint": "f"})


def test_file_swapped_for_a_symlink_after_the_check_is_still_refused(db, fixtures, tmp_path, monkeypatch):
    """The path was checked, then (here: exactly when it is opened) replaced by a link out of the directory."""
    from tracker import ingest as ingest_mod
    diff = hostile_diff(fixtures, tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"assets": []}), encoding="utf-8")
    real_open = open

    def swapping_open(path, *args, **kwargs):
        if str(path).endswith("vehicles.json"):
            (diff / "vehicles.json").unlink()
            try:
                (diff / "vehicles.json").symlink_to(outside)
            except OSError:
                pytest.skip("symlinks need a privilege on this machine")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(ingest_mod, "open", swapping_open, raising=False)
    rejects(db, diff, "leaves the diff directory")


def run_ingest_cli(db, monkeypatch, diff, *extra):
    monkeypatch.setenv("DATABASE_URL", db.url.render_as_string(hide_password=False))
    return cli.main(["ingest", "--label", "b2", "--diff", str(diff), *extra])


def test_cli_reports_bad_files_and_keys_without_a_traceback(db, fixtures, tmp_path, monkeypatch, capsys):
    diff = hostile_diff(fixtures, tmp_path)
    (diff / "subdir").mkdir()
    set_file(diff, "subdir")
    assert run_ingest_cli(db, monkeypatch, diff) == 1
    err = capsys.readouterr().err
    assert "not a regular file" in err and "Traceback" not in err

    edit_json(diff, "index.json", lambda d: d.pop("areas"))
    assert run_ingest_cli(db, monkeypatch, diff) == 1
    err = capsys.readouterr().err
    assert "missing 'areas'" in err and "Traceback" not in err

    assert run_ingest_cli(db, monkeypatch, tmp_path / "no-such-dir") == 1
    assert "does not exist" in capsys.readouterr().err


def test_cli_reports_a_bad_manifest_without_a_traceback(db, fixtures, tmp_path, monkeypatch, capsys):
    assert run_ingest_cli(db, monkeypatch, fixtures / "diff_b2", "--manifest", str(tmp_path / "nope.json")) == 1
    assert "manifest" in capsys.readouterr().err
    bad = tmp_path / "manifest.json"
    bad.write_text("{not json", encoding="utf-8")
    assert run_ingest_cli(db, monkeypatch, fixtures / "diff_b2", "--manifest", str(bad)) == 1
    assert "manifest" in capsys.readouterr().err


def test_cli_disposes_its_engine(db, fixtures, monkeypatch):
    engines = []
    real = cli.create_engine

    def spy(*args, **kwargs):
        engines.append(real(*args, **kwargs))
        return engines[-1]

    monkeypatch.setattr(cli, "create_engine", spy)
    assert run_ingest_cli(db, monkeypatch, fixtures / "diff_b2") == 0
    assert len(engines) == 1 and engines[0].pool.checkedin() == 0 and engines[0].pool.checkedout() == 0


# ---------- types and enums the database relies on ----------

def counts_zero(db):
    with db.connect() as conn:
        assert count(conn, build) == 0 and count(conn, asset) == 0 and count(conn, change) == 0


@pytest.mark.parametrize("edit, match", [
    (lambda e: e.update(status="deleted"), "status 'deleted' is not one of"),
    (lambda e: e.update(status=["added"]), "is not one of"),
    (lambda e: e["changes"][0].update(op="rewritten"), "change op 'rewritten' is not one of"),
    (lambda e: e.update(exports="12"), "exports must be an integer or null"),
    (lambda e: e.update(exports=1.5), "exports must be an integer or null"),
    (lambda e: e.update(exports=True), "exports must be an integer or null"),
    (lambda e: e.update(exports=2**40), "exports must be an integer or null"),
    (lambda e: e["noise"].update(ref_shifts="3"), "noise.ref_shifts must be an integer"),
    (lambda e: e["noise"].update(dup_keys=None), "noise.dup_keys must be an integer"),
    (lambda e: e["noise"].update(dup_keys=1.0), "noise.dup_keys must be an integer"),
    (lambda e: e.update(name="a\ud800"), "DA_Truck.*lone surrogate"),
])
def test_wrong_types_and_enums_are_clear_value_errors(db, fixtures, tmp_path, edit, match):
    rejects(db, hostile_diff(fixtures, tmp_path, edit), match)


def test_exports_may_be_null_or_an_int(db, fixtures, tmp_path):
    with db.begin() as conn:
        ingest(conn, hostile_diff(fixtures, tmp_path, lambda e: e.update(exports=7)), "b2")
    with db.connect() as conn:
        assert conn.execute(select(asset_status.c.exports).where(asset_status.c.exports == 7)).scalar_one() == 7


@pytest.mark.parametrize("name, edit, match", [
    ("index.json", lambda d: d["areas"][0].update(id=5), "area 0 id: must be a string"),
    ("index.json", lambda d: d["areas"][0].update(title=None), "title: must be a string"),
    ("index.json", lambda d: d["areas"][0].update(title="t\x00"), "title: contains a NUL"),
    ("index.json", lambda d: d["areas"][0].update(file=3), "file must be a string or a list of strings"),
    ("index.json", lambda d: d["areas"][0].update(file=["weapons-ammo-1.json", 3]), "file must be a string or a list"),
])
def test_area_types_are_validated(db, fixtures, tmp_path, name, edit, match):
    diff = hostile_diff(fixtures, tmp_path)
    edit_json(diff, name, edit)
    rejects(db, diff, match)


@pytest.mark.parametrize("manifest, match", [
    ({"steam": {"buildid": 123}, "fingerprint": "f"}, r"manifest.steam.buildid: must be a string"),
    ({"steam": {"buildid": "1\x00"}, "fingerprint": "f"}, r"manifest.steam.buildid: contains a NUL"),
    ({"steam": {"buildid": "1"}, "fingerprint": None}, r"manifest.fingerprint: must be a string"),
    ({"steam": {"buildid": "1"}, "fingerprint": "f\x00"}, r"manifest.fingerprint: contains a NUL"),
])
def test_manifest_types_are_validated(db, fixtures, manifest, match):
    with pytest.raises(ValueError, match=match):
        with db.begin() as conn:
            ingest(conn, fixtures / "diff_b2", "b2", manifest=manifest)
    counts_zero(db)


def test_a_move_onto_another_existing_asset_is_refused(db, fixtures, tmp_path):
    with db.begin() as conn:
        ingest(conn, fixtures / "diff_b1", "b1", "b0")
        old = conn.execute(select(asset.c.path)).scalar_one()
        new = "Game/Other/Taken.json"
        conn.execute(asset.insert().values(path=new, name="Taken", type="t"))
        before = count(conn, asset)
    diff = hostile_diff(fixtures, tmp_path, lambda e: e.update(old_path=old, path=new))
    with pytest.raises(ValueError, match="both paths are existing assets"):
        with db.begin() as conn:
            ingest(conn, diff, "b2", "b1")
    with db.connect() as conn:
        assert count(conn, asset) == before and count(conn, build) == 2  # b0, b1; b2 was never written


def test_a_move_onto_a_path_that_is_itself_moving_away_is_allowed(db, fixtures):
    entries = [{"old_path": "a", "path": "b"}, {"old_path": "b", "path": "c"}]
    with db.begin() as conn:
        conn.execute(asset.insert(), [{"path": "a", "name": "a", "type": "t"}, {"path": "b", "name": "b", "type": "t"}])
        from tracker.ingest import _check_moves
        _check_moves(conn, entries)  # no error


# ---------- the CLI separates database errors (3) from bad diffs (1) ----------

@pytest.mark.parametrize("error", [
    sqlalchemy.exc.IntegrityError("INSERT INTO asset VALUES (%(p)s)", {"p": "SECRET-PARAM"}, Exception("duplicate key")),
    sqlalchemy.exc.OperationalError("SELECT 1", {}, Exception("connection lost")),
    TypeError("expected str, got int"),
])
def test_cli_database_errors_exit_3_without_a_traceback(db, fixtures, monkeypatch, capsys, error):
    def boom(*args, **kwargs):
        raise error
    monkeypatch.setattr(cli, "ingest", boom)
    assert run_ingest_cli(db, monkeypatch, fixtures / "diff_b2") == 3
    err = capsys.readouterr().err
    assert err.startswith("tracker: database error") and err.count("\n") == 1
    assert "Traceback" not in err and "SECRET-PARAM" not in err and "INSERT" not in err


def test_cli_bad_diff_still_exits_1(db, fixtures, tmp_path, monkeypatch, capsys):
    diff = hostile_diff(fixtures, tmp_path, lambda e: e.update(status="deleted"))
    assert run_ingest_cli(db, monkeypatch, diff) == 1
    assert "bad diff" in capsys.readouterr().err
