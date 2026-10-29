"""Deliver Discord webhook messages for the subscriptions a build matches. See docs/NOTIFY.md."""
import json
import logging
import math
import re
import time
import unicodedata
from decimal import ROUND_HALF_UP, Decimal, localcontext
from urllib.parse import quote

import httpx
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.dialects.postgresql import insert

from tracker.api import NEWEST_FIRST, STATUS_ORDER, _contains
from tracker.db import asset, asset_status, build, build_area, change, delivery, subscription
from tracker.webhooks import WEBHOOK_RE

log = logging.getLogger("tracker.notify")

MINUS = "−"
MAX_TITLE = 256
MAX_DESCRIPTION = 4096
MAX_LINES = 10
MAX_VALUE = 80
MAX_PATH = 200
MAX_NAME = 100
MAX_FOOTER = 2048
MAX_URL = 2048
MAX_EMBED = 6000  # Discord counts title + description + footer text
HUGE = 1e300  # numbers at or beyond this are shown as plain values, not subtracted
MAX_ERROR = 300
ATTEMPTS = 3
BACKOFF = (1, 4)
RETRY_AFTER_CAP = 30
TIMEOUT = 10
BUDGET = 60  # seconds one subscription may take over all its attempts, waits included
MAX_RATE_SLEEP = 40  # of which at most this many may be spent waiting out 429s
STATEMENT_TIMEOUT_MS = 60_000  # per statement on the notify engine
GONE = (401, 403, 404)


class UnknownBuild(Exception):
    pass


def make_engine(url: str):
    """The engine a notify run uses: every statement is bounded, so a stuck query cannot hang the run."""
    return create_engine(url, connect_args={"options": f"-c statement_timeout={STATEMENT_TIMEOUT_MS}"})


# ---------- value formatting (mirrors web/src/format.ts) ----------

