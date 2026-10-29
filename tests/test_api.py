from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from tracker import api
from tracker.db import change
from tracker.ingest import ingest

W = "/api/builds/b2/assets"


@pytest.fixture
def client(db, fixtures, monkeypatch):
    with db.begin() as conn:
        ingest(conn, fixtures / "diff_b1", "b1", "b0")
        ingest(conn, fixtures / "diff_b2", "b2", "b1", {"steam": {"buildid": "99"}, "fingerprint": "f"}, date(2026, 10, 15))
    monkeypatch.setenv("DATABASE_URL", db.url.render_as_string(hide_password=False))
    api.get_engine.cache_clear()
    yield TestClient(api.app)
    api.get_engine().dispose()
    api.get_engine.cache_clear()


def names(r):
    return [i["name"] for i in r.json()["items"]]


def test_builds_newest_first_and_no_base(client):
    r = client.get("/api/builds")
    assert r.status_code == 200
    assert [b["label"] for b in r.json()] == ["b2", "b1"]  # released_at null sorts last
    b2 = r.json()[0]
    assert b2["prev_label"] == "b1" and b2["steam_buildid"] == "99" and b2["released_at"] == "2026-10-15"
    assert "T" in b2["ingested_at"] and ("+" in b2["ingested_at"] or "-" in b2["ingested_at"][11:])
    assert r.json()[1]["released_at"] is None and r.json()[1]["steam_buildid"] is None
    assert client.get("/api/builds/b0").status_code == 404
    assert client.get("/api/builds/b0/assets").json() == {"detail": "build not found"}


def test_totals_are_summed_from_areas(client):
    assert client.get("/api/builds").json()[0]["totals"] == {
        "added": 1, "removed": 1, "modified": 3, "moved": 1, "property_changes": 4, "removed_values": 2}


def test_build_detail_areas_and_types(client):
    body = client.get("/api/builds/b2").json()
    assert body["totals"]["modified"] == 3 and body["prev_label"] == "b1"
    assert [a["id"] for a in body["areas"]] == ["weapons-ammo", "vehicles"]
    assert body["areas"][0]["title"] == "Weapons & Ammo"
    assert body["types"] == [{"type": "WeaponData", "assets": 3}, {"type": "VehicleData", "assets": 2},
                             {"type": "BHAnimationSet", "assets": 1}]
    assert client.get("/api/builds/nope").status_code == 404


def test_status_order_then_name(client):
    r = client.get(W)
    assert r.json()["total"] == 6
    assert [(i["status"], i["name"]) for i in r.json()["items"]] == [
        ("modified", "DA_Jeep"), ("modified", "DA_Rifle"), ("modified", "DA_Truck"),
        ("moved", "AnimSet_Mosin"), ("added", "DA_Pistol"), ("removed", "DA_OldGun")]


def test_filters_alone_and_combined(client):
    assert names(client.get(W, params={"area": "vehicles"})) == ["DA_Jeep", "DA_Truck"]
    assert names(client.get(W, params={"status": "added"})) == ["DA_Pistol"]
    assert names(client.get(W, params={"type": "BHAnimationSet"})) == ["AnimSet_Mosin"]
    assert names(client.get(W, params={"q": "RIFLE"})) == ["DA_Rifle"]
    assert names(client.get(W, params={"q": "game/vehicles/"})) == ["DA_Jeep", "DA_Truck"]
    assert names(client.get(W, params={"area": "weapons-ammo", "status": "modified"})) == ["DA_Rifle"]
    r = client.get(W, params={"area": "weapons-ammo", "type": "WeaponData", "q": "da_", "field": "tags"})
    assert names(r) == ["DA_Rifle"] and r.json()["total"] == 1
    assert client.get(W, params={"area": "vehicles", "status": "added"}).json() == {"total": 0, "items": []}


def test_field_narrows_changes(client):
    r = client.get(W, params={"field": "basedamage"})
    assert r.json()["total"] == 1
    (item,) = r.json()["items"]
    assert [c["field_path"] for c in item["changes"]] == ["Properties.BaseDamage"]
    full = client.get(W, params={"q": "DA_Rifle"}).json()["items"][0]
    assert [c["field_path"] for c in full["changes"]] == ["Properties.BaseDamage", "Properties.Tags[2]"]


def test_pagination_total(client):
    r = client.get(W, params={"limit": 2, "offset": 2})
    assert r.json()["total"] == 6 and names(r) == ["DA_Truck", "AnimSet_Mosin"]
    assert client.get(W, params={"offset": 10}).json() == {"total": 6, "items": []}


def test_item_shape_old_path_and_null_before(client):
    items = {i["name"]: i for i in client.get(W).json()["items"]}
    mosin = items["AnimSet_Mosin"]
    assert mosin["old_path"] == "Game/Old/AnimSet_Mosin.json" and mosin["paired_by"] == "same-name"
    assert items["DA_Rifle"]["old_path"] is None
    assert items["DA_Pistol"]["exports"] == 3
    assert items["DA_Jeep"]["note"] == "hashed names only" and items["DA_Jeep"]["noise"]["renamed_keys"] == 4
    assert items["DA_Jeep"]["changes"] == []
    assert items["DA_Rifle"]["changes"][1] == {
        "field_path": "Properties.Tags[2]", "op": "added", "before": None, "after": "Tag.X"}
    assert items["DA_Truck"]["changes"][0]["before"] == ["a", "b"]


def test_json_values_returned_as_stored(client, db):
    with db.begin() as conn:
        conn.execute(update(change).where(change.c.field_path == "Properties.BaseDamage").values(before=60.0))
    r = client.get(W, params={"field": "BaseDamage"})
    assert '"before":60.0' in r.text and r.json()["items"][0]["changes"][0]["after"] == 45


@pytest.mark.parametrize("params", [{"status": "bogus"}, {"limit": 0}, {"limit": 201}, {"offset": -1}, {"limit": "x"}])
def test_bad_params_are_422(client, params):
    assert client.get(W, params=params).status_code == 422


@pytest.mark.parametrize("params, total", [
    ({"q": "%"}, 0), ({"q": "_"}, 6), ({"q": "\\"}, 0), ({"q": "DA%Rifle"}, 0), ({"q": "DA_Rifl_"}, 0),
    ({"q": "DA_Rifle"}, 1), ({"field": "Properties.%"}, 0), ({"field": "Tags[_]"}, 0), ({"field": "Tags[2]"}, 1),
])
def test_like_wildcards_are_literal(client, params, total):
    assert client.get(W, params=params).json()["total"] == total
