"""Discord sign-in, sessions and watch subscriptions. See docs/API.md, "Accounts and subscriptions"."""
import base64
import binascii
import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, exists, func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from tracker.db import app_user, asset, build, build_area, delivery, session, subscription


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
MAX_SUBSCRIPTIONS = 25
MAX_NEXT = 500
WEBHOOK_RE = re.compile(
    r"https://(?:discord\.com|discordapp\.com|ptb\.discord\.com|canary\.discord\.com)/api/webhooks/"
    r"(\d{1,20})/([A-Za-z0-9_-]{1,100})", re.ASCII)
AVATAR_RE = re.compile(r"[A-Za-z0-9_]{1,64}", re.ASCII)
FAILED = "/?signin=failed"


def http_client() -> httpx.Client:
    """Factory for the Discord HTTP client; tests replace it with one on httpx.MockTransport."""
    return httpx.Client(timeout=10.0)


def _public_origin() -> str:
    return os.environ.get("PUBLIC_ORIGIN", "").rstrip("/")


def _cookie_secure() -> bool:
    return os.environ.get("COOKIE_SECURE", "1") != "0"


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
    _clear_cookie(response, "oauth_state", "/api/auth")
    return response


@router.get("/auth/login")
def login(next: str | None = None):
    client_id = os.environ.get("DISCORD_CLIENT_ID")
    if not client_id or not os.environ.get("DISCORD_CLIENT_SECRET") or not _public_origin():
        raise HTTPException(503, "sign-in is not configured")
    state = secrets.token_urlsafe(32)
    packed = base64.urlsafe_b64encode(_safe_next(next).encode()).decode().rstrip("=")
    query = urlencode({"client_id": client_id, "redirect_uri": _public_origin() + "/api/auth/callback",
                       "response_type": "code", "scope": "identify", "state": state, "prompt": "none"})
    response = RedirectResponse(f"{DISCORD_AUTHORIZE}?{query}", status_code=302)
    _set_cookie(response, "oauth_state", f"{state}.{packed}", STATE_TTL, "/api/auth")
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


def _discord_user(code: str) -> dict | None:
    """Exchange the code and read the profile; None on any Discord failure. Neither secret is logged."""
    try:
        with http_client() as client:
            token = client.post(DISCORD_TOKEN, data={
                "client_id": os.environ["DISCORD_CLIENT_ID"], "client_secret": os.environ["DISCORD_CLIENT_SECRET"],
                "grant_type": "authorization_code", "code": code,
                "redirect_uri": _public_origin() + "/api/auth/callback"})
            token.raise_for_status()
            access = token.json()["access_token"]
            me = client.get(DISCORD_ME, headers={"Authorization": f"Bearer {access}"})
            me.raise_for_status()
            profile = me.json()
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None
    if (not isinstance(profile, dict) or not isinstance(profile.get("id"), str)
            or not re.fullmatch(r"\d{1,20}", profile["id"], re.ASCII)
            or not isinstance(profile.get("username"), str) or not profile["username"]):
        return None
    avatar = profile.get("avatar")
    return {"discord_id": profile["id"], "username": profile["username"][:200],
            "avatar": avatar if isinstance(avatar, str) and AVATAR_RE.fullmatch(avatar) else None}


@router.get("/auth/callback")
def callback(code: str | None = None, state: str | None = None, error: str | None = None,
             oauth_state: Annotated[str | None, Cookie()] = None):
    next_path = _parse_state_cookie(oauth_state, state)
    if error or next_path is None or not code or len(code) > 512 or not _public_origin():
        return _failed()
    user = _discord_user(code)
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
        conn.execute(delete(session).where(session.c.user_id == user_id, session.c.expires_at <= func.now()))
        conn.execute(insert(session).values(token_hash=_hash(token), user_id=user_id,
                                            expires_at=datetime.now(timezone.utc) + SESSION_TTL))
    response = RedirectResponse(next_path, status_code=302)
    _clear_cookie(response, "oauth_state", "/api/auth")
    _set_cookie(response, "session", token, int(SESSION_TTL.total_seconds()), "/")
    return response


@router.post("/auth/logout", status_code=204)
def logout(session_token: Annotated[str | None, Cookie(alias="session")] = None):
    if session_token:
        with _api().get_engine().begin() as conn:
            conn.execute(delete(session).where(session.c.token_hash == _hash(session_token)))
    response = Response(status_code=204)
    _clear_cookie(response, "session", "/")
    return response


def current_user(session_token: Annotated[str | None, Cookie(alias="session")] = None):
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
    if len(body.webhook_url) > 300 or not WEBHOOK_RE.fullmatch(body.webhook_url):
        raise HTTPException(422, "webhook_url must be a Discord webhook URL")
    if body.kind == "asset":
        if (body.target is not None or not body.asset_path or len(body.asset_path) > 1000
                or "\x00" in body.asset_path):
            raise HTTPException(422, "asset subscriptions need asset_path (and no target)")
    elif (body.asset_path is not None or body.target is None or "\x00" in body.target
            or not (2 if body.kind == "field" else 1) <= len(body.target) <= 200):
        raise HTTPException(422, f"{body.kind} subscriptions need a valid target (and no asset_path)")

    with _api().get_engine().begin() as conn:
        values = {"user_id": user.id, "kind": body.kind, "webhook_url": body.webhook_url}
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
