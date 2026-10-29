import base64
import hashlib
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, text, update

from tracker import accounts, api
from tracker.db import build, delivery, session, subscription
from tracker.ingest import ingest

ORIGIN = "https://tracker.test"
HOOK = "https://discord.com/api/webhooks/1234567890/abcdefghijk3Zq"
RIFLE = "Game/Weapons/DA_Rifle.json"
OLD = "Game/Old/AnimSet_Mosin.json"
NEW = "Game/New/AnimSet_Mosin.json"
ME = {"id": "80351110224678912", "username": "kiyo", "avatar": "abc123"}
HEADERS = {"Origin": ORIGIN}
SESSION, STATE = "__Host-session", "__Host-oauth_state"  # COOKIE_SECURE is on in these tests


@pytest.fixture
def world(db, fixtures, monkeypatch):
    with db.begin() as conn:
        conn.execute(text("TRUNCATE app_user, session, subscription, delivery RESTART IDENTITY CASCADE"))
        ingest(conn, fixtures / "diff_b1", "b1", "b0")
        ingest(conn, fixtures / "diff_b2", "b2", "b1", released_at=date(2026, 10, 15))
        ingest(conn, fixtures / "diff_b3", "b3", "b2", released_at=date(2026, 11, 1))
    monkeypatch.setenv("DATABASE_URL", db.url.render_as_string(hide_password=False))
    monkeypatch.setenv("PUBLIC_ORIGIN", ORIGIN)
    monkeypatch.setenv("DISCORD_CLIENT_ID", "fake-client-id")
    monkeypatch.setenv("DISCORD_CLIENT_SECRET", "fake-client-secret")
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    api.get_engine.cache_clear()
    yield db
    api.get_engine().dispose()
    api.get_engine.cache_clear()


