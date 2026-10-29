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


@pytest.mark.parametrize("path", ["/API/x", "/Api/builds", "/API", "/apix", "/api-x/y"])
def test_api_prefix_is_case_sensitive(client, path):
    r = client.get(path)
    assert r.status_code == 200 and "spa" in r.text  # static fallback, never the API


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


STRONG = "0123456789abcdef0123456789abcdef"
GOOD = {"DATABASE_URL": f"postgresql+psycopg://u:{STRONG}@db/t", "PUBLIC_ORIGIN": "https://t.example.com"}


def origin(value, **extra):
    return {**GOOD, "PUBLIC_ORIGIN": value, **extra}


@pytest.mark.parametrize("env, message", [
    ({**GOOD, "DATABASE_URL": "postgresql+psycopg://u:change-me@db/t"}, "DATABASE_URL"),
    ({**GOOD, "DATABASE_URL": "postgresql+psycopg://u:CHANGE-ME@db/t"}, "DATABASE_URL"),
    ({**GOOD, "DATABASE_URL": "postgresql+psycopg://u:ChangeMe@db/t"}, "DATABASE_URL"),
    ({**GOOD, "DATABASE_URL": "postgresql+psycopg://u:PASSWORD@db/t"}, "DATABASE_URL"),
    ({**GOOD, "DATABASE_URL": "postgresql+psycopg://u:@db/t"}, "DATABASE_URL"),
    ({**GOOD, "DATABASE_URL": "postgresql+psycopg://u:%20%20@db/t"}, "DATABASE_URL"),
    ({**GOOD, "DATABASE_URL": "postgresql+psycopg://u@db/t"}, "DATABASE_URL"),
    ({**GOOD, "MIGRATE_DATABASE_URL": "postgresql+psycopg://u:change-me@db/t"}, "MIGRATE_DATABASE_URL"),
    ({**GOOD, "MIGRATE_DATABASE_URL": "postgresql+psycopg://u:Password@db/t"}, "MIGRATE_DATABASE_URL"),
    ({**GOOD, "DATABASE_URL": f"mysql://u:{STRONG}@db/t"}, "scheme"),
    ({**GOOD, "DATABASE_URL": f"postgresql+asyncpg://u:{STRONG}@db/t"}, "scheme"),
    ({**GOOD, "DATABASE_URL": f"sqlite://u:{STRONG}@db/t"}, "scheme"),
    ({**GOOD, "MIGRATE_DATABASE_URL": f"postgresql+psycopg://o:{STRONG}@db/t"}, "different passwords"),
    ({**GOOD, "DATABASE_URL": f"postgresql://u:{STRONG}@db/t", "MIGRATE_DATABASE_URL": f"postgresql://o:{STRONG}@db/t"},
     "different passwords"),
    (origin("https://t.example.com", DOMAIN="other.example.com"), "does not match DOMAIN"),
    (origin("https://sub.t.example.com", DOMAIN="t.example.com"), "does not match DOMAIN"),
    ({"DATABASE_URL": GOOD["DATABASE_URL"]}, "PUBLIC_ORIGIN is not set"),
    (origin("http://t.example.com"), "must be https"),
    (origin("http://localhost:8000"), "COOKIE_SECURE=0"),
    (origin("http://localhost.evil.com", COOKIE_SECURE="0"), "must be https"),
    # Reviewer's bypasses:
    (origin("https://evil.com/path/x"), "must not have a path"),
    (origin("https://user:pw@evil.com"), "user info"),
    (origin("https://?x"), "PUBLIC_ORIGIN"),
    (origin("https://a b"), "whitespace"),
    (origin("https://t.example.com "), "whitespace"),
    (origin(" https://t.example.com"), "whitespace"),
    (origin("https://t.example.com\\@evil.com"), "backslash"),
    (origin("https://t.example.com?x=1"), "query"),
    (origin("https://t.example.com#f"), "fragment"),
    (origin("https://t.example.com:notaport"), "PUBLIC_ORIGIN"),
    (origin("https://"), "PUBLIC_ORIGIN"),
    (origin("ftp://t.example.com"), "must be https"),
    (origin("http://localhost:80@evil.com", COOKIE_SECURE="0"), "user info"),
    (origin("http://localhost:@evil.com", COOKIE_SECURE="0"), "user info"),
    (origin("http://localhost@evil.com", COOKIE_SECURE="0"), "user info"),
    (origin("https://t.example.com", COOKIE_SECURE="0"), "COOKIE_SECURE=0"),
    (origin("https://localhost", COOKIE_SECURE="0"), "COOKIE_SECURE=0"),
    (origin("http://localhost.evil.com"), "must be https"),
    # Third review: hostnames that Caddy or a browser would read differently than the app.
    (origin("https://example.com;"), "invalid hostname"),
    (origin('https://example.com"'), "invalid hostname"),
    (origin("https://*.example.com"), "invalid hostname"),
    (origin("https://."), "invalid hostname"),
    (origin("https://-"), "invalid hostname"),
    (origin("https://example.com%2f"), "invalid hostname"),
    (origin("https://example.com:"), "empty port"),
    (origin("https://example.com:0"), "port 0"),
    (origin("https://example.com:65536"), "not a valid URL"),
    (origin("https://-a.example.com"), "invalid hostname"),
    (origin("https://a-.example.com"), "invalid hostname"),
    (origin("https://example..com"), "invalid hostname"),
    (origin("https://example.com."), "invalid hostname"),
    (origin("https://nodots"), "invalid hostname"),
    (origin("https://" + "a" * 64 + ".example.com"), "invalid hostname"),
    (origin("https://" + ".".join(["a" * 60] * 5)), "invalid hostname"),
    (origin("https://exa_mple.com"), "invalid hostname"),
])
def test_check_environment_refuses(env, message):
    with pytest.raises(RuntimeError, match=message):
        check_environment(env)


