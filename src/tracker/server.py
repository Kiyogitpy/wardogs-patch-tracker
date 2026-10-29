"""Production ASGI app: the JSON API plus the built web app, one process.

`/api/*` (and FastAPI's own /docs, /redoc, /openapi.json) go to `tracker.api.app` untouched.
Everything else is served from STATIC_DIR, with unknown GET paths falling back to index.html
so client-side routes work on reload.
"""
import os
from pathlib import Path

from starlette.responses import FileResponse, PlainTextResponse

from tracker.api import app as api_app

API_PREFIXES = ("/api", "/docs", "/redoc", "/openapi.json")

CSP = "; ".join([
    "default-src 'self'",
    # 'unsafe-inline' for React style attributes (hero sizing, odometer); scripts stay 'self' only.
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src https://fonts.gstatic.com",
    "img-src 'self' data: https://cdn.discordapp.com",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
])
HTML_HEADERS = {
    "Content-Security-Policy": CSP,
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Content-Type-Options": "nosniff",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cache-Control": "no-cache",
}


def _is_api(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in API_PREFIXES)


class Server:
    def __init__(self, api, static_dir: str | Path):
        self.api = api
        self.root = Path(static_dir).resolve()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or _is_api(scope["path"]):
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
            return FileResponse(target, headers={"Cache-Control": cache, "X-Content-Type-Options": "nosniff"})
        return FileResponse(index, headers=HTML_HEADERS)


app = Server(api_app, os.environ.get("STATIC_DIR", "/app/web/dist"))
