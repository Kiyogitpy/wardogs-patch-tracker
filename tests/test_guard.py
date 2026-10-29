"""Body cap, rate limiting and statement timeout in front of the API."""
import psycopg.errors
import pytest
import sqlalchemy.exc
from fastapi.testclient import TestClient
from sqlalchemy import text

from tracker import api

ORIGIN = "https://tracker.test"
JSON = {"Origin": ORIGIN, "Content-Type": "application/json"}


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", db.url.render_as_string(hide_password=False))
    monkeypatch.setenv("PUBLIC_ORIGIN", ORIGIN)
    api.get_engine.cache_clear()
    yield TestClient(api.app, base_url=ORIGIN)
    api.get_engine().dispose()
    api.get_engine.cache_clear()


# --- body cap ---------------------------------------------------------------------------------------------------

def test_content_length_over_the_cap_is_413(client):
    r = client.post("/api/subscriptions", content=b"x" * (64 * 1024 + 1), headers=JSON)
    assert r.status_code == 413 and r.json() == {"detail": "request body too large"}


def test_declared_huge_body_is_rejected_before_it_is_read(client):
    # Only the header is sent; the middleware must answer without waiting for 200 MB (and before auth).
    r = client.post("/api/subscriptions", headers={**JSON, "Content-Length": str(200 * 1024 * 1024)}, content=b"{}")
    assert r.status_code == 413


def test_chunked_body_over_the_cap_is_413(client):
    def chunks():
        for _ in range(100):
            yield b"x" * 1024  # no Content-Length: the stream is counted

    r = client.post("/api/subscriptions", content=chunks(), headers=JSON)
    assert r.status_code == 413 and r.json() == {"detail": "request body too large"}


def test_body_under_the_cap_still_reaches_auth(client):
    assert client.post("/api/subscriptions", json={"kind": "asset"}, headers=JSON).status_code == 401
    def chunks():
        yield b'{"kind": "asset"}'
    assert client.post("/api/subscriptions", content=chunks(), headers=JSON).status_code == 401


# --- rate limiting ----------------------------------------------------------------------------------------------

def test_auth_routes_are_limited_per_ip(client, monkeypatch):
    monkeypatch.setattr(api, "RATE_AUTH", (6, 2))  # 6 a minute, burst 2: one token per 10 s
    monkeypatch.setattr(api, "RATE_API", (10**9, 10**9))
    assert [client.get("/api/auth/login").status_code for _ in range(2)] == [503, 503]  # reaches the route
    r = client.get("/api/auth/login")
    assert r.status_code == 429 and r.json() == {"detail": "too many requests"}
    assert 1 <= int(r.headers["retry-after"]) <= 10
    assert client.get("/api/builds").status_code == 200  # other routes have their own bucket


