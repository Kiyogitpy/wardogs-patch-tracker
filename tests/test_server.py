import logging

import pytest
from starlette.testclient import TestClient

from tracker.api import app as api_app
from tracker.server import COMMON_HEADERS, Server, check_environment, _StripQuery


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
                 "font-src 'self' https://fonts.gstatic.com", "https://cdn.discordapp.com",
                 "object-src 'none'", "upgrade-insecure-requests"):
        assert part in csp
    # The web app sets style attributes (Hero.tsx, Odometer.tsx); without this they are blocked.
    assert "style-src 'self' 'unsafe-inline'" in csp and "script-src" not in csp
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


@pytest.mark.parametrize("method, path", [
    ("GET", "/"), ("GET", "/builds/x/changes"), ("GET", "/api/x"),
    ("GET", "/assets/app-abc123.js"), ("GET", "/favicon.svg"), ("HEAD", "/"), ("POST", "/"),
    ("DELETE", "/api/builds"),
])
def test_security_headers_on_every_response(client, method, path):
    r = client.request(method, path)
    for name, value in COMMON_HEADERS.items():
        assert r.headers[name] == value, (path, name)
    assert r.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"
    assert r.headers["x-frame-options"] == "DENY" and r.headers["cross-origin-opener-policy"] == "same-origin"


def test_not_built_has_headers(tmp_path):
    r = TestClient(Server(api_app, tmp_path)).get("/")
    assert r.status_code == 503 and r.headers["x-content-type-options"] == "nosniff"


def test_csp_on_html_only(client):
    assert "content-security-policy" in client.get("/").headers
    assert "content-security-policy" not in client.get("/api/x").headers
    assert "content-security-policy" not in client.get("/assets/app-abc123.js").headers


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"])
def test_api_docs_not_served_in_production(client, path):
    r = client.get(path)
    assert r.status_code == 200 and "spa" in r.text and "swagger" not in r.text.lower()
    assert "openapi" not in r.text.lower()
    assert client.post(path).status_code == 405


def test_access_log_drops_query_string():
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                               ("1.2.3.4:5", "GET", "/api/auth/callback?code=SECRET&state=S2", "1.1", 302), None)
    assert _StripQuery().filter(record)
    line = record.getMessage()
    assert "/api/auth/callback" in line and "SECRET" not in line and "?" not in line and "302" in line


def test_access_log_filter_is_installed(caplog):
    with caplog.at_level(logging.INFO, logger="uvicorn.access"):
        logging.getLogger("uvicorn.access").info('%s - "%s %s HTTP/%s" %d', "c", "GET", "/p?code=SECRET", "1.1", 200)
    assert "SECRET" not in caplog.text and '"GET /p HTTP' in caplog.text


GOOD = {"DATABASE_URL": "postgresql+psycopg://u:abc123@db/t", "PUBLIC_ORIGIN": "https://t.example.com"}


@pytest.mark.parametrize("env, message", [
    ({**GOOD, "DATABASE_URL": "postgresql+psycopg://u:change-me@db/t"}, "DATABASE_URL"),
    ({**GOOD, "MIGRATE_DATABASE_URL": "postgresql+psycopg://u:change-me@db/t"}, "MIGRATE_DATABASE_URL"),
    ({"DATABASE_URL": GOOD["DATABASE_URL"]}, "PUBLIC_ORIGIN is not set"),
    ({**GOOD, "PUBLIC_ORIGIN": "http://t.example.com"}, "must be https"),
    ({**GOOD, "PUBLIC_ORIGIN": "http://localhost:8000"}, "COOKIE_SECURE=0"),
    ({**GOOD, "PUBLIC_ORIGIN": "http://localhost.evil.com", "COOKIE_SECURE": "0"}, "must be https"),
])
def test_check_environment_refuses(env, message):
    with pytest.raises(RuntimeError, match=message):
        check_environment(env)


@pytest.mark.parametrize("env", [
    GOOD,
    {**GOOD, "PUBLIC_ORIGIN": "http://localhost:8000", "COOKIE_SECURE": "0"},
    {**GOOD, "PUBLIC_ORIGIN": "http://localhost", "COOKIE_SECURE": "0"},
])
def test_check_environment_accepts(env):
    check_environment(env)


def test_startup_refuses_placeholder(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:change-me@db/t")
    monkeypatch.setenv("PUBLIC_ORIGIN", "https://t.example.com")
    with pytest.raises(RuntimeError, match="change-me"):
        with TestClient(Server(api_app, tmp_path)):
            pass


def test_startup_ok(tmp_path, monkeypatch):
    for k, v in GOOD.items():
        monkeypatch.setenv(k, v)
    with TestClient(Server(api_app, tmp_path)) as c:
        assert c.get("/").status_code == 503
