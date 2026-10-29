"""Production ASGI app: the JSON API plus the built web app, one process.

`/api/*` goes to `tracker.api.app`. FastAPI's own /docs, /redoc and /openapi.json are not routed
here (dev, `uvicorn tracker.api:app`, still has them); in production they fall to the web app.
Everything else is served from STATIC_DIR, with unknown GET paths falling back to index.html
so client-side routes work on reload.
"""
import ipaddress
import logging
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import make_url
from sqlalchemy.exc import ArgumentError
from starlette.responses import FileResponse, PlainTextResponse

from tracker.api import app as api_app

API_PREFIXES = ("/api",)

CSP = "; ".join([
    "default-src 'self'",
    # 'unsafe-inline' for React style attributes (hero sizing, odometer); scripts stay 'self' only.
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data: https://cdn.discordapp.com",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "object-src 'none'",
    "upgrade-insecure-requests",
])
# On every response (API JSON, errors and static files too). HSTS is sent unconditionally: browsers
# ignore it over plain http, and behind Tailscale Funnel there may be no Caddy in front to add it.
COMMON_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
}
HTML_HEADERS = {
    "Content-Security-Policy": CSP,
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cache-Control": "no-cache",
}


class _StripQuery(logging.Filter):
    """uvicorn.access logs the full request target; OAuth callbacks carry `code` and `state` in it."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            record.args = (*args[:2], args[2].split("?", 1)[0], *args[3:])
        return True


logging.getLogger("uvicorn.access").addFilter(_StripQuery())


PLACEHOLDER_PASSWORDS = {"", "change-me", "changeme", "password"}
MIN_PASSWORD_LENGTH = 16
DB_SCHEMES = ("postgresql", "postgresql+psycopg")
LOCAL_HOSTS = {"localhost", "127.0.0.1"}
_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")


def _host_ok(host: str) -> bool:
    """A DNS hostname (letters, digits, hyphens; dotted unless localhost) or a literal IP address."""
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    if host == "localhost":
        return True
    labels = host.split(".")
    return len(host) <= 253 and len(labels) >= 2 and all(_LABEL.fullmatch(label) for label in labels)


def _origin_problem(raw: str) -> str | None:
    """Why `raw` cannot be the public origin, or None. Strict: scheme://host[:port] and nothing else."""
    if any(c.isspace() or c in "\\?#" for c in raw):
        return "must not contain whitespace, backslash, query or fragment"
    try:
        parts = urlsplit(raw)
        parts.port  # noqa: B018 - raises ValueError for a bad port
    except ValueError:
        return "is not a valid URL"
    if parts.scheme not in ("http", "https"):
        return "must be https"
    if "@" in parts.netloc:
        return "must not contain user info"
    if not parts.hostname:
        return "has no hostname"
    if parts.path not in ("", "/"):
        return "must not have a path"
    if parts.netloc.endswith(":"):
        return "has an empty port"
    if parts.port == 0:
        return "has port 0"
    if not _host_ok(parts.hostname):
        return "has an invalid hostname"
    return None


