from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, update

from tracker import api
from tracker.db import asset, asset_alias, asset_status, build, change
from tracker.ingest import ingest

OLD = "Game/Old/AnimSet_Mosin.json"
NEW = "Game/New/AnimSet_Mosin.json"
RIFLE = "Game/Weapons/DA_Rifle.json"


@pytest.fixture
def client(db, fixtures, monkeypatch):
    # Chronological: b1 (no date), b2, b3. b0 is only a base build and is never ingested.
    with db.begin() as conn:
        ingest(conn, fixtures / "diff_b1", "b1", "b0")
        ingest(conn, fixtures / "diff_b2", "b2", "b1", released_at=date(2026, 10, 15))
        ingest(conn, fixtures / "diff_b3", "b3", "b2", released_at=date(2026, 11, 1))
    monkeypatch.setenv("DATABASE_URL", db.url.render_as_string(hide_password=False))
    api.get_engine.cache_clear()
    yield TestClient(api.app)
    api.get_engine().dispose()
    api.get_engine.cache_clear()


def names(r):
    return [i["name"] for i in r.json()["items"]]


def test_asset_history_is_chronological(client):
    body = client.get("/api/asset", params={"path": RIFLE}).json()
    assert body["name"] == "DA_Rifle" and body["type"] == "WeaponData" and body["aliases"] == []
    assert [h["build"]["label"] for h in body["history"]] == ["b2", "b3"]
    assert body["history"][0]["build"] == {"label": "b2", "prev_label": "b1", "released_at": "2026-10-15"}
    assert [c["field_path"] for c in body["history"][0]["changes"]] == ["Properties.BaseDamage", "Properties.Tags[2]"]
    assert body["history"][1]["changes"] == [
        {"field_path": "Properties.BaseDamage", "op": "changed", "before": 45, "after": 50}]
    assert body["history"][1]["status"] == "modified" and body["history"][1]["area"] == "weapons-ammo"


def test_undated_build_is_first_in_history(client):
    body = client.get("/api/asset", params={"path": NEW}).json()
    assert [h["build"]["label"] for h in body["history"]] == ["b1", "b2", "b3"]
    assert body["history"][0]["build"]["released_at"] is None
    assert [h["status"] for h in body["history"]] == ["modified", "moved", "modified"]
    assert body["history"][1]["paired_by"] == "same-name"


def test_lookup_by_former_path_and_aliases(client):
    by_old = client.get("/api/asset", params={"path": OLD})
    assert by_old.status_code == 200
    assert by_old.json() == client.get("/api/asset", params={"path": NEW}).json()
    assert by_old.json()["path"] == NEW
    assert by_old.json()["aliases"] == [{"old_path": OLD, "build": "b2"}]


def _ids(conn):
    return ({r.name: r.id for r in conn.execute(asset.select())}, {r.label: r.id for r in conn.execute(build.select())})


def test_former_path_resolves_to_most_recent_mover(client, db):
    with db.begin() as conn:
        ids, builds = _ids(conn)
        conn.execute(insert(asset_alias), [
            {"old_path": "Shared.json", "build_id": builds["b3"], "asset_id": ids["DA_Truck"]},
            {"old_path": "Shared.json", "build_id": builds["b2"], "asset_id": ids["DA_Jeep"]},
        ])
    assert client.get("/api/asset", params={"path": "Shared.json"}).json()["name"] == "DA_Truck"


def test_current_path_wins_over_former_path(client, db):
    with db.begin() as conn:
        ids, builds = _ids(conn)
        conn.execute(insert(asset_alias).values(old_path=RIFLE, build_id=builds["b3"], asset_id=ids["DA_Jeep"]))
    assert client.get("/api/asset", params={"path": RIFLE}).json()["name"] == "DA_Rifle"


def test_aliases_are_chronological(client, db):
    with db.begin() as conn:
        ids, builds = _ids(conn)
        conn.execute(insert(asset_alias).values(old_path="Game/Older.json", build_id=builds["b1"], asset_id=ids["AnimSet_Mosin"]))
    aliases = client.get("/api/asset", params={"path": NEW}).json()["aliases"]
    assert aliases == [{"old_path": "Game/Older.json", "build": "b1"}, {"old_path": OLD, "build": "b2"}]


def test_field_narrows_changes_but_keeps_builds(client):
    body = client.get("/api/asset", params={"path": NEW, "field": "package"}).json()
    assert [h["build"]["label"] for h in body["history"]] == ["b1", "b2", "b3"]
    assert [len(h["changes"]) for h in body["history"]] == [0, 1, 0]
    assert body["history"][1]["changes"][0]["field_path"] == "Package"