@pytest.mark.parametrize("password", ["admin", "tracker", "postgres", "a", "Password1", "x" * 15])
@pytest.mark.parametrize("name", ["DATABASE_URL", "MIGRATE_DATABASE_URL"])
def test_check_environment_refuses_weak_password(name, password):
    env = {**GOOD, name: f"postgresql+psycopg://u:{password}@db/t"}
    with pytest.raises(RuntimeError, match=f"{name} password is shorter than 16"):
        check_environment(env)


def test_check_environment_refuses_identical_passwords_across_roles():
    env = {**GOOD, "MIGRATE_DATABASE_URL": GOOD["DATABASE_URL"].replace("u:", "tracker:")}
    with pytest.raises(RuntimeError, match="different passwords"):
        check_environment(env)


@pytest.mark.parametrize("env", [
    GOOD,
    {**GOOD, "MIGRATE_DATABASE_URL": f"postgresql+psycopg://o:{STRONG[::-1]}@db/t"},
    {**GOOD, "DATABASE_URL": f"postgresql://u:{STRONG}@db/t"},
    {**GOOD, "DOMAIN": "t.example.com"},
    {**GOOD, "DOMAIN": "T.Example.com"},
    origin("https://203.0.113.7:8443"),
    origin("https://[2001:db8::1]"),
    origin("https://xn--bcher-kva.example"),
    origin("https://" + ".".join(["a" * 50] * 4)),
    origin("https://t.example.com/"),
    origin("https://t.example.com:8443"),
    origin("https://localhost.evil.com"),  # https is fine; it is just not "localhost over http"
    origin("https://localhost", COOKIE_SECURE="1"),
    {**GOOD, "PUBLIC_ORIGIN": "http://localhost:8000", "COOKIE_SECURE": "0"},
    {**GOOD, "PUBLIC_ORIGIN": "http://localhost", "COOKIE_SECURE": "0"},
    {**GOOD, "PUBLIC_ORIGIN": "http://127.0.0.1:8000", "COOKIE_SECURE": "0"},
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