@pytest.fixture
def discord(monkeypatch):
    """A mocked Discord. state["profile"] is what /users/@me returns; state["fail"] makes the token call fail."""
    state = {"profile": dict(ME), "fail": False, "requests": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        if request.url.path == "/api/oauth2/token":
            if state["fail"]:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": "fake-access", "token_type": "Bearer"})
        if request.url.path == "/api/users/@me":
            assert request.headers["authorization"] == "Bearer fake-access"
            return httpx.Response(200, json=state["profile"])
        return httpx.Response(404)

    monkeypatch.setattr(accounts, "http_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(handler), timeout=10.0))
    return state


def new_client():
    return TestClient(api.app, base_url=ORIGIN, follow_redirects=False)


@pytest.fixture
def client(world, discord):
    return new_client()


def sign_in(client, discord, profile=None, next_path=None):
    discord["profile"] = profile or dict(ME)
    r = client.get("/api/auth/login", params={"next": next_path} if next_path else {})
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    return client.get("/api/auth/callback", params={"code": "fake-code", "state": state})


@pytest.fixture
def user(client, discord):
    assert sign_in(client, discord).status_code == 302
    return client


def post(client, kind="asset", **kw):
    body = {"kind": kind, "webhook_url": HOOK}
    if kind == "asset":
        body["asset_path"] = RIFLE
    else:
        body["target"] = "weapons-ammo" if kind == "area" else "BaseDamage"
    body.update(kw)
    return client.post("/api/subscriptions", json=body, headers=HEADERS)


def count(db, table):
    with db.connect() as conn:
        return conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()


def cookie_attrs(response, name):
    """The attributes of the Set-Cookie header for `name`, lower-cased; valueless flags map to True."""
    header = next(h for h in response.headers.get_list("set-cookie") if h.startswith(name + "="))
    parts = [p.strip() for p in header.split(";")][1:]
    return {p.split("=")[0].lower(): (p.split("=", 1)[1] if "=" in p else True) for p in parts}


# --- sign-in flow -----------------------------------------------------------------------------------------------

def test_login_redirects_to_discord(client):
    r = client.get("/api/auth/login", params={"next": "/asset?path=x"})
    assert r.status_code == 302
    url = urlparse(r.headers["location"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://discord.com/oauth2/authorize"
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert q["client_id"] == "fake-client-id" and q["response_type"] == "code" and q["scope"] == "identify"
    assert q["redirect_uri"] == ORIGIN + "/api/auth/callback" and q["prompt"] == "none"
    assert "fake-client-secret" not in r.headers["location"]
    assert client.cookies.get(STATE).startswith(q["state"] + ".")


def test_full_flow(client, discord, world):
    r = sign_in(client, discord, next_path="/asset?path=x")
    assert r.status_code == 302 and r.headers["location"] == "/asset?path=x"
    token_call, me_call = discord["requests"]
    assert b"code=fake-code" in token_call.content and b"fake-client-secret" in token_call.content
    assert STATE not in client.cookies

    assert client.get("/api/me").json() == {
        "discord_id": ME["id"], "username": "kiyo",
        "avatar_url": f"https://cdn.discordapp.com/avatars/{ME['id']}/abc123.png"}

    # Signing in again refreshes the profile instead of duplicating the user.
    sign_in(client, discord, {**ME, "username": "kiyo2", "avatar": None})
    assert client.get("/api/me").json()["username"] == "kiyo2"
    assert client.get("/api/me").json()["avatar_url"] is None
    assert count(world, "app_user") == 1 and count(world, "session") == 2

    assert client.post("/api/auth/logout", headers=HEADERS).status_code == 204
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me").json() == {"detail": "not signed in"}
    assert count(world, "session") == 1  # only the current session was deleted


def test_logout_when_signed_out(client):
    assert client.post("/api/auth/logout", headers=HEADERS).status_code == 204


def test_database_stores_only_the_hash(user, world):
    token = user.cookies.get(SESSION)
    with world.connect() as conn:
        row = conn.execute(select(session)).one()
    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest() and token != row.token_hash
    assert row.expires_at - datetime.now(timezone.utc) > timedelta(days=29)


@pytest.mark.parametrize("variant", ["wrong_state", "no_state", "no_code", "nothing"])
def test_bad_or_missing_state_creates_no_session(client, discord, world, variant):
    client.get("/api/auth/login")
    good = client.cookies.get(STATE).split(".")[0]
    params = {"wrong_state": {"code": "c", "state": "wrong"}, "no_state": {"code": "c"},
              "no_code": {"state": good}, "nothing": {}}[variant]
    r = client.get("/api/auth/callback", params=params)
    assert r.status_code == 302 and r.headers["location"] == "/?signin=failed"
    assert count(world, "session") == 0 and count(world, "app_user") == 0
    assert discord["requests"] == []


def test_callback_without_state_cookie(world, discord):
    r = new_client().get("/api/auth/callback", params={"code": "c", "state": "anything"})
    assert r.headers["location"] == "/?signin=failed" and count(world, "session") == 0


def test_access_denied(client, discord, world):
    client.get("/api/auth/login")
    state = client.cookies.get(STATE).split(".")[0]
    r = client.get("/api/auth/callback", params={"error": "access_denied", "state": state})
    assert r.headers["location"] == "/?signin=failed"
    assert count(world, "session") == 0 and discord["requests"] == []


def test_discord_error_fails_without_session(client, discord, world):
    discord["fail"] = True
    r = sign_in(client, discord)
    assert r.headers["location"] == "/?signin=failed" and count(world, "session") == 0


def test_malformed_discord_profile_fails(client, discord, world):
    r = sign_in(client, discord, {"id": "not-a-number", "username": "x"})
    assert r.headers["location"] == "/?signin=failed" and count(world, "app_user") == 0


def test_state_cannot_be_replayed_by_another_client(client, discord, world):
    client.get("/api/auth/login")
    state = client.cookies.get(STATE).split(".")[0]
    r = new_client().get("/api/auth/callback", params={"code": "c", "state": state})
    assert r.headers["location"] == "/?signin=failed" and count(world, "session") == 0


@pytest.mark.parametrize("next_path", [
    "//evil.test", "/\\evil.test", "https://evil.test", "evil", "/\t/evil.test", "/\r\n/evil.test", "/a\\b",
    "http://evil.test/", "/" + "a" * 600])
def test_open_redirects_become_root(client, discord, next_path):
    r = sign_in(client, discord, next_path=next_path)
    assert r.status_code == 302 and r.headers["location"] == "/"


def test_tampered_next_in_cookie_is_revalidated(client, discord):
    client.get("/api/auth/login")
    state = client.cookies.get(STATE).split(".")[0]
    evil = base64.urlsafe_b64encode(b"//evil.test").decode().rstrip("=")
    client.cookies.clear()
    client.cookies.set(STATE, f"{state}.{evil}", path="/")
    r = client.get("/api/auth/callback", params={"code": "c", "state": state})
    assert r.status_code == 302 and r.headers["location"] == "/"


def test_login_without_discord_config_is_503(client, monkeypatch):
    monkeypatch.delenv("DISCORD_CLIENT_ID")
    r = client.get("/api/auth/login")
    assert r.status_code == 503 and r.json() == {"detail": "sign-in is not configured"}
    assert client.get("/api/builds").status_code == 200  # read-only keeps working


def test_expired_session_is_signed_out(user, world):
    assert user.get("/api/me").status_code == 200
    with world.begin() as conn:
        conn.execute(update(session).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    assert user.get("/api/me").status_code == 401
    assert user.get("/api/subscriptions").status_code == 401


def test_unknown_session_token_is_signed_out(client):
    client.cookies.set(SESSION, "not-a-real-token")
    assert client.get("/api/me").status_code == 401


@pytest.mark.parametrize("secure", [None, "1", "0"])
def test_cookie_flags(client, discord, monkeypatch, secure):
    if secure is not None:
        monkeypatch.setenv("COOKIE_SECURE", secure)
    expect_secure = secure != "0"
    prefix = "__Host-" if expect_secure else ""  # the prefix needs Secure, so plain http dev keeps plain names
    state_name, session_name = prefix + "oauth_state", prefix + "session"
    login = client.get("/api/auth/login")
    state = cookie_attrs(login, state_name)
    assert state["httponly"] and state["samesite"].lower() == "lax"
    assert state["path"] == ("/" if expect_secure else "/api/auth") and "domain" not in state
    assert state["max-age"] == "600" and ("secure" in state) == expect_secure
    # The test client is https, so Secure cookies are sent back and the callback works in every case.
    done = client.get("/api/auth/callback", params={"code": "c", "state": client.cookies.get(state_name).split(".")[0]})
    sess = cookie_attrs(done, session_name)
    assert sess["httponly"] and sess["samesite"].lower() == "lax" and sess["path"] == "/" and "domain" not in sess
    assert sess["max-age"] == str(30 * 86400) and ("secure" in sess) == expect_secure
    assert client.get("/api/me").status_code == 200
    out = client.post("/api/auth/logout", headers=HEADERS)
    assert cookie_attrs(out, session_name)["path"] == "/"


# --- CSRF / content type ----------------------------------------------------------------------------------------

def test_origin_check(user):
    body = {"kind": "asset", "asset_path": RIFLE, "webhook_url": HOOK}
    for headers in ({}, {"Origin": "https://evil.test"}, {"Origin": "null"}, {"Origin": ORIGIN + ".evil.test"}):
        r = user.post("/api/subscriptions", json=body, headers=headers)
        assert r.status_code == 403 and r.json() == {"detail": "bad origin"}
    assert user.delete("/api/subscriptions/1").status_code == 403
    assert user.post("/api/auth/logout").status_code == 403
    assert user.post("/api/subscriptions", json=body, headers=HEADERS).status_code == 201
    assert user.get("/api/subscriptions", headers={"Origin": "https://evil.test"}).status_code == 200  # GET is exempt


def test_origin_unset_fails_closed(user, monkeypatch):
    monkeypatch.delenv("PUBLIC_ORIGIN")
    assert user.post("/api/auth/logout", headers=HEADERS).status_code == 403


def test_non_json_body_is_415(user):
    for content_type in ("text/plain", "application/x-www-form-urlencoded"):
        r = user.post("/api/subscriptions", content="kind=asset", headers={**HEADERS, "Content-Type": content_type})
        assert r.status_code == 415
    ok = user.post("/api/subscriptions", json={"kind": "asset", "asset_path": RIFLE, "webhook_url": HOOK},
                   headers={**HEADERS, "Content-Type": "application/json; charset=utf-8"})
    assert ok.status_code == 201


# --- subscriptions ----------------------------------------------------------------------------------------------

def test_signed_out_is_401(client):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/subscriptions").status_code == 401
    assert client.post("/api/subscriptions", json={}, headers=HEADERS).status_code == 401
    assert client.delete("/api/subscriptions/1", headers=HEADERS).status_code == 401


def test_create_each_kind_and_list(user, world):
    asset_sub = post(user).json()
    assert post(user, "area").status_code == 201
    field_sub = post(user, "field").json()
    assert asset_sub["kind"] == "asset" and asset_sub["target"] is None
    assert asset_sub["asset"] == {"path": RIFLE, "name": "DA_Rifle"}
    assert field_sub["asset"] is None and field_sub["target"] == "BaseDamage"
    assert asset_sub["webhook"] == "https://discord.com/api/webhooks/1234567890/…k3Zq"
    assert asset_sub["disabled_at"] is None and asset_sub["last_delivery"] is None
    listed = user.get("/api/subscriptions")
    assert [s["kind"] for s in listed.json()] == ["field", "area", "asset"]  # newest first
    assert "abcdefghijk3Zq" not in listed.text
    with world.connect() as conn:  # the full URL is stored, only the API masks it
        assert conn.execute(select(subscription.c.webhook_url)).scalars().first() == HOOK


def test_asset_by_former_path(user):
    r = post(user, asset_path=OLD)
    assert r.status_code == 201 and r.json()["asset"]["path"] == NEW
    assert post(user, asset_path=NEW).status_code == 409  # the same asset


def test_invalid_subscriptions_are_422(user):
    assert post(user, asset_path="Game/Nope.json").status_code == 422
    assert post(user, "area", target="no-such-area").status_code == 422
    assert post(user, "field", target="x").status_code == 422
    assert post(user, "field", target="x" * 201).status_code == 422
    assert post(user, "field", target="a\u0000b").status_code == 422
    assert post(user, asset_path="a\u0000b").status_code == 422
    assert post(user, "asset", target="x").status_code == 422
    assert post(user, "area", asset_path=RIFLE).status_code == 422
    assert post(user, kind="bogus").status_code == 422
    assert post(user, extra="x").status_code == 422


@pytest.mark.parametrize("host", ["discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com"])
def test_webhook_hosts_accepted(user, host):
    r = post(user, webhook_url=f"https://{host}/api/webhooks/42/tok-en_X")
    assert r.status_code == 201 and r.json()["webhook"] == "https://discord.com/api/webhooks/42/…en_X"


@pytest.mark.parametrize("url", [
    "http://discord.com/api/webhooks/1/abc",
    "https://evil.test/api/webhooks/1/abc",
    "https://discord.com.evil.test/api/webhooks/1/abc",
    "https://discord.com@evil.test/api/webhooks/1/abc",
    "https://user:pass@discord.com/api/webhooks/1/abc",
    "https://discord.com:8443/api/webhooks/1/abc",
    "https://discord.com/api/webhooks/1/abc/extra",
    "https://discord.com/api/webhooks/1/abc?wait=true",
    "https://discord.com/api/webhooks/1/abc#frag",
    "https://discord.com/api/webhooks/1/abc\n",
    "https://discord.com/api/webhooks/x/abc",
    "https://discord.com/api/webhooks/1",
    "https://discord.com/api/webhooks/١/abc",
    "https://sub.discord.com/api/webhooks/1/abc",
    "https://discord.com/api/webhooks/1/" + "a" * 101,
    "",
])
def test_webhook_url_rejected(user, url):
    assert post(user, webhook_url=url).status_code == 422


def test_limit_of_25(user):
    for i in range(25):
        assert post(user, "field", target=f"field{i:02}").status_code == 201
    r = post(user, "field", target="one-too-many")
    assert r.status_code == 422 and r.json() == {"detail": "subscription limit reached"}
    assert len(user.get("/api/subscriptions").json()) == 25


def test_duplicate_is_409(user):
    assert post(user, "field").status_code == 201
    assert post(user, "field").status_code == 409
    assert post(user, "field", webhook_url="https://discord.com/api/webhooks/9/other").status_code == 201
    assert len(user.get("/api/subscriptions").json()) == 2


def test_other_users_subscription_is_invisible(user, discord):
    mine = post(user).json()["id"]
    other = new_client()
    sign_in(other, discord, {"id": "111", "username": "other", "avatar": None})
    assert other.get("/api/subscriptions").json() == []
    assert other.delete(f"/api/subscriptions/{mine}", headers=HEADERS).status_code == 404
    assert post(other).status_code == 201  # an identical subscription is not a duplicate across users
    assert len(user.get("/api/subscriptions").json()) == 1  # and mine was not deleted


def test_delete(user):
    sub = post(user).json()["id"]
    assert user.delete(f"/api/subscriptions/{sub}", headers=HEADERS).status_code == 204
    assert user.get("/api/subscriptions").json() == []
    assert user.delete(f"/api/subscriptions/{sub}", headers=HEADERS).status_code == 404
    assert user.delete("/api/subscriptions/99999999999", headers=HEADERS).status_code == 404


def test_last_delivery_is_newest_row(user, world):
    sub = post(user).json()["id"]
    with world.begin() as conn:
        b = {r.label: r.id for r in conn.execute(select(build.c.id, build.c.label))}
        conn.execute(insert(delivery), [
            {"subscription_id": sub, "build_id": b["b2"], "status": "failed", "attempts": 3,
             "updated_at": datetime(2026, 10, 16, tzinfo=timezone.utc)},
            {"subscription_id": sub, "build_id": b["b3"], "status": "sent", "attempts": 1,
             "updated_at": datetime(2026, 11, 2, 10, tzinfo=timezone.utc)},
        ])
    item = user.get("/api/subscriptions").json()[0]
    assert item["last_delivery"] == {"build": "b3", "status": "sent", "updated_at": "2026-11-02T10:00:00+00:00"}


def test_disabled_at_is_reported(user, world):
    sub = post(user).json()["id"]
    with world.begin() as conn:
        conn.execute(update(subscription).where(subscription.c.id == sub)
                     .values(disabled_at=datetime(2026, 11, 3, tzinfo=timezone.utc)))
    assert user.get("/api/subscriptions").json()[0]["disabled_at"] == "2026-11-03T00:00:00+00:00"


# --- hardening --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("username", ["ki\x00yo", "ki\nyo", "ki\x1fyo", "\x7fkiyo"])
def test_control_characters_in_username_fail_sign_in(client, discord, world, username):
    r = sign_in(client, discord, {**ME, "username": username})
    assert r.status_code == 302 and r.headers["location"] == "/?signin=failed"
    assert count(world, "app_user") == 0 and count(world, "session") == 0


def test_login_deletes_every_users_expired_sessions(client, discord, world):
    with world.begin() as conn:
        conn.execute(text("INSERT INTO app_user (discord_id, username) VALUES ('2', 'other')"))
        past = datetime.now(timezone.utc) - timedelta(days=1)
        future = datetime.now(timezone.utc) + timedelta(days=1)
        conn.execute(text("INSERT INTO session (token_hash, user_id, expires_at) VALUES "
                          "('old-other', 1, :p), ('live-other', 1, :f)"), {"p": past, "f": future})
    sign_in(client, discord)  # a different user (discord id 8035...) signs in
    with world.connect() as conn:
        hashes = set(conn.execute(select(session.c.token_hash)).scalars())
    assert "old-other" not in hashes and "live-other" in hashes and len(hashes) == 2


@pytest.mark.parametrize("host", ["discordapp.com", "ptb.discord.com", "canary.discord.com"])
def test_webhook_aliases_are_stored_canonically_and_duplicate(user, world, host):
    assert post(user).status_code == 201
    r = post(user, webhook_url=f"https://{host}" + HOOK.removeprefix("https://discord.com"))
    assert r.status_code == 409
    with world.connect() as conn:
        assert conn.execute(select(subscription.c.webhook_url)).scalars().all() == [HOOK]


def test_alias_webhook_is_stored_as_discord_com(user, world):
    assert post(user, webhook_url="https://ptb.discord.com/api/webhooks/42/tok-en_X").status_code == 201
    with world.connect() as conn:
        assert conn.execute(select(subscription.c.webhook_url)).scalar_one() == "https://discord.com/api/webhooks/42/tok-en_X"


def test_callback_exchange_cap_redirects_at_once(client, discord, world, monkeypatch):
    monkeypatch.setattr(accounts, "MAX_EXCHANGES", 1)
    client.get("/api/auth/login")
    state = client.cookies.get(STATE).split(".")[0]
    monkeypatch.setattr(accounts, "_exchanges", 1)  # one exchange is already waiting on Discord
    r = client.get("/api/auth/callback", params={"code": "c", "state": state})
    assert r.status_code == 302 and r.headers["location"] == "/?signin=failed"
    assert discord["requests"] == [] and count(world, "session") == 0
    monkeypatch.setattr(accounts, "_exchanges", 0)  # the slot frees; a fresh attempt works
    client.get("/api/auth/login")
    state = client.cookies.get(STATE).split(".")[0]
    r = client.get("/api/auth/callback", params={"code": "c", "state": state})
    assert r.headers["location"] == "/" and count(world, "session") == 1
    assert accounts._exchanges == 0


def test_webhook_ids_with_leading_zeros_are_the_same_webhook():
    from tracker.webhooks import canonical
    plain = canonical("https://discord.com/api/webhooks/123/tok")
    assert plain == "https://discord.com/api/webhooks/123/tok"
    assert canonical("https://discord.com/api/webhooks/0123/tok") == plain
    assert canonical("https://ptb.discord.com/api/webhooks/000123/tok") == plain
    assert canonical("https://discord.com/api/webhooks/000/tok") == "https://discord.com/api/webhooks/0/tok"


def test_leading_zero_duplicate_is_409(user):
    assert post(user).status_code == 201
    assert post(user, webhook_url="https://discord.com/api/webhooks/01234567890/abcdefghijk3Zq").status_code == 409