def test_asset_404_and_422(client):
    assert client.get("/api/asset", params={"path": "nope"}).json() == {"detail": "asset not found"}
    assert client.get("/api/asset", params={"path": ""}).status_code == 404
    assert client.get("/api/asset").status_code == 422
    assert client.get("/api/asset", params={"path": "a\x00"}).status_code == 422
    assert client.get("/api/asset", params={"path": NEW, "field": "x" * 201}).status_code == 422


def test_asset_in_no_ingested_diff_is_not_listed(client, db):
    # Ghost's only diff belongs to the never-ingested base build b0; NoDiff has no diff at all.
    with db.begin() as conn:
        _, builds = _ids(conn)
        ghost = conn.execute(insert(asset).values(path="Game/Ghost.json", name="Ghost", type="Ghost").returning(asset.c.id)).scalar_one()
        conn.execute(insert(asset_status).values(build_id=builds["b0"], asset_id=ghost, area="weapons-ammo", status="modified", noise={}))
        conn.execute(insert(change).values(build_id=builds["b0"], asset_id=ghost, field_path="Ghost.Damage", op="changed", before=1, after=2))
        conn.execute(insert(asset).values(path="Game/NoDiff.json", name="NoDiff", type="Ghost"))
    assert client.get("/api/assets").json()["total"] == 6
    assert client.get("/api/assets", params={"q": "ghost"}).json() == {"total": 0, "items": []}
    assert client.get("/api/assets", params={"q": "nodiff"}).json()["total"] == 0
    assert client.get("/api/asset", params={"path": "Game/Ghost.json"}).status_code == 404
    assert client.get("/api/asset", params={"path": "Game/NoDiff.json"}).status_code == 404
    assert client.get("/api/changes", params={"field": "Ghost"}).json() == {"total": 0, "items": []}


def test_assets_search_order_and_last(client):
    r = client.get("/api/assets")
    assert r.json()["total"] == 6
    assert names(r) == ["AnimSet_Mosin", "DA_Jeep", "DA_OldGun", "DA_Pistol", "DA_Rifle", "DA_Truck"]
    items = {i["name"]: i for i in r.json()["items"]}
    assert items["AnimSet_Mosin"] == {
        "path": NEW, "name": "AnimSet_Mosin", "type": "BHAnimationSet", "builds": 3,
        "last": {"label": "b3", "status": "modified", "area": "weapons-ammo"}}
    assert items["DA_Rifle"]["builds"] == 2 and items["DA_Rifle"]["last"]["label"] == "b3"
    assert items["DA_Jeep"]["builds"] == 1 and items["DA_Jeep"]["last"] == {
        "label": "b2", "status": "modified", "area": "vehicles"}
    assert items["DA_OldGun"]["last"]["status"] == "removed"


def test_assets_search_matches_name_path_and_old_path(client):
    assert names(client.get("/api/assets", params={"q": "RIFLE"})) == ["DA_Rifle"]
    assert names(client.get("/api/assets", params={"q": "game/vehicles"})) == ["DA_Jeep", "DA_Truck"]
    r = client.get("/api/assets", params={"q": "Game/Old/"})  # only a former path of the Mosin
    assert names(r) == ["AnimSet_Mosin"] and r.json()["items"][0]["path"] == NEW
    assert client.get("/api/assets", params={"q": ""}).json()["total"] == 6
    assert client.get("/api/assets", params={"q": "%"}).json()["total"] == 0


def test_assets_pagination_total(client):
    r = client.get("/api/assets", params={"limit": 2, "offset": 2})
    assert r.json()["total"] == 6 and names(r) == ["DA_OldGun", "DA_Pistol"]
    assert client.get("/api/assets", params={"offset": 10}).json() == {"total": 6, "items": []}


def test_assets_order_is_by_code_point(client, db):
    with db.begin() as conn:
        conn.execute(update(asset).where(asset.c.name == "DA_Jeep").values(name="DA_Truck_B"))
        conn.execute(update(asset).where(asset.c.name == "DA_Truck").values(name="DA_TruckA"))
    assert names(client.get("/api/assets", params={"q": "DA_Truck"})) == ["DA_Truck_B", "DA_TruckA"]


def test_changes_newest_build_first(client):
    r = client.get("/api/changes", params={"field": "damage"})
    assert r.json()["total"] == 2
    assert [(i["build"], i["before"], i["after"]) for i in r.json()["items"]] == [("b3", 45, 50), ("b2", 40, 45)]
    assert r.json()["items"][0] == {
        "build": "b3", "released_at": "2026-11-01",
        "asset": {"path": RIFLE, "name": "DA_Rifle", "type": "WeaponData"},
        "area": "weapons-ammo", "status": "modified", "field_path": "Properties.BaseDamage",
        "op": "changed", "before": 45, "after": 50}