def _plain(n: int | float, digits: int) -> str:
    if isinstance(n, float) and not math.isfinite(n):
        return str(n)
    with localcontext() as ctx:
        ctx.prec = 60
        d = Decimal(repr(n))
        if d == 0:
            return "0"
        d = d.quantize(Decimal(1).scaleb(d.adjusted() - digits + 1), rounding=ROUND_HALF_UP)
        text = format(d, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _signed(n, text: str) -> str:
    if n > 0:
        return f"+{text}"
    if n < 0:
        return MINUS + text.replace("-", "")
    return text


def _number_pair(before, after) -> tuple[str, str]:
    b, a = _plain(before, 6), _plain(after, 6)
    return (_plain(before, 17), _plain(after, 17)) if b == a and before != after else (b, a)


def _delta(before, after) -> str | None:
    d = float(f"{after - before:.12g}")
    text = _signed(d, _plain(d, 6))
    if before == 0:
        return f"({text})"
    p = d / abs(before) * 100
    rounded = round(p, 1) if abs(p) < 10 else math.floor(p + 0.5)
    if rounded == 0 and d != 0:
        pct = f"{'+' if p > 0 else MINUS}<0.1%"
    else:
        pct = _signed(rounded, str(rounded) if isinstance(rounded, int) else f"{rounded:g}") + "%"
    return f"({text}, {pct})"


def u16(text: str) -> int:
    """Length in UTF-16 code units, which is how Discord counts every embed limit (an emoji is 2)."""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def _cut(text: str, limit: int) -> str:
    """At most `limit` UTF-16 units, ending in "…" when cut; a surrogate pair is never split."""
    if u16(text) <= limit:
        return text
    used = 0
    for i, c in enumerate(text):
        used += 2 if ord(c) > 0xFFFF else 1
        if used > limit - 1:
            return text[:i] + "…"
    return text


# ---------- Discord Markdown safety ----------
# Game text is hostile input. Fragments go into the message either escaped (md) or inside a code span that
# cannot be closed (code); both are one line, and both are cut to length BEFORE they are wrapped.

_SPECIAL = re.compile(r"([\\*_~|>\[\]()#<`])")
_LIST_START = re.compile(r"^(\s*)(?:([-+])|(\d+)\.)")


ZWSP = "\N{ZERO WIDTH SPACE}"  # zero-width space: the one format character we add ourselves, after the game text is cleaned
_DOT = re.compile(r"(?<=\w)[.\N{IDEOGRAPHIC FULL STOP}\N{FULLWIDTH FULL STOP}\N{HALFWIDTH IDEOGRAPHIC FULL STOP}](?=\w)")  # ASCII, ideographic, fullwidth and halfwidth dots


def _oneline(text: str) -> str:
    """One line: line breaks and control characters become spaces; format characters (category Cf: bidi
    overrides and isolates, joiners, BOM, soft hyphen) are dropped."""
    out = []
    for c in text:
        category = unicodedata.category(c)
        if category in ("Cc", "Zl", "Zp"):
            out.append(" ")
        elif category != "Cf":
            out.append(c)
    return "".join(out)


def md(text: str, limit: int = 10_000) -> str:
    """Plain text that Discord will not render as Markdown: no emphasis, links, headings, spoilers or mentions
    syntax, and no clickable bare URL."""
    text = _SPECIAL.sub(r"\\\1", _cut(_oneline(text), limit))
    text = _LIST_START.sub(lambda m: m[1] + (f"\\{m[2]}" if m[2] else f"{m[3]}\\."), text)
    text = text.replace("://", ":\\/\\/")
    return _DOT.sub(lambda m: m[0] + ZWSP, text)  # evil.com, discord.gg/x: not a domain Discord will link


def code(text: str, limit: int = MAX_VALUE) -> str:
    """A code span: Markdown inside it is inert, and backticks are swapped out so it cannot be closed early."""
    return "`" + (_cut(_oneline(text), limit).replace("`", "ˋ") or " ") + "`"


def _value(v) -> str:
    try:
        text = json.dumps(v, separators=(",", ":"), ensure_ascii=False)
    except (ValueError, OverflowError):  # e.g. an integer too long to print
        text = "<unprintable>"
    return code(text)


def _plain_number(v) -> bool:
    return _is_number(v) and (isinstance(v, int) or math.isfinite(v)) and abs(v) < HUGE


def change_line(c) -> str:
    values = None
    if _plain_number(c.before) and _plain_number(c.after):
        try:
            b, a = _number_pair(c.before, c.after)
            values = f"{b} ▶ {a}  {_delta(c.before, c.after)}"
        except (ArithmeticError, ValueError):  # e.g. a difference too large for a float
            pass
    if values is None:
        values = f"{_value(c.before)} ▶ {_value(c.after)}"
    return f"{code(c.field_path, MAX_PATH)}  {values}"


def description(lines: list[str], total: int, link: str, limit: int = MAX_DESCRIPTION) -> str:
    """At most 10 lines, then "and N more" with the link; lines are dropped until it fits Discord's limit."""
    shown = lines[:MAX_LINES]
    while True:
        more = total - len(shown)
        text = "\n".join(shown + ([f"and {more} more: {link}"] if more > 0 else []))
        if u16(text) <= limit or not shown:
            return _cut(text, limit)
        shown.pop()


def _embed(title: str, url: str, lines: list[str], total: int, footer: str, fallback: str) -> dict:
    title, footer = _cut(title, MAX_TITLE), _cut(_oneline(footer), MAX_FOOTER)
    if len(url) > MAX_URL:  # a very long asset path: link to the build page instead
        url = fallback
    budget = min(MAX_DESCRIPTION, MAX_EMBED - u16(title) - u16(footer))
    return {
        "username": "Wardogs Patch Tracker",
        "allowed_mentions": {"parse": []},
        "embeds": [{
            "title": title,
            "url": url,
            "description": description(lines, total, url, budget),
            "footer": {"text": footer},
        }],
    }


# ---------- matching and message building ----------

def _q(text: str) -> str:
    return quote(text, safe="")


def _build_url(origin: str, b) -> str:
    return f"{origin}/builds/{_q(b.label)}"


def _asset_message(conn, sub, b, origin):
    row = conn.execute(
        select(asset.c.name, asset.c.path, asset_status.c.status)
        .select_from(asset_status.join(asset, asset.c.id == asset_status.c.asset_id))
        .where(asset_status.c.build_id == b.id, asset_status.c.asset_id == sub.asset_id)
    ).first()
    if row is None:
        return None
    where = (change.c.build_id == b.id, change.c.asset_id == sub.asset_id)
    total = conn.execute(select(func.count()).where(*where)).scalar_one()
    rows = conn.execute(select(change).where(*where).order_by(change.c.id).limit(MAX_LINES)).all()
    verb = "changed" if row.status == "modified" else row.status
    lines = [f"Status: {row.status}"] + [change_line(c) for c in rows]
    url = f"{origin}/asset?path={_q(row.path)}"
    return _embed(f"{md(row.name, MAX_NAME)} {verb} in {md(b.label, MAX_NAME)}", url, lines, total + 1,
                  f"Watching: asset {row.name}", _build_url(origin, b))


def _area_message(conn, sub, b, origin):
    where = (asset_status.c.build_id == b.id, asset_status.c.area == sub.target)
    joined = asset_status.join(asset, asset.c.id == asset_status.c.asset_id)
    total = conn.execute(select(func.count()).select_from(joined).where(*where)).scalar_one()
    if total == 0:
        return None
    # Same order as /api/builds/{label}/assets.
    rows = conn.execute(
        select(asset.c.name, asset_status.c.status).select_from(joined).where(*where)
        .order_by(STATUS_ORDER, func.lower(asset.c.name).collate("C"), asset.c.path.collate("C")).limit(MAX_LINES)
    ).all()
    area = conn.execute(select(build_area.c.title, build_area.c.counts)
                        .where(build_area.c.build_id == b.id, build_area.c.area == sub.target)).first()
    title = area.title if area else sub.target
    counts = ", ".join(f"{area.counts[k]} {k}" for k in ("added", "removed", "modified", "moved")
                       if area.counts.get(k)) if area else ""
    lines = ([counts] if counts else []) + [f"{r.status}  {md(r.name, MAX_NAME)}" for r in rows]
    url = f"{origin}/builds/{_q(b.label)}?area={_q(sub.target)}"
    return _embed(f"{md(title, MAX_NAME)} changed in {md(b.label, MAX_NAME)}", url, lines, total + bool(counts),
                  f"Watching: area {sub.target}", _build_url(origin, b))


def _field_message(conn, sub, b, origin):
    where = (change.c.build_id == b.id, _contains(change.c.field_path, sub.target))
    joined = change.join(build, build.c.id == change.c.build_id).join(asset, asset.c.id == change.c.asset_id)
    total = conn.execute(select(func.count()).select_from(joined).where(*where)).scalar_one()
    if total == 0:
        return None
    # Same order as /api/changes.
    rows = conn.execute(
        select(asset.c.name, change.c.field_path, change.c.before, change.c.after).select_from(joined).where(*where)
        .order_by(*NEWEST_FIRST, func.lower(asset.c.name).collate("C"), asset.c.path.collate("C"), change.c.id)
        .limit(MAX_LINES)
    ).all()
    lines = [f"**{md(r.name, MAX_NAME)}**  {change_line(r)}" for r in rows]
    url = f"{origin}/changes?field={_q(sub.target)}&build={_q(b.label)}"
    title = f"{total} change{'' if total == 1 else 's'} matching “{md(sub.target, MAX_NAME)}” in {md(b.label, MAX_NAME)}"
    return _embed(title, url, lines, total, f"Watching: field {sub.target}", _build_url(origin, b))


BUILDERS = {"asset": _asset_message, "area": _area_message, "field": _field_message}


# ---------- sending ----------

def _error(code: int, response: httpx.Response, webhook: str) -> str:
    return f"HTTP {code}: " + _cut(response.text.replace(webhook, "[webhook]"), MAX_ERROR)


def _retry_after(response: httpx.Response) -> float:
    try:
        value = float(response.json()["retry_after"])
    except (ValueError, KeyError, TypeError):
        value = 1.0
    return min(max(value, 0.0), RETRY_AFTER_CAP)


def send(client: httpx.Client, webhook: str, body: dict, sleep, clock=time.monotonic) -> tuple[str, int, str | None, bool]:
    """POST with the spec's retry rules inside a time budget of BUDGET seconds, waits included, of which at most
    MAX_RATE_SLEEP go to 429 waits. When the next wait would not fit, it stops: "time budget exhausted".
    Returns (status, tries, last_error, webhook_gone)."""
    deadline = clock() + BUDGET
    waited_429 = 0.0
    error = None
    for attempt in range(1, ATTEMPTS + 1):
        remaining = deadline - clock()
        if remaining <= 0:
            return "failed", attempt - 1, "time budget exhausted", False
        delay = BACKOFF[min(attempt - 1, len(BACKOFF) - 1)]
        rate_limited = False
        try:
            response = client.post(webhook, json=body, timeout=min(TIMEOUT, remaining))
        except httpx.HTTPError as e:
            error = f"network error: {type(e).__name__}"  # str(e) can carry the request URL
        else:
            code = response.status_code
            if 200 <= code < 300:
                return "sent", attempt, None, False
            error = _error(code, response, webhook)
            if code in GONE:
                return "failed", attempt, error, True
            if code == 429:
                delay, rate_limited = _retry_after(response), True
            elif code < 500:
                return "failed", attempt, error, False
        if attempt < ATTEMPTS:
            if clock() + delay >= deadline or (rate_limited and waited_429 + delay > MAX_RATE_SLEEP):
                return "failed", attempt, "time budget exhausted", False
            sleep(delay)
            waited_429 += delay if rate_limited else 0
    return "failed", ATTEMPTS, error, False


def _record(engine, sub_id: int, build_id: int, status: str, tries: int, error: str | None, gone: bool) -> None:
    """One transaction per delivery, so a crash later in the run keeps the record of what was sent."""
    with engine.begin() as conn:
        stmt = insert(delivery).values(subscription_id=sub_id, build_id=build_id, status=status, attempts=tries,
                                       last_error=error)
        conn.execute(stmt.on_conflict_do_update(
            index_elements=["subscription_id", "build_id"],
            set_={"status": status, "attempts": delivery.c.attempts + tries, "last_error": error,
                  "updated_at": func.now()}))
        if gone:
            conn.execute(update(subscription).where(subscription.c.id == sub_id, subscription.c.disabled_at.is_(None))
                         .values(disabled_at=func.now()))


def notify(engine, label: str, public_origin: str, client: httpx.Client | None = None, dry_run: bool = False,
           sleep=time.sleep, clock=time.monotonic) -> dict:
    """Send every due message for build `label`. Raises UnknownBuild if it is not ingested."""
    origin = public_origin.rstrip("/")
    with engine.connect() as conn:
        b = None if "\x00" in label else conn.execute(
            select(build.c.id, build.c.label).where(build.c.label == label, build.c.ingested_at.is_not(None))).first()
        if b is None:
            raise UnknownBuild(label)
        sent = select(delivery.c.subscription_id).where(
            delivery.c.subscription_id == subscription.c.id, delivery.c.build_id == b.id, delivery.c.status == "sent")
        subs = conn.execute(select(subscription).where(subscription.c.disabled_at.is_(None), ~sent.exists())
                            .order_by(subscription.c.id)).all()
        due, broken = [], []
        for sub in subs:
            try:
                with conn.begin_nested():  # a failed statement must not poison the other subscriptions
                    body = BUILDERS[sub.kind](conn, sub, b, origin)
            except Exception as e:
                log.error("subscription %s: building the message failed: %s", sub.id, type(e).__name__)
                broken.append((sub, f"internal error: {type(e).__name__}"))
                continue
            if body is not None:
                due.append((sub, body))

    summary = {"label": label, "matched": len(due), "sent": 0, "failed": 0, "disabled": 0, "messages": []}
    if dry_run:
        summary["messages"] = [{"subscription_id": s.id, "body": body} for s, body in due]
        return summary

    for sub, error in broken:
        _finish(engine, summary, sub.id, b.id, "failed", 0, error, False)

    own_client = client is None
    client = client or httpx.Client()
    try:
        for sub, body in due:
            try:
                if not WEBHOOK_RE.fullmatch(sub.webhook_url):  # the stored value is re-checked before it is used
                    status, tries, error, gone = "failed", 0, "invalid webhook", True
                else:
                    status, tries, error, gone = send(client, sub.webhook_url, body, sleep, clock)
            except Exception as e:
                log.error("subscription %s: sending failed: %s", sub.id, type(e).__name__)
                status, tries, error, gone = "failed", 0, f"internal error: {type(e).__name__}", False
            _finish(engine, summary, sub.id, b.id, status, tries, error, gone)
    finally:
        if own_client:
            client.close()
    return summary


def _finish(engine, summary: dict, sub_id: int, build_id: int, status: str, tries: int, error: str | None,
            gone: bool) -> None:
    try:
        _record(engine, sub_id, build_id, status, tries, error, gone)
    except Exception as e:
        log.error("subscription %s: recording the delivery failed: %s", sub_id, type(e).__name__)
        status, gone = "failed", False
    summary[status] += 1
    summary["disabled"] += gone
    log.info("subscription %s: %s after %s attempt(s)%s", sub_id, status, tries,
             " (webhook gone, disabled)" if gone else "")
