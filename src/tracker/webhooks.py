"""Definitions shared by api.py and accounts.py (request input rules) and by accounts.py and notify.py (the
Discord webhook URL)."""
import json
import re
from typing import Annotated

from fastapi import Request
from fastapi.routing import APIRoute
from pydantic import AfterValidator

WEBHOOK_RE = re.compile(
    r"https://(?:discord\.com|discordapp\.com|ptb\.discord\.com|canary\.discord\.com)/api/webhooks/"
    r"(\d{1,20})/([A-Za-z0-9_-]{1,100})", re.ASCII)


def canonical(url: str) -> str | None:
    """The webhook on discord.com (aliases map to it, so duplicates are caught), or None if it is not valid."""
    m = WEBHOOK_RE.fullmatch(url)
    # int() drops leading zeros: /webhooks/0123/t and /webhooks/123/t are the same webhook.
    return f"https://discord.com/api/webhooks/{int(m[1])}/{m[2]}" if m else None


def utf8_ok(value):
    """A str that does not encode as UTF-8 (a lone surrogate, which JSON can carry) would raise in the database driver."""
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("must be valid Unicode text") from None
    return value


# Free text from a client: body fields, query and path parameters.
Utf8 = AfterValidator(utf8_ok)
Text = Annotated[str, Utf8]


class StrictJsonRequest(Request):
    """A request whose JSON body rejects NaN, Infinity and -Infinity (Python accepts them, JSON does not)."""

    async def json(self):
        if not hasattr(self, "_json"):
            def no_constants(token):
                raise json.JSONDecodeError(f"{token} is not valid JSON", "", 0)
            self._json = json.loads(await self.body(), parse_constant=no_constants)
        return self._json


class StrictJsonRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def strict(request: Request):
            return await handler(StrictJsonRequest(request.scope, request.receive, request._send))
        return strict
