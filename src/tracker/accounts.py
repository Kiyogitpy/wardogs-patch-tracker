"""Discord sign-in, sessions and watch subscriptions. See docs/API.md, "Accounts and subscriptions"."""
import base64
import binascii
import hashlib
import hmac
import os
import re
import secrets
import threading
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, exists, func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from tracker.db import app_user, asset, build, build_area, delivery, oauth_state, session, subscription
from tracker.webhooks import canonical


def _api():
    # api.py includes this router at its end, so it is imported lazily here to avoid an import cycle.
    from tracker import api
    return api


router = APIRouter(prefix="/api")

DISCORD_AUTHORIZE = "https://discord.com/oauth2/authorize"
DISCORD_TOKEN = "https://discord.com/api/oauth2/token"
DISCORD_ME = "https://discord.com/api/users/@me"
SESSION_TTL = timedelta(days=30)
STATE_TTL = 600
MAX_SESSIONS = 10  # per user; a login beyond it ends the user's oldest
EXCHANGE_DEADLINE = 8.0  # seconds for the whole Discord exchange (token call and profile call together)
MAX_SUBSCRIPTIONS = 25
MAX_NEXT = 500
MAX_EXCHANGES = 4  # concurrent Discord code exchanges; tests patch it
AVATAR_RE = re.compile(r"[A-Za-z0-9_]{1,64}", re.ASCII)
FAILED = "/?signin=failed"


_monotonic = time.monotonic  # tests replace it


def http_client() -> httpx.Client:
    """Factory for the Discord HTTP client; tests replace it with one on httpx.MockTransport.
    Every call passes its own timeout, cut from the exchange deadline."""
    return httpx.Client()


def _public_origin() -> str:
    return os.environ.get("PUBLIC_ORIGIN", "").rstrip("/")


def _cookie_secure() -> bool:
    return os.environ.get("COOKIE_SECURE", "1") != "0"


def cookie_name(base: str) -> str:
    """The `__Host-` prefix (Secure, path "/", no Domain) when cookies are Secure; plain names for http dev."""
    return ("__Host-" + base) if _cookie_secure() else base


def _state_path() -> str:
    return "/" if _cookie_secure() else "/api/auth"  # the prefix demands path "/"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _safe_next(value: str | None) -> str:
    """A same-site path or "/". Rejects `//x`, `/\\x`, any backslash and control characters (browsers drop
    tabs and newlines, so "/<TAB>/evil" would become `//evil`)."""
    if (not value or len(value) > MAX_NEXT or not value.startswith("/") or value.startswith(("//", "/\\"))
            or "\\" in value or any(ord(c) < 0x20 or ord(c) == 0x7F for c in value)):
        return "/"
    return value


def _set_cookie(response: Response, key: str, value: str, max_age: int, path: str) -> None:
    response.set_cookie(key, value, max_age=max_age, path=path, httponly=True, samesite="lax",
                        secure=_cookie_secure())


def _clear_cookie(response: Response, key: str, path: str) -> None:
    response.delete_cookie(key, path=path, httponly=True, samesite="lax", secure=_cookie_secure())


def _failed() -> RedirectResponse:
    response = RedirectResponse(FAILED, status_code=302)
    _clear_cookie(response, cookie_name("oauth_state"), _state_path())
    return response


@router.get("/auth/login")
def login(next: str | None = None):
    client_id = os.environ.get("DISCORD_CLIENT_ID")
    if not client_id or not os.environ.get("DISCORD_CLIENT_SECRET") or not _public_origin():
        raise HTTPException(503, "sign-in is not configured")
    state = secrets.token_urlsafe(32)
    next_path = _safe_next(next)
    packed = base64.urlsafe_b64encode(next_path.encode()).decode().rstrip("=")
    with _api().get_engine().begin() as conn:  # the state must exist here to be accepted by the callback
        conn.execute(delete(oauth_state).where(oauth_state.c.expires_at <= func.now()))
        conn.execute(insert(oauth_state).values(
            state_hash=_hash(state), next=next_path,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=STATE_TTL)))
    query = urlencode({"client_id": client_id, "redirect_uri": _public_origin() + "/api/auth/callback",
                       "response_type": "code", "scope": "identify", "state": state, "prompt": "none"})
    response = RedirectResponse(f"{DISCORD_AUTHORIZE}?{query}", status_code=302)
    _set_cookie(response, cookie_name("oauth_state"), f"{state}.{packed}", STATE_TTL, _state_path())
    return response


