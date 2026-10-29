"""Hostile input: lone surrogates, NaN, a dripping Discord, the logout rate bucket, 503 logging."""
import hashlib
import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone

import httpx
import psycopg.errors
import pytest
import sqlalchemy.exc
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import insert, text

from tracker import accounts, api
from tracker.db import app_user, session
from tracker.ingest import ingest
from tracker.webhooks import Utf8, utf8_ok

ORIGIN = "https://tracker.test"
HOOK = "https://discord.com/api/webhooks/1234567890/abcdefghijk3Zq"
JSON = {"Origin": ORIGIN, "Content-Type": "application/json"}
SESSION = "__Host-session"
BAD = "a\ud800"  # a lone surrogate: JSON can carry it, UTF-8 cannot
ESC = "a\\ud800"  # the same, as JSON text


@pytest.fixture
def world(db, fixtures, monkeypatch):
    with db.begin() as conn:
        ingest(conn, fixtures / "diff_b1", "b1", "b0")
        ingest(conn, fixtures / "diff_b2", "b2", "b1", released_at=date(2026, 10, 15))
    monkeypatch.setenv("DATABASE_URL", db.url.render_as_string(hide_password=False))
    monkeypatch.setenv("PUBLIC_ORIGIN", ORIGIN)
    monkeypatch.setenv("DISCORD_CLIENT_ID", "id")
    monkeypatch.setenv("DISCORD_CLIENT_SECRET", "secret")
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    api.get_engine.cache_clear()
    yield db
    api.get_engine().dispose()
    api.get_engine.cache_clear()