def test_changes_order_within_build_and_undated_last(client):
    r = client.get("/api/changes", params={"field": "Properties"})
    assert [(i["build"], i["asset"]["name"], i["field_path"]) for i in r.json()["items"]] == [
        ("b3", "AnimSet_Mosin", "Properties.Rate"), ("b3", "DA_Rifle", "Properties.BaseDamage"),
        ("b2", "DA_Rifle", "Properties.BaseDamage"), ("b2", "DA_Rifle", "Properties.Tags[2]"),
        ("b1", "AnimSet_Mosin", "Properties.Rate")]
    assert r.json()["items"][-1]["released_at"] is None
    # status is the asset's status in that build
    assert client.get("/api/changes", params={"field": "Package"}).json()["items"][0]["status"] == "moved"


def test_changes_filters(client):
    p = {"field": "Properties"}
    assert [i["build"] for i in client.get("/api/changes", params={**p, "build": "b2"}).json()["items"]] == ["b2", "b2"]
    assert client.get("/api/changes", params={**p, "build": "b1"}).json()["total"] == 1
    assert client.get("/api/changes", params={**p, "area": "vehicles"}).json()["total"] == 0
    assert client.get("/api/changes", params={"field": "Seats", "area": "vehicles"}).json()["total"] == 1
    assert client.get("/api/changes", params={**p, "op": "added"}).json()["total"] == 1
    assert client.get("/api/changes", params={"field": "Seats", "op": "resequenced"}).json()["total"] == 1
    assert client.get("/api/changes", params={"field": "Type", "op": "removed"}).json()["total"] == 1
    r = client.get("/api/changes", params={**p, "build": "b2", "area": "weapons-ammo", "op": "changed"})
    assert r.json()["total"] == 1 and r.json()["items"][0]["field_path"] == "Properties.BaseDamage"


@pytest.mark.parametrize("label", ["b0", "nope"])
def test_changes_unknown_or_base_only_build_is_404(client, label):
    r = client.get("/api/changes", params={"field": "Properties", "build": label})
    assert r.status_code == 404 and r.json() == {"detail": "build not found"}


def test_changes_pagination_total(client):
    r = client.get("/api/changes", params={"field": "Properties", "limit": 2, "offset": 1})
    assert r.json()["total"] == 5 and len(r.json()["items"]) == 2
    assert r.json()["items"][0]["asset"]["name"] == "DA_Rifle"
    assert client.get("/api/changes", params={"field": "Properties", "offset": 9}).json() == {"total": 5, "items": []}


def test_changes_order_is_by_code_point(client, db):
    with db.begin() as conn:
        conn.execute(update(asset).where(asset.c.name == "DA_Rifle").values(name="DA_Truck_B"))
        conn.execute(update(asset).where(asset.c.name == "AnimSet_Mosin").values(name="DA_TruckA"))
    r = client.get("/api/changes", params={"field": "Properties", "build": "b3"})
    assert [i["asset"]["name"] for i in r.json()["items"]] == ["DA_Truck_B", "DA_TruckA"]


def test_changes_wildcards_are_literal(client):
    assert client.get("/api/changes", params={"field": "Pr%"}).json()["total"] == 0
    assert client.get("/api/changes", params={"field": "Tags[_]"}).json()["total"] == 0
    assert client.get("/api/changes", params={"field": "Tags[2]"}).json()["total"] == 1


@pytest.mark.parametrize("path, params", [
    ("/api/changes", {}), ("/api/changes", {"field": ""}), ("/api/changes", {"field": "a"}),
    ("/api/changes", {"field": "x" * 201}), ("/api/changes", {"field": "ab\x00"}),
    ("/api/changes", {"field": "ab", "op": "bogus"}), ("/api/changes", {"field": "ab", "limit": 0}),
    ("/api/changes", {"field": "ab", "limit": 201}), ("/api/changes", {"field": "ab", "offset": -1}),
    ("/api/changes", {"field": "ab", "offset": 1_000_001}), ("/api/changes", {"field": "ab", "build": "b\x00"}),
    ("/api/changes", {"field": "ab", "area": "x" * 201}),
    ("/api/assets", {"q": "a\x00"}), ("/api/assets", {"q": "x" * 201}), ("/api/assets", {"limit": 0}),
    ("/api/assets", {"limit": 201}), ("/api/assets", {"offset": -1}), ("/api/assets", {"offset": 10**20}),
])
def test_bad_params_are_422(client, path, params):
    assert client.get(path, params=params).status_code == 422