def _parse_state_cookie(cookie: str | None, state: str | None) -> str | None:
    """The `next` path when the query state matches the cookie (constant-time), else None."""
    if not cookie or not state or "." not in cookie:
        return None
    expected, _, packed = cookie.partition(".")
    if not hmac.compare_digest(expected.encode(), state.encode()):
        return None
    try:
        return _safe_next(base64.urlsafe_b64decode(packed + "=" * (-len(packed) % 4)).decode())
    except (binascii.Error, UnicodeDecodeError):
        return "/"


class _DeadlinePassed(Exception):
    pass


def _discord_user(code: str) -> dict | None:
    """Exchange the code and read the profile; None on any Discord failure. Neither secret is logged.
    Both calls share one wall-clock deadline: each gets what is left of EXCHANGE_DEADLINE as its timeout."""
    deadline = _monotonic() + EXCHANGE_DEADLINE

    def left() -> float:
        remaining = deadline - _monotonic()
        if remaining <= 0:
            raise _DeadlinePassed
        return remaining

    try:
        with http_client() as client:
            token = client.post(DISCORD_TOKEN, timeout=left(), data={
                "client_id": os.environ["DISCORD_CLIENT_ID"], "client_secret": os.environ["DISCORD_CLIENT_SECRET"],
                "grant_type": "authorization_code", "code": code,
                "redirect_uri": _public_origin() + "/api/auth/callback"})
            token.raise_for_status()
            access = token.json()["access_token"]
            me = client.get(DISCORD_ME, headers={"Authorization": f"Bearer {access}"}, timeout=left())
            me.raise_for_status()
            profile = me.json()
            left()  # a profile that arrived after the deadline is not used
    except (httpx.HTTPError, ValueError, KeyError, TypeError, _DeadlinePassed):
        return None
    if (not isinstance(profile, dict) or not isinstance(profile.get("id"), str)
            or not re.fullmatch(r"\d{1,20}", profile["id"], re.ASCII)
            or not isinstance(profile.get("username"), str) or not profile["username"]
            or any(unicodedata.category(c) == "Cc" for c in profile["username"])):  # NUL would be a 500 in Postgres
        return None
    avatar = profile.get("avatar")
    return {"discord_id": profile["id"], "username": profile["username"][:200],
            "avatar": avatar if isinstance(avatar, str) and AVATAR_RE.fullmatch(avatar) else None}


_exchanges = 0
_exchanges_lock = threading.Lock()


def _exchange(code: str) -> dict | None:
    """_discord_user, but at most MAX_EXCHANGES at once; over the cap it fails at once instead of queueing."""
    global _exchanges
    with _exchanges_lock:
        if _exchanges >= MAX_EXCHANGES:
            return None
        _exchanges += 1
    try:
        return _discord_user(code)
    finally:
        with _exchanges_lock:
            _exchanges -= 1