@pytest.fixture
def user(world):
    """A client with a live session, made directly in the database."""
    with world.begin() as conn:
        uid = conn.execute(insert(app_user).values(discord_id="42", username="kiyo").returning(app_user.c.id)).scalar_one()
        conn.execute(insert(session).values(token_hash=hashlib.sha256(b"tok").hexdigest(), user_id=uid,
                                            expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
    client = TestClient(api.app, base_url=ORIGIN, raise_server_exceptions=False)
    client.cookies.set(SESSION, "tok")
    return client


def raw_json(client, method, path, body: str):
    return client.request(method, path, content=body.encode(), headers=JSON)


# --- fix 1: lone surrogates -------------------------------------------------------------------------------------

@pytest.mark.parametrize("body", [
    '{"kind": "asset", "asset_path": "%s", "webhook_url": "%s"}' % (ESC, HOOK),
    '{"kind": "area", "target": "%s", "webhook_url": "%s"}' % (ESC, HOOK),
    '{"kind": "field", "target": "%s", "webhook_url": "%s"}' % (ESC, HOOK),
    '{"kind": "asset", "asset_path": "x", "webhook_url": "https://discord.com/api/webhooks/1/a\\udfff"}',
    '{"kind": "area", "target": "weapons-ammo", "webhook_url": "%s", "%s": 1}' % (HOOK, ESC),  # in a key
    '{"kind": "%s", "target": "x", "webhook_url": "%s"}' % (ESC, HOOK),  # in a Literal field
])
def test_surrogates_in_subscription_bodies_are_422(user, body):
    r = raw_json(user, "POST", "/api/subscriptions", body)
    assert r.status_code == 422
    r.json()  # the error body itself is valid JSON


def test_every_text_parameter_is_validated_as_utf8():
    """Query and path parameters cannot carry a surrogate over HTTP (the server decodes them with replacement),
    but each str one carries the check, so no route can regress into a 500."""
    checked = []
    for route in api.app.routes:
        if not isinstance(route, APIRoute):
            continue
        for field in [*route.dependant.query_params, *route.dependant.path_params]:
            if "str" not in str(field.field_info.annotation):
                continue
            checked.append((route.path, field.name))
            assert Utf8 in field.field_info.metadata, (route.path, field.name)
    assert len(checked) >= 10


def test_utf8_ok():
    assert utf8_ok("fine é \U0001f600") == "fine é \U0001f600" and utf8_ok(None) is None
    with pytest.raises(ValueError):
        utf8_ok(BAD)


@pytest.mark.parametrize("path", [
    "/api/builds/b1/assets?q=%ED%A0%80", "/api/assets?q=%ED%A0%80", "/api/asset?path=%ED%A0%80",
    "/api/changes?field=%ED%A0%80", "/api/builds/%ED%A0%80", "/api/auth/login?next=%ED%A0%80",
])
def test_invalid_percent_encoding_is_never_a_500(user, path):
    assert user.get(path).status_code < 500


# --- fix 1: NaN and Infinity ------------------------------------------------------------------------------------

@pytest.mark.parametrize("body", ["NaN", "Infinity", "-Infinity", '{"kind": NaN}',
                                  '{"kind": "asset", "target": Infinity}', '{"kind": ["x", -Infinity]}'])
def test_non_finite_json_is_422_not_500(user, body):
    r = raw_json(user, "POST", "/api/subscriptions", body)
    assert r.status_code == 422, r.text
    assert r.json()["detail"][0]["type"] == "json_invalid"


def test_validation_errors_do_not_echo_input(user):
    secret = "S" * 5000
    r = user.post("/api/subscriptions", json={"kind": secret, "webhook_url": HOOK}, headers=JSON)
    assert r.status_code == 422 and secret not in r.text and len(r.text) < 1000
    e = r.json()["detail"][0]
    assert set(e) == {"type", "loc", "msg"} and e["loc"] == ["body", "kind"]


def test_query_validation_errors_keep_their_shape(user):
    r = user.get("/api/assets?limit=0")
    assert r.status_code == 422 and r.json()["detail"][0]["loc"] == ["query", "limit"]


# --- fix 2: wall-clock deadline for the Discord exchange --------------------------------------------------------

class Drip(httpx.SyncByteStream):
    """A body that sends one byte every `gap` real seconds."""

    def __init__(self, gap, count=200):
        self.gap, self.count, self.sent = gap, count, 0

    def __iter__(self):
        for _ in range(self.count):
            time.sleep(self.gap)
            self.sent += 1
            yield b" "

    def close(self):
        pass


def discord_env(monkeypatch):
    monkeypatch.setenv("DISCORD_CLIENT_ID", "id")
    monkeypatch.setenv("DISCORD_CLIENT_SECRET", "secret")
    monkeypatch.setenv("PUBLIC_ORIGIN", ORIGIN)


def test_dripping_discord_is_abandoned_at_the_wall_clock_deadline(monkeypatch):
    drips = []

    def handler(request):
        drips.append(Drip(0.05))
        return httpx.Response(200, stream=drips[-1])

    monkeypatch.setattr(accounts, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(accounts, "EXCHANGE_DEADLINE", 0.3)
    discord_env(monkeypatch)

    start = time.monotonic()
    assert accounts._exchange("code") is None
    elapsed = time.monotonic() - start
    assert elapsed < 0.3 + 0.25, elapsed
    assert accounts._exchanges == 0  # the slot is free again
    time.sleep(0.3)
    assert drips[0].sent < 20  # the abandoned worker stopped reading instead of draining 200 chunks
    assert [t for t in threading.enumerate() if t.name == "discord-exchange"] == []


def test_oversized_discord_body_is_refused(monkeypatch):
    big = httpx.Response(200, content=b" " * (accounts.MAX_RESPONSE + 1))
    monkeypatch.setattr(accounts, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(lambda r: big)))
    discord_env(monkeypatch)
    assert accounts._discord_user("code") is None


def test_a_stuck_discord_call_cannot_create_a_session(world, monkeypatch):
    """The callback gives up at the deadline; the late worker only returns data and writes nothing."""
    release = threading.Event()

    def stuck(code):
        release.wait(2)
        return {"id": "9", "username": "late"}

    monkeypatch.setattr(accounts, "_fetch_profile", stuck)
    monkeypatch.setattr(accounts, "EXCHANGE_DEADLINE", 0.2)
    assert accounts._exchange("code") is None
    release.set()
    time.sleep(0.1)
    with world.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM session")).scalar_one() == 0


# --- fix 3: logout uses the general bucket ----------------------------------------------------------------------

def test_logout_is_not_throttled_by_sign_in_attempts(user, monkeypatch):
    monkeypatch.setattr(api, "RATE_AUTH", (6, 2))
    monkeypatch.setattr(api, "RATE_API", (10**9, 10**9))
    for _ in range(2):
        user.get("/api/auth/login")
    assert user.get("/api/auth/login").status_code == 429  # sign-in is throttled
    assert user.post("/api/auth/logout-all", headers=JSON).status_code == 204
    assert user.post("/api/auth/logout", headers=JSON).status_code == 204


# --- fix 7: 503 mappings are logged without parameters ----------------------------------------------------------

class BrokenEngine:
    def __init__(self, error):
        self.error = error

    def connect(self):
        raise self.error


@pytest.mark.parametrize("orig, sqlstate", [
    (psycopg.errors.DiskFull("disk"), "53100"),
    (psycopg.errors.QueryCanceled("canceled"), "57014"),
])
def test_database_503_is_logged_with_class_and_sqlstate_only(orig, sqlstate, monkeypatch, caplog):
    error = sqlalchemy.exc.OperationalError("SELECT secret FROM t WHERE x = %(x)s", {"x": "hunter2"}, orig)
    monkeypatch.setattr(api, "get_engine", lambda: BrokenEngine(error))
    with caplog.at_level(logging.WARNING, logger="tracker.api"):
        assert TestClient(api.app, raise_server_exceptions=False).get("/api/builds").status_code == 503
    [record] = [r for r in caplog.records if r.name == "tracker.api"]
    message = record.getMessage()
    assert record.levelno == logging.WARNING and type(orig).__name__ in message and sqlstate in message
    assert "hunter2" not in message and "SELECT" not in message


def test_pool_timeout_503_is_logged(monkeypatch, caplog):
    monkeypatch.setattr(api, "get_engine", lambda: BrokenEngine(sqlalchemy.exc.TimeoutError("QueuePool limit")))
    with caplog.at_level(logging.WARNING, logger="tracker.api"):
        assert TestClient(api.app, raise_server_exceptions=False).get("/api/builds").status_code == 503
    assert any("TimeoutError" in r.getMessage() for r in caplog.records if r.name == "tracker.api")