def check_environment(env=os.environ) -> None:
    """Refuse to serve with placeholder secrets or an origin that cannot be right in production."""
    passwords = {}
    for name in ("DATABASE_URL", "MIGRATE_DATABASE_URL"):
        url = env.get(name)
        if not url:
            continue
        try:
            parsed = make_url(url)
        except ArgumentError:
            raise RuntimeError(f"{name} is not a valid database URL") from None
        password = parsed.password
        if "change-me" in url.lower() or (password or "").strip().lower() in PLACEHOLDER_PASSWORDS:
            raise RuntimeError(f"{name} has an empty or placeholder password (change-me...); set a real one in .env")
        if name == "DATABASE_URL" and parsed.drivername not in DB_SCHEMES:
            raise RuntimeError(f"{name} must use the postgresql or postgresql+psycopg scheme (got {parsed.drivername!r})")
        if len(password) < MIN_PASSWORD_LENGTH:
            raise RuntimeError(f"{name} password is shorter than {MIN_PASSWORD_LENGTH} characters; "
                               "generate one with: openssl rand -hex 24")
        passwords[name] = password
    if len(passwords) == 2 and passwords["DATABASE_URL"] == passwords["MIGRATE_DATABASE_URL"]:
        raise RuntimeError("DATABASE_URL and MIGRATE_DATABASE_URL must use different passwords")
    origin = env.get("PUBLIC_ORIGIN", "")
    if not origin:
        raise RuntimeError("PUBLIC_ORIGIN is not set (expected https://<your-domain>)")
    problem = _origin_problem(origin)
    if problem:
        raise RuntimeError(f"PUBLIC_ORIGIN {problem} (got {origin!r}); expected https://<your-domain>[:port]")
    parts = urlsplit(origin)
    domain = env.get("DOMAIN")
    if domain and (parts.hostname or "") != domain.lower():
        raise RuntimeError(f"PUBLIC_ORIGIN host {parts.hostname!r} does not match DOMAIN {domain!r}")
    local_http = parts.scheme == "http" and parts.hostname in LOCAL_HOSTS
    if parts.scheme == "http" and not local_http:
        raise RuntimeError(f"PUBLIC_ORIGIN must be https (got {origin!r})")
    insecure = env.get("COOKIE_SECURE") == "0"
    if local_http and not insecure:
        raise RuntimeError(f"PUBLIC_ORIGIN is {origin}; plain http is only allowed with COOKIE_SECURE=0")
    if insecure and not local_http:
        raise RuntimeError("COOKIE_SECURE=0 is only allowed with an http://localhost PUBLIC_ORIGIN")


def _is_api(path: str) -> bool:
    # Exact, case-sensitive prefix: the same rule as Caddy's `path_regexp ^/api(/|$)`, so /API/x is not API.
    return any(path == p or path.startswith(p + "/") for p in API_PREFIXES)


class Server:
    def __init__(self, api, static_dir: str | Path):
        self.api = api
        self.root = Path(static_dir).resolve()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self._lifespan(scope, receive, send)
        if scope["type"] != "http":
            return await self.api(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                present = {k.lower() for k, _ in message["headers"]}
                message["headers"] = [*message["headers"], *(
                    (k.lower().encode(), v.encode()) for k, v in COMMON_HEADERS.items() if k.lower().encode() not in present)]
            await send(message)

        await self._http(scope, receive, send_with_headers)

    async def _lifespan(self, scope, receive, send):
        first = await receive()
        if first["type"] == "lifespan.startup":
            try:
                check_environment()
            except RuntimeError as exc:
                await send({"type": "lifespan.startup.failed", "message": str(exc)})
                raise  # like Starlette's own lifespan; ASGI test clients wait for the app to end
        replay = [first]

        async def replaying_receive():
            return replay.pop(0) if replay else await receive()

        await self.api(scope, replaying_receive, send)

    async def _http(self, scope, receive, send):
        if _is_api(scope["path"]):
            return await self.api(scope, receive, send)
        if scope["method"] not in ("GET", "HEAD"):
            response = PlainTextResponse("Method Not Allowed", 405, {"Allow": "GET, HEAD"})
        else:
            response = self._static(scope["path"])
        await response(scope, receive, send)

    def _static(self, path: str):
        index = self.root / "index.html"
        if not index.is_file():
            return PlainTextResponse("web app not built", 503)
        rel = path.lstrip("/")
        if "\x00" in rel:
            return PlainTextResponse("Bad Request", 400)
        target = (self.root / rel).resolve()
        # Traversal guard: after decoding and resolving, the file must still live under the root.
        if rel and target.is_relative_to(self.root) and target.is_file() and target != index:
            long_lived = target.relative_to(self.root).parts[0] == "assets"
            cache = "public, max-age=31536000, immutable" if long_lived else "public, max-age=3600"
            return FileResponse(target, headers={"Cache-Control": cache})
        return FileResponse(index, headers=HTML_HEADERS)


app = Server(api_app, os.environ.get("STATIC_DIR", "/app/web/dist"))
