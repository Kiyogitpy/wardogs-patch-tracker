"""The one definition of a valid Discord webhook URL, shared by sign-up (accounts.py) and delivery (notify.py)."""
import re

WEBHOOK_RE = re.compile(
    r"https://(?:discord\.com|discordapp\.com|ptb\.discord\.com|canary\.discord\.com)/api/webhooks/"
    r"(\d{1,20})/([A-Za-z0-9_-]{1,100})", re.ASCII)


def canonical(url: str) -> str | None:
    """The webhook on discord.com (aliases map to it, so duplicates are caught), or None if it is not valid."""
    m = WEBHOOK_RE.fullmatch(url)
    return f"https://discord.com/api/webhooks/{m[1]}/{m[2]}" if m else None
