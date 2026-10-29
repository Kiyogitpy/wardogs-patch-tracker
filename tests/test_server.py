import pytest
from starlette.testclient import TestClient

from tracker.api import app as api_app
from tracker.server import Server


@pytest.fixture
def client(tmp_path):
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<html>spa</html>")
    (root / "assets" / "app-abc123.js").write_text("console.log(1)")
    (root / "favicon.svg").write_text("<svg/>")
    (tmp_path / "secret.txt").write_text("secret")
    return TestClient(Server(api_app, root))


def test_index_and_headers(client):
    r = client.get("/")
    assert r.status_code == 200 and "spa" in r.text
    assert r.headers["cache-control"] == "no-cache"
    csp = r.headers["content-security-policy"]
    for part in ("default-src 'self'", "frame-ancestors 'none'", "https://fonts.googleapis.com",
                 "https://fonts.gstatic.com", "https://cdn.discordapp.com"):
        assert part in csp
    assert r.headers["referrer-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["permissions-policy"]


def test_spa_fallback(client):
    r = client.get("/builds/s2-cl516586/changes")
    assert r.status_code == 200 and "spa" in r.text
    assert "content-security-policy" in r.headers


def test_hashed_asset_is_immutable(client):
    r = client.get("/assets/app-abc123.js")
    assert r.status_code == 200 and r.text == "console.log(1)"
    assert "immutable" in r.headers["cache-control"] and "max-age=31536000" in r.headers["cache-control"]


def test_other_file_short_cache(client):
    r = client.get("/favicon.svg")
    assert r.status_code == 200 and r.headers["cache-control"] == "public, max-age=3600"


def test_missing_asset_falls_back_to_index(client):
    assert "spa" in client.get("/assets/missing.js").text


def test_api_unknown_is_json_404(client):
    r = client.get("/api/x")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")
    assert "spa" not in r.text


@pytest.mark.parametrize("path", [
    "/..%2fsecret.txt", "/%2e%2e/secret.txt", "/..%2f..%2fsecret.txt", "/assets/..%2f..%2fsecret.txt",
    "/%2e%2e%2fsecret.txt", "/..%252fsecret.txt", "/assets/%00",
])
def test_traversal_never_leaks(client, path):
    r = client.get(path)
    assert "secret" not in r.text


def test_directory_not_listed(client):
    r = client.get("/assets")
    assert "app-abc123" not in r.text and "spa" in r.text
    assert "app-abc123" not in client.get("/assets/").text


def test_post_outside_api_rejected(client):
    assert client.post("/").status_code == 405


def test_head(client):
    assert client.head("/").status_code == 200


def test_not_built(tmp_path):
    assert TestClient(Server(api_app, tmp_path)).get("/").status_code == 503