@router.get("/auth/callback")
def callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    next_path = _parse_state_cookie(request.cookies.get(cookie_name("oauth_state")), state)
    if error or next_path is None or not code or len(code) > 512 or not _public_origin():
        return _failed()
    with _api().get_engine().begin() as conn:  # single use: taking the row is what lets this callback go on
        row = conn.execute(delete(oauth_state).where(oauth_state.c.state_hash == _hash(state))
                           .returning(oauth_state.c.next, oauth_state.c.expires_at > func.now())).first()
    if row is None or not row[1]:
        return _failed()
    next_path = _safe_next(row[0])
    user = _exchange(code)
    if user is None:
        return _failed()
    token = secrets.token_urlsafe(32)
    with _api().get_engine().begin() as conn:
        user_id = conn.execute(
            pg_insert(app_user).values(**user)
            .on_conflict_do_update(index_elements=[app_user.c.discord_id],
                                   set_={"username": user["username"], "avatar": user["avatar"],
                                         "last_login_at": func.now()})
            .returning(app_user.c.id)).scalar_one()
        conn.execute(delete(session).where(session.c.expires_at <= func.now()))  # everyone's, so the table stays small
        conn.execute(insert(session).values(token_hash=_hash(token), user_id=user_id,
                                            expires_at=datetime.now(timezone.utc) + SESSION_TTL))
        newest = (select(session.c.token_hash).where(session.c.user_id == user_id, session.c.token_hash != _hash(token))
                  .order_by(session.c.created_at.desc(), session.c.token_hash).limit(MAX_SESSIONS - 1))
        conn.execute(delete(session).where(session.c.user_id == user_id, session.c.token_hash != _hash(token),
                                           session.c.token_hash.not_in(newest)))  # the user's oldest beyond the cap
    response = RedirectResponse(next_path, status_code=302)
    _clear_cookie(response, cookie_name("oauth_state"), _state_path())
    _set_cookie(response, cookie_name("session"), token, int(SESSION_TTL.total_seconds()), "/")
    return response


@router.post("/auth/logout", status_code=204)
def logout(request: Request):
    session_token = request.cookies.get(cookie_name("session"))
    if session_token:
        with _api().get_engine().begin() as conn:
            conn.execute(delete(session).where(session.c.token_hash == _hash(session_token)))
    response = Response(status_code=204)
    _clear_cookie(response, cookie_name("session"), "/")
    return response


def current_user(request: Request):
    session_token = request.cookies.get(cookie_name("session"))
    if session_token:
        with _api().get_engine().connect() as conn:
            row = conn.execute(
                select(app_user).select_from(session.join(app_user, app_user.c.id == session.c.user_id))
                .where(session.c.token_hash == _hash(session_token), session.c.expires_at > func.now())).first()
        if row is not None:
            return row
    raise HTTPException(401, "not signed in")


User = Annotated[object, Depends(current_user)]
NO_STORE = {"Cache-Control": "no-store"}


@router.post("/auth/logout-all", status_code=204)
def logout_all(request: Request, user: User):
    with _api().get_engine().begin() as conn:
        conn.execute(delete(session).where(session.c.user_id == user.id))
    response = Response(status_code=204)
    _clear_cookie(response, cookie_name("session"), "/")
    return response


@router.get("/me")
def me(user: User, response: Response):
    response.headers.update(NO_STORE)
    return {"discord_id": user.discord_id, "username": user.username,
            "avatar_url": (f"https://cdn.discordapp.com/avatars/{user.discord_id}/{user.avatar}.png"
                           if user.avatar else None)}


def _mask(url: str) -> str:
    m = re.fullmatch(r"(https://[^/]+/api/webhooks/\d+)/(.+)", url)
    return f"{m[1]}/…{m[2][-4:]}" if m else "…"