def test_other_api_routes_are_limited_and_tokens_refill(client, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(api, "_now", lambda: clock[0])
    monkeypatch.setattr(api, "RATE_API", (60, 3))  # one token a second, burst 3
    assert [client.get("/api/builds").status_code for _ in range(3)] == [200] * 3
    r = client.get("/api/builds")
    assert r.status_code == 429 and r.headers["retry-after"] == "1"
    clock[0] += 2
    assert client.get("/api/builds").status_code == 200


def test_non_api_paths_are_not_limited(client, monkeypatch):
    monkeypatch.setattr(api, "RATE_API", (1, 1))
    for _ in range(5):
        assert client.get("/not-the-api").status_code == 404


def test_idle_buckets_are_evicted_and_the_count_is_bounded(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(api, "_now", lambda: clock[0])
    monkeypatch.setattr(api, "MAX_BUCKETS", 3)
    for i in range(10):
        api._take(f"10.0.0.{i}", "api", 120, 60)
    assert len(api._buckets) == 3 and ("10.0.0.9", "api") in api._buckets  # oldest dropped first
    clock[0] += api.BUCKET_IDLE + 1
    api._take("10.0.0.99", "api", 120, 60)
    assert list(api._buckets) == [("10.0.0.99", "api")]  # everything idle went


# --- statement timeout ------------------------------------------------------------------------------------------

def test_api_engine_has_a_statement_timeout(client):
    with api.get_engine().connect() as conn:
        assert conn.execute(text("SHOW statement_timeout")).scalar_one() == "3s"


def test_slow_query_is_503_not_500(client, monkeypatch):
    monkeypatch.setattr(api, "STATEMENT_TIMEOUT_MS", 100)
    api.get_engine.cache_clear()

    @api.app.get("/api/_slow")
    def slow():
        with api.get_engine().connect() as conn:
            conn.execute(text("SELECT pg_sleep(2)"))

    try:
        r = client.get("/api/_slow")
    finally:
        api.app.router.routes[:] = [x for x in api.app.router.routes if getattr(x, "path", "") != "/api/_slow"]
    assert r.status_code == 503 and r.json() == {"detail": "query took too long, narrow the search"}


# --- database errors under load ---------------------------------------------------------------------------------

BUSY = {"detail": "the database is busy, try again shortly"}
SLOW = {"detail": "query took too long, narrow the search"}


class BrokenEngine:
    def __init__(self, error):
        self.error = error

    def connect(self):
        raise self.error


@pytest.mark.parametrize("orig, body", [
    (psycopg.errors.DiskFull("could not resize shared memory segment"), BUSY),
    (psycopg.errors.TooManyConnections("sorry, too many clients already"), BUSY),
    (psycopg.OperationalError("server closed the connection unexpectedly"), BUSY),
    (psycopg.errors.QueryCanceled("canceling statement due to statement timeout"), SLOW),
])
def test_database_operational_errors_are_503(orig, body, monkeypatch):
    error = sqlalchemy.exc.OperationalError("SELECT 1", {}, orig)
    monkeypatch.setattr(api, "get_engine", lambda: BrokenEngine(error))
    r = TestClient(api.app, raise_server_exceptions=False).get("/api/builds")
    assert r.status_code == 503 and r.json() == body


def test_pool_timeout_is_503(monkeypatch):
    error = sqlalchemy.exc.TimeoutError("QueuePool limit of size 5 overflow 10 reached")
    monkeypatch.setattr(api, "get_engine", lambda: BrokenEngine(error))
    r = TestClient(api.app, raise_server_exceptions=False).get("/api/builds")
    assert r.status_code == 503 and r.json() == BUSY


def test_other_database_errors_stay_500(monkeypatch):
    error = sqlalchemy.exc.ProgrammingError("SELECT 1", {}, psycopg.errors.UndefinedTable("no such table"))
    monkeypatch.setattr(api, "get_engine", lambda: BrokenEngine(error))
    assert TestClient(api.app, raise_server_exceptions=False).get("/api/builds").status_code == 500


def test_pool_waits_at_most_5_seconds(client):
    assert api.get_engine().pool.timeout() == 5


# --- rate limit buckets -----------------------------------------------------------------------------------------

def _statuses(monkeypatch, *ips):
    import asyncio
    monkeypatch.setattr(api, "RATE_API", (60, 2))  # burst 2, a token a second

    async def inner(scope, receive, send):
        await api._reply(send, 200, "ok")

    async def run():
        out = []
        for ip in ips:
            sent = []

            async def send(message):
                sent.append(message)

            await api.Guard(inner)({"type": "http", "path": "/api/builds", "headers": [], "client": (ip, 1)}, None, send)
            out.append(sent[0]["status"])
        return out

    return asyncio.run(run())


def test_ipv6_addresses_in_one_slash_64_share_a_bucket(monkeypatch):
    got = _statuses(monkeypatch, "2001:db8:1:2::1", "2001:db8:1:2:ffff:ffff:ffff:ffff", "2001:db8:1:2:1:2:3:4",
                    "2001:db8:1:3::1")
    assert got == [200, 200, 429, 200]  # the third is in the first one's /64; the fourth is in another
    assert ("2001:db8:1:2::/64", "api") in api._buckets


def test_ipv4_is_limited_per_address_and_mapped_ipv6_is_ipv4(monkeypatch):
    got = _statuses(monkeypatch, "192.0.2.1", "192.0.2.2", "192.0.2.1", "::ffff:192.0.2.1", "192.0.2.2")
    assert got == [200, 200, 200, 429, 200]