def _list(conn, user_id: int, only_id: int | None = None) -> list[dict]:
    where = [subscription.c.user_id == user_id]
    if only_id is not None:
        where.append(subscription.c.id == only_id)
    rows = conn.execute(
        select(subscription, asset.c.path, asset.c.name)
        .select_from(subscription.outerjoin(asset, asset.c.id == subscription.c.asset_id))
        .where(*where).order_by(subscription.c.created_at.desc(), subscription.c.id.desc())).all()
    last = {}
    if rows:
        for d in conn.execute(
            select(delivery.c.subscription_id, build.c.label, delivery.c.status, delivery.c.updated_at)
            .select_from(delivery.join(build, build.c.id == delivery.c.build_id))
            .where(delivery.c.subscription_id.in_([r.id for r in rows]))
            .order_by(delivery.c.updated_at.desc(), delivery.c.build_id.desc())
        ):
            last.setdefault(d.subscription_id, d)
    return [
        {"id": r.id, "kind": r.kind, "target": r.target,
         "asset": {"path": r.path, "name": r.name} if r.kind == "asset" else None,
         "webhook": _mask(r.webhook_url), "created_at": r.created_at.isoformat(),
         "disabled_at": r.disabled_at.isoformat() if r.disabled_at else None,
         "last_delivery": ({"build": last[r.id].label, "status": last[r.id].status,
                            "updated_at": last[r.id].updated_at.isoformat()} if r.id in last else None)}
        for r in rows
    ]


@router.get("/subscriptions")
def list_subscriptions(user: User, response: Response):
    response.headers.update(NO_STORE)
    with _api().get_engine().connect() as conn:
        return _list(conn, user.id)


class SubscriptionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["asset", "area", "field"]
    asset_path: str | None = None
    target: str | None = None
    webhook_url: str


@router.post("/subscriptions", status_code=201)
def create_subscription(body: SubscriptionIn, user: User, response: Response):
    response.headers.update(NO_STORE)
    webhook = canonical(body.webhook_url) if len(body.webhook_url) <= 300 else None
    if webhook is None:
        raise HTTPException(422, "webhook_url must be a Discord webhook URL")
    if body.kind == "asset":
        if (body.target is not None or not body.asset_path or len(body.asset_path) > 1000
                or "\x00" in body.asset_path):
            raise HTTPException(422, "asset subscriptions need asset_path (and no target)")
    elif (body.asset_path is not None or body.target is None or "\x00" in body.target
            or not (2 if body.kind == "field" else 1) <= len(body.target) <= 200):
        raise HTTPException(422, f"{body.kind} subscriptions need a valid target (and no asset_path)")

    with _api().get_engine().begin() as conn:
        values = {"user_id": user.id, "kind": body.kind, "webhook_url": webhook}
        if body.kind == "asset":
            found = _api().resolve_asset(conn, body.asset_path)
            if found is None:
                raise HTTPException(422, "asset not found")
            values["asset_id"] = found.id
        else:
            if body.kind == "area" and not conn.execute(
                select(exists().where(build_area.c.area == body.target, build_area.c.build_id == build.c.id,
                                      build.c.ingested_at.is_not(None)))).scalar_one():
                raise HTTPException(422, "unknown area")
            values["target"] = body.target
        # Serialise one user's creates so the limit cannot be raced past.
        conn.execute(select(app_user.c.id).where(app_user.c.id == user.id).with_for_update())
        count = conn.execute(select(func.count()).select_from(subscription)
                             .where(subscription.c.user_id == user.id)).scalar_one()
        if count >= MAX_SUBSCRIPTIONS:
            raise HTTPException(422, "subscription limit reached")
        try:
            with conn.begin_nested():
                new_id = conn.execute(insert(subscription).values(**values).returning(subscription.c.id)).scalar_one()
        except IntegrityError:
            raise HTTPException(409, "already subscribed") from None
        item = _list(conn, user.id, new_id)[0]
    response.status_code = 201
    return item


@router.delete("/subscriptions/{subscription_id}", status_code=204)
def delete_subscription(subscription_id: int, user: User):
    # Beyond the Postgres integer range no row can exist; answer 404 rather than raise a driver error.
    if not 0 < subscription_id < 2**31:
        raise HTTPException(404, "subscription not found")
    with _api().get_engine().begin() as conn:
        deleted = conn.execute(delete(subscription).where(subscription.c.id == subscription_id,
                                                          subscription.c.user_id == user.id))
    if deleted.rowcount == 0:
        raise HTTPException(404, "subscription not found")
    return Response(status_code=204)
